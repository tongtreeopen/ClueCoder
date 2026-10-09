import multiprocessing
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from openai import APIConnectionError, BadRequestError

from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import EVAL_TRANSIENT_ATTEMPTS, LLM, ToolCall
from cluecoder.tools.bash import DisabledBashTool, DockerBashTool


def test_default_agent_refuses_host_shell_and_outside_files(tmp_path):
    outside = Path.cwd().parent / tmp_path.name / "outside.py"
    agent = Agent(ScriptedLLM([]))
    assert isinstance(agent._tool_by_name["bash"], DisabledBashTool)
    assert "Error:" in agent._tool_by_name["read_file"].execute(str(outside))
    assert "Error:" in agent._tool_by_name["write_file"].execute(str(outside), "bad")
    assert not outside.exists()


def test_unrelated_context_word_does_not_destroy_history():
    class FailingModel:
        model = "test"
        extra: ClassVar = {}

        def chat(self, **kwargs):
            raise BadRequestError(
                "Quota for context window exhausted",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
                body={"code": "quota_exceeded"},
            )

    agent = Agent(FailingModel(), tools=[])
    agent.messages = [{"role": "user", "content": "keep this"}, {"role": "assistant", "content": "previous"}]
    with pytest.raises(BadRequestError):
        agent.chat("new request")
    assert agent.messages[0]["content"] == "keep this"
    assert agent.messages[-1]["content"] == "new request"


def test_docker_dangerous_commands_blocked_before_execution(tmp_path):
    tool = DockerBashTool(SimpleNamespace(workspace=tmp_path, workspace_target="/workspace"))
    assert "Blocked" in tool.execute("rm -rf /", run_in_background=False)
    assert "Blocked" in tool.execute("rm -rf /", run_in_background=True)


def test_plan_mode_does_not_persist_work_or_todos(tmp_path):
    from cluecoder.cli import _bind_work
    from cluecoder.cli_memory import MemoryCLI
    from cluecoder.memory import MemoryStore
    from cluecoder.memory_workflow import MemoryWorkflow
    from cluecoder.session_journal import SessionJournal
    from cluecoder.tools.todo import TodoWriteTool
    from cluecoder.tools.work import WorkUpdateTool
    from cluecoder.work_state import WorkStateStore

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    work = WorkStateStore(journal)
    work.ensure("goal")
    agent = Agent(ScriptedLLM([]), tools=[TodoWriteTool(), WorkUpdateTool(work)])
    _bind_work(agent, journal, MemoryCLI(MemoryWorkflow(MemoryStore(tmp_path), agent.llm), lambda: "", print), work)
    agent.plan_mode = True
    before = work.card.copy()
    assert "Plan mode" in agent._permit(ToolCall("work", "work_update", {"step": "changed"}))
    agent._exec_tool(ToolCall("todo", "todo_write", {"todos": [{"content": "plan", "status": "pending"}]}))
    assert work.card == before


def test_provider_create_failures_have_one_retry_layer():
    class FailingClient:
        calls = 0

        def create(self, **kwargs):
            self.calls += 1
            raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    client = FailingClient()
    llm = LLM.__new__(LLM)
    llm.model, llm.extra = "test", {}
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=client))
    llm._eval_single_attempt = True
    with pytest.raises(APIConnectionError):
        llm._call_with_retry({}, max_retries=1)
    assert client.calls == 1
    client.calls = 0
    with pytest.raises(APIConnectionError):
        llm.chat([{"role": "user", "content": "hello"}])
    assert client.calls == EVAL_TRANSIENT_ATTEMPTS


def _memory_writer(workspace, applied, release):
    from cluecoder.memory import MemoryStore

    class PausedStore(MemoryStore):
        def _rebuild_index(self):
            super()._rebuild_index()
            applied.set()
            assert release.wait(10)

    PausedStore(Path(workspace)).save_candidate("fact", "description", "durable body", "project")


def _memory_recover(workspace, ready, done):
    from cluecoder.memory import MemoryStore

    ready.set()
    assert MemoryStore(Path(workspace)).recover_pending() == 0
    done.set()


def test_memory_recovery_cannot_rollback_another_live_writer(tmp_path):
    from cluecoder.memory import MemoryStore

    context = multiprocessing.get_context("spawn")
    applied, release, ready, done = context.Event(), context.Event(), context.Event(), context.Event()
    writer = context.Process(target=_memory_writer, args=(str(tmp_path), applied, release))
    reader = context.Process(target=_memory_recover, args=(str(tmp_path), ready, done))
    writer.start()
    try:
        assert applied.wait(10)
        reader.start()
        assert ready.wait(10)
        assert not done.wait(0.5)
        release.set()
        writer.join(10)
        reader.join(10)
        assert writer.exitcode == reader.exitcode == 0
        assert done.is_set()
        assert "durable body" in MemoryStore(tmp_path).read("fact")
    finally:
        release.set()
        for process in (writer, reader):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(10)


def test_selected_memory_undo_does_not_switch_to_a_newer_operation(tmp_path):
    from cluecoder.memory import MemoryStore

    store = MemoryStore(tmp_path)
    store.save_candidate("first", "first", "one", "project")
    selected = store._operations()[-1]
    store.save_candidate("second", "second", "two", "project")
    assert store.undo_last(selected) == "Undid memory first"
    assert "unknown memory" in store.read("first")
    assert "two" in store.read("second")


