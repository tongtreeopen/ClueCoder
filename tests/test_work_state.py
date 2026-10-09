import json

from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.permissions import Permission
from cluecoder.session_journal import SessionJournal
from cluecoder.tools.todo import TodoWriteTool
from cluecoder.tools.work import WorkUpdateTool
from cluecoder.work_state import WorkStateStore


def test_work_card_restores_from_events_when_snapshot_is_stale(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Fix the failing tests")
    first = work.card.copy()
    work.update(step="Inspect the traceback", next_action="Run the focused test")
    work.snapshot_path.write_text(json.dumps(first), encoding="utf-8")

    restored = WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path))
    assert restored.card["revision"] == 2
    assert restored.card["next_action"] == "Run the focused test"
    assert json.loads(restored.snapshot_path.read_text(encoding="utf-8"))["revision"] == 2


def test_todo_done_and_no_criteria_do_not_complete_work(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Fix bug")
    work.set_todos([{"content": "Run tests", "status": "done"}])
    assert work.complete()["status"] == "awaiting_review"
    assert not work.card["archived"]
    assert work.confirm()["status"] == "completed"
    assert work.card["archived"]
    assert WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)).card["archived"]


def test_machine_check_requires_real_successful_bash_result(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Fix bug")
    fake = journal.append_message({"role": "assistant", "content": "Tests pass"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "event_id": fake}])
    assert work.complete()["status"] == "awaiting_review"

    journal.append_message({"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "bash", "arguments": '{"command":"pytest -q"}'}},
    ]})
    failed = journal.append_message({"role": "tool", "tool_call_id": "x", "content": "[exit code: 1]"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "command": "pytest -q", "event_id": failed}])
    assert work.complete()["status"] == "awaiting_review"

    failed_exception = journal.append_message({"role": "tool", "tool_call_id": "x",
                                               "content": "Error executing bash: container unavailable"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "command": "pytest -q",
                          "event_id": failed_exception}])
    assert work.complete()["status"] == "awaiting_review"

    interrupted = journal.append_message({"role": "tool", "tool_call_id": "x", "content": "[interrupted]"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "command": "pytest -q",
                          "event_id": interrupted}])
    assert work.complete()["status"] == "awaiting_review"

    journal.append_message({"role": "assistant", "content": None, "tool_calls": [
        {"id": "y", "type": "function", "function": {"name": "bash", "arguments": '{"command":"echo hello"}'}},
    ]})
    passed = journal.append_message({"role": "tool", "tool_call_id": "y", "content": "2 passed"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "command": "pytest -q", "event_id": passed}])
    assert work.complete()["status"] == "awaiting_review"

    journal.append_message({"role": "assistant", "content": None, "tool_calls": [
        {"id": "z", "type": "function", "function": {"name": "bash", "arguments": '{"command":"pytest -q"}'}},
    ]})
    passed = journal.append_message({"role": "tool", "tool_call_id": "z", "content": "2 passed"})
    work.update(criteria=[{"description": "tests pass", "kind": "check", "command": "pytest -q", "event_id": passed}])
    assert work.complete()["status"] == "completed"


def test_work_cannot_be_updated_after_completion_without_reopen(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Task")
    work.confirm()
    try:
        work.update(step="more")
    except ValueError as error:
        assert "reopen" in str(error)
    else:
        assert False, "completed work must be reopened first"
    assert work.reopen()["status"] == "active"


def test_work_snapshot_uses_redacted_journal_value(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Use sk-12345678901234567890 safely")
    assert "sk-12345678901234567890" not in work.snapshot_path.read_text(encoding="utf-8")


def test_todo_callback_persists_before_mutating_and_restores(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Task")
    todo = TodoWriteTool(on_change=work.set_todos)
    assert "updated" in todo.execute([{"content": "first", "status": "in_progress"}])
    restored = WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path))
    other = TodoWriteTool()
    other.restore(restored.card["todos"])
    assert "first" in other.render()

    def fail(_tasks):
        raise OSError("disk full")

    todo.on_change = fail
    try:
        todo.execute([{"content": "second", "status": "done"}])
    except OSError:
        pass
    assert "first" in todo.render()


def test_work_tool_cannot_self_certify_without_evidence(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Task")
    tool = WorkUpdateTool(work)
    assert "awaiting_review" in tool.execute(step="done", complete=True)


def test_plan_mode_refuses_persistent_session_work_updates(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Plan a change")
    agent = Agent(
        ScriptedLLM([
            LLMResponse(tool_calls=[ToolCall(id="update", name="work_update", arguments={"step": "Inspect code"})]),
            LLMResponse(content="Plan ready"),
        ]),
        tools=[WorkUpdateTool(work)], permission=Permission(),
    )
    agent.plan_mode = True
    assert agent.chat("Continue planning") == "Plan ready"
    assert work.card["step"] == ""
    assert "Plan mode" in agent.messages[2]["content"]
