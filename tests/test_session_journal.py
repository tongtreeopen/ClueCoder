import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tracecoder import cli as cli_module
from tracecoder import session as session_module
from tracecoder.agent import Agent
from tracecoder.demo import ScriptedLLM
from tracecoder.llm import LLMResponse
from tracecoder.memory import MemoryStore
from tracecoder.session_journal import SessionJournal
from tracecoder.work_state import WorkStateStore


def test_journal_roundtrip_and_workspace_boundary(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    journal = SessionJournal.create(tmp_path / "sessions", workspace, "model-a")
    journal.append_message({"role": "user", "content": "修复这个问题"})
    journal.append_message({"role": "assistant", "content": "完成"})
    journal.set_context_budget(128000, 4096, "TRACECODER_MAX_CONTEXT")
    journal.set_context_budget(128000, 4096, "TRACECODER_MAX_CONTEXT")

    restored = SessionJournal.open(tmp_path / "sessions", journal.id, workspace)
    assert restored.messages() == [
        {"role": "user", "content": "修复这个问题"},
        {"role": "assistant", "content": "完成"},
    ]
    assert restored.model == "model-a"
    assert restored.meta["context_budget"]["max_context_tokens"] == 128000
    assert restored.meta["context_budget"]["max_context_tokens"] == 128000
    assert [event["seq"] for event in restored.events] == [1, 2]
    with pytest.raises(ValueError, match="workspace"):
        SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)

    moved = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path, allow_workspace_change=True)
    assert moved.workspace == str(tmp_path.resolve())
    assert SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).workspace == moved.workspace
    with pytest.raises(ValueError, match="workspace"):
        SessionJournal.open(tmp_path / "sessions", journal.id, workspace)


def test_parallel_subagent_events_remain_ordered_and_durable(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")

    def write(worker):
        for index in range(8):
            journal.append_event("note", {"type": "subagent_message", "child_id": str(worker), "index": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    restored = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    assert len(restored.events) == 64
    assert [event["seq"] for event in restored.events] == list(range(1, 65))


def test_journal_partial_tail_is_saved_and_recovered(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "user", "content": "first"})
    with journal.events_path.open("ab") as stream:
        stream.write(b'{"seq":2,"kind":"message"')

    restored = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    assert restored.messages() == [{"role": "user", "content": "first"}]
    assert restored.warnings
    assert list(restored.directory.glob("events.corrupt-*.bin"))
    restored.append_message({"role": "assistant", "content": "second"})
    assert SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).messages()[-1]["content"] == "second"


def test_journal_repairs_unanswered_tool_call_without_reexecuting(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "name": "bash"}]})

    restored = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    assert restored.messages()[-1] == {
        "role": "tool", "tool_call_id": "call-1",
        "content": "[result not persisted; side effect unknown; do not retry automatically]",
    }
    assert len(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).messages()) == 2


def test_journal_redacts_secret_and_archives_large_tool_output(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "user", "content": "key=sk-12345678901234567890"})
    journal.append_message({"role": "tool", "tool_call_id": "1", "content": "x" * 70000})

    events = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).events
    assert "sk-12345678901234567890" not in journal.events_path.read_text(encoding="utf-8")
    assert events[0]["redacted"]
    assert events[1]["data"]["message"]["archive_ref"].startswith("archives/")
    assert journal.read_archive(2, max_chars=10).startswith("x" * 10)
    assert not events[1]["truncated"]
    assert len(json.dumps(events[1]).encode()) < 65536


def test_archived_tool_output_is_redacted_bounded_and_checksum_checked(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "tool", "tool_call_id": "x",
                            "content": "x" * 31000 + " token=sk-12345678901234567890"})
    archive = journal.events[0]["data"]["message"]["archive_ref"]
    assert "sk-12345678901234567890" not in (journal.directory / archive).read_text(encoding="utf-8")
    assert journal.read_archive(1, max_chars=10).startswith("x" * 10)
    (journal.directory / archive).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        journal.read_archive(1)


def test_journal_rejects_tampered_complete_event(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "user", "content": "safe"})
    journal.events_path.write_text(
        journal.events_path.read_text(encoding="utf-8").replace("safe", "evil"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="checksum"):
        SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)


def test_model_view_cannot_mutate_journal_source(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_message({"role": "tool", "tool_call_id": "x", "content": "original"})
    view = journal.messages()
    view[0]["content"] = "compressed"
    assert journal.messages()[0]["content"] == "original"
    assert SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).messages()[0]["content"] == "original"