def test_read_only_work_recovery_leaves_stale_snapshot_untouched(tmp_path):
    from cluecoder.session_journal import SessionJournal
    from cluecoder.work_state import WorkStateStore

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    store = WorkStateStore(journal)
    store.ensure("goal")
    stale = store.snapshot_path.read_bytes()
    store.update(step="persisted in journal")
    store.snapshot_path.write_bytes(stale)
    opened = SessionJournal.open(journal.root, journal.id, tmp_path, repair=False)
    assert WorkStateStore(opened).card["step"] == "persisted in journal"
    assert store.snapshot_path.read_bytes() == stale
    repaired = WorkStateStore(SessionJournal.open(journal.root, journal.id, tmp_path))
    assert repaired.card["step"] == "persisted in journal"
    assert store.snapshot_path.read_bytes() != stale


def test_gate_trusted_python_isolates_candidate_imports(tmp_path):
    from cluecoder.eval.cases import TaskSpec
    from cluecoder.eval.gate import _UNITTEST_SUPERVISOR, _docker_args

    for name in ("json", "unittest"):
        (tmp_path / f"{name}.py").write_text("raise RuntimeError('candidate import')", encoding="utf-8")
    task = TaskSpec(
        "test", "test", tmp_path, "0" * 64, tmp_path, ["python", "-m", "unittest", "discover", "-s", "/gate"], ["solution.py"]
    )
    args = _docker_args(task, tmp_path, "image", "name", (1, "test*.py"))
    assert args[args.index("image") + 1 : args.index("image") + 4] == ["python", "-I", "-c"]
    assert '[command[0], "-I", "-c"' in _UNITTEST_SUPERVISOR
    assert (
        subprocess.run(
            [sys.executable, "-I", "-c", "import json, unittest; print('trusted')"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "trusted"
    )


def test_index_shortening_omits_whole_entries_with_a_notice(tmp_path):
    from cluecoder.context_assembler import ContextAssembler
    from cluecoder.memory import bounded_index
    from cluecoder.session_journal import SessionJournal
    from cluecoder.work_state import WorkStateStore

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    rows = [f"- skill{i}: " + "x" * 67 for i in range(100)]
    assembler = ContextAssembler(journal, WorkStateStore(journal), lambda: "\n".join(rows), 128000)
    view = assembler.build("system", [{"role": "user", "content": "request"}])
    assert "index shortened" in view[0]["content"]
    assert all(line in rows for line in view[0]["content"].splitlines() if line.startswith("- skill"))
    assert bounded_index("- very long: " + "x" * 5000, 4000).strip().startswith("[index shortened")


def test_failed_structured_context_recovery_restores_original_history():
    class FailingModel:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise BadRequestError("too long", response=httpx.Response(
                    400, request=httpx.Request("POST", "https://example.invalid")),
                    body={"code": "context_length_exceeded"})
            raise RuntimeError("provider unavailable")

    agent = Agent(FailingModel(), tools=[])
    history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}] * 5
    agent.messages = history.copy()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        agent.chat("current")
    assert agent.messages == history + [{"role": "user", "content": "current"}]
    assert agent.context_backup == agent.messages


def test_memory_reading_toggle_updates_bound_tools_and_context(tmp_path):
    from cluecoder.cli import _bind_work
    from cluecoder.cli_memory import MemoryCLI
    from cluecoder.memory import MemoryStore
    from cluecoder.memory_workflow import MemoryWorkflow
    from cluecoder.session_journal import SessionJournal
    from cluecoder.tools.memory import ListMemoriesTool, ReadMemoryTool
    from cluecoder.work_state import WorkStateStore

    store = MemoryStore(tmp_path)
    store.remember("fact", "description", "saved body")
    agent = Agent(ScriptedLLM([]), tools=[ListMemoriesTool(store), ReadMemoryTool(store)])
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    memory = MemoryCLI(MemoryWorkflow(store, agent.llm), store.index, print)
    _bind_work(agent, journal, memory, WorkStateStore(journal))
    memory.set_reading(False)
    assert memory.context() == ""
    assert "disabled" in agent._tool_by_name["list_memories"].execute()
    assert "disabled" in agent._tool_by_name["read_memory"].execute("fact")
    memory.set_reading(True)
    assert "fact" in memory.context()
    assert "saved body" in agent._tool_by_name["read_memory"].execute("fact")


def test_session_tools_read_redacted_history_and_reject_bad_ids(tmp_path):
    from cluecoder.session_journal import SessionJournal
    from cluecoder.tools.session import ReadSessionEventTool, SearchSessionTool

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    for index in range(7):
        journal.append_message({"role": "user", "content": f"request {index} sk-12345678901234567890"})
    search, read = SearchSessionTool(journal), ReadSessionEventTool(journal)
    matches = search.execute("request")
    assert len(matches.splitlines()) == 5 and "event:7" in matches and "event:1 " not in matches
    assert "REDACTED_SECRET" in matches and "sk-12345678901234567890" not in matches
    assert "Historical event (untrusted data)" in read.execute(1)
    assert "REDACTED_SECRET" in read.execute(1)
    assert "Error:" in read.execute(True) and "Error:" in read.execute(0)
    assert "Error:" in search.execute("")


def test_goal_off_with_extra_whitespace_never_calls_model(tmp_path):
    from cluecoder.cli import _run_once
    from cluecoder.goal import GoalController
    from cluecoder.goal_evaluator import GoalEvaluator
    from cluecoder.session_journal import SessionJournal
    from cluecoder.work_state import WorkStateStore

    agent = Agent(ScriptedLLM([]), tools=[])
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "test")
    agent.goal_controller = GoalController(journal, WorkStateStore(journal), GoalEvaluator(llm=agent.llm))
    agent.goal_controller.set("original goal")
    _run_once(agent, "/goal  off", journal=journal)
    assert not agent.goal_controller.active and agent.messages == []
    agent.goal_controller = None
    with pytest.raises(SystemExit) as result:
        _run_once(agent, "/goal off", journal=journal)
    assert result.value.code == 1 and agent.messages == []