def test_compaction_records_source_range_without_erasing_tool_result(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    agent = Agent(ScriptedLLM([]), tools=[], max_context_tokens=1000, on_message=journal.append_message)
    agent.on_compaction = lambda detail: journal.append_event(
        "note", {"type": "model_view_compaction", "source_event_range": [1, len(journal.events)], **detail}
    )
    original = "\n".join(f"line {index} " + "x" * 200 for index in range(20))
    agent._append_message({"role": "tool", "tool_call_id": "x", "content": original})
    assert agent.compress_view()
    assert agent.messages[0]["content"] != original
    assert journal.messages()[0]["content"] == original
    assert journal.events[-1]["data"]["source_event_range"] == [1, 1]


def test_agent_persists_messages_before_return(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "scripted")
    agent = Agent(
        ScriptedLLM([LLMResponse(content="done", prompt_tokens=3, completion_tokens=2)]), tools=[],
        on_message=journal.append_message,
        on_usage=lambda detail: journal.append_event("note", {"type": "context_usage", **detail}),
    )

    assert agent.chat("hello") == "done"
    assert SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path).messages() == agent.messages
    usage = next(event for event in reversed(journal.events) if event["kind"] == "note")
    assert usage["data"]["prompt_tokens"] == 3


def test_failed_journal_write_prevents_next_model_request():
    llm = ScriptedLLM([LLMResponse(content="should not run")])

    def fail(_message):
        raise OSError("disk full")

    agent = Agent(llm, tools=[], on_message=fail)
    with pytest.raises(OSError, match="disk full"):
        agent.chat("hello")
    assert agent.messages == []
    assert len(llm._turns) == 1


def test_one_shot_cli_persists_work_card_and_answer(tmp_path, monkeypatch):
    class OfflineLLM(ScriptedLLM):
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            response = "answer" if self.instances == 1 else "[]"
            super().__init__([LLMResponse(content=response)], kwargs["model"])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", ["tracecoder", "-p", "Fix the tests"])
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli_module, "LLM", OfflineLLM)
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    cli_module.main()

    sessions = session_module.list_sessions()
    assert len(sessions) == 1
    journal = session_module.resume_journal(sessions[0]["id"], tmp_path)
    assert journal.messages()[0]["content"] == "Fix the tests"
    assert journal.messages()[-1]["content"] == "answer"
    assert WorkStateStore(journal).card["goal"] == "Fix the tests"


def test_one_shot_cli_autosaves_sourced_preference_without_prompt(tmp_path, monkeypatch):
    class OfflineLLM(ScriptedLLM):
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            response = ("answer" if self.instances == 1 else
                        '[{"name":"language","description":"response language","type":"user",'
                        '"content":"I prefer Chinese answers","source_event_ids":[1]}]')
            super().__init__([LLMResponse(content=response)], kwargs["model"])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", ["tracecoder", "-p", "I prefer Chinese answers"])
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli_module, "LLM", OfflineLLM)
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompted")))
    cli_module.main()

    assert MemoryStore(tmp_path, scope="user").records()[0]["content"] == "I prefer Chinese answers"


def test_one_shot_resume_restores_messages_and_work_card(tmp_path, monkeypatch):
    class OfflineLLM(ScriptedLLM):
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            response = "[]" if self.instances % 2 == 0 else f"answer {self.instances}"
            super().__init__([LLMResponse(content=response)], kwargs["model"])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli_module, "LLM", OfflineLLM)
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    monkeypatch.setattr(sys, "argv", ["tracecoder", "-p", "First request"])
    cli_module.main()
    session_id = session_module.list_sessions()[0]["id"]
    monkeypatch.setattr(sys, "argv", ["tracecoder", "--resume", session_id, "-p", "Continue"])
    cli_module.main()

    journal = session_module.resume_journal(session_id, tmp_path)
    assert [message["content"] for message in journal.messages() if message["role"] == "user"] == [
        "First request", "Continue",
    ]
    assert WorkStateStore(journal).card["goal"] == "First request"
    assert len(session_module.list_sessions()) == 1


def test_repl_reset_preserves_old_work_and_starts_empty_session(tmp_path, monkeypatch):
    from tracecoder.cli_memory import MemoryCLI
    from tracecoder.config import Config
    from tracecoder.memory_workflow import MemoryWorkflow
    from tracecoder.tools.todo import TodoWriteTool

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    journal = session_module.start_journal(tmp_path, "scripted")
    work = WorkStateStore(journal)
    work.ensure("Finish task")
    store = MemoryStore(tmp_path)
    memory = MemoryCLI(MemoryWorkflow(store, ScriptedLLM([])), store.catalog, notify=lambda message: None)
    todo = TodoWriteTool()
    agent = Agent(ScriptedLLM([]), tools=[todo], context_catalog=memory.context)
    cli_module._bind_work(agent, journal, memory, work)
    todo.execute([{"content": "Run tests", "status": "in_progress"}])
    answers = iter(["/reset", "quit"])
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(cli_module, "FileHistory", lambda path: None)
    cli_module._repl(agent, Config(), memory=memory, journal=journal)

    assert len(session_module.list_sessions()) == 2
    assert WorkStateStore(session_module.resume_journal(journal.id, tmp_path)).card["todos"][0]["content"] == "Run tests"
    assert agent.messages == []
    assert todo.render() == ""
