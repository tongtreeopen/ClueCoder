import importlib
import json
import sys

import pytest

from cluecoder import Agent
from cluecoder import session as session_module
from cluecoder.cli_memory import MemoryCLI
from cluecoder.config import Config
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse
from cluecoder.memory import MemoryStore
from cluecoder.memory_workflow import MemoryWorkflow

cli_module = importlib.import_module("cluecoder.cli")


def test_review_mode_requires_answer_and_auto_mode_is_explicit(tmp_path):
    store = MemoryStore(tmp_path)
    extracted = '[{"name":"style","description":"format","type":"user","content":"Use Chinese"}]'
    notices = []
    workflow = MemoryWorkflow(store, ScriptedLLM([LLMResponse(content=extracted)]))
    controller = MemoryCLI(workflow, store.catalog, notify=notices.append, ask=lambda candidate: False)
    agent = Agent(ScriptedLLM([LLMResponse(content="answer")]), tools=[], context_catalog=controller.context)
    controller.begin_turn("question")
    assert agent.chat("question") == "answer"
    controller.after_turn(agent, "question", interactive=True)
    assert store.list() == "(no memories found)"
    assert any("Use Chinese" in notice for notice in notices)

    workflow = MemoryWorkflow(store, ScriptedLLM([LLMResponse(content=extracted)]))
    controller = MemoryCLI(workflow, store.catalog, notify=notices.append)
    agent = Agent(ScriptedLLM([LLMResponse(content="answer")]), tools=[], context_catalog=controller.context)
    controller.begin_turn("question")
    agent.chat("question")
    controller.after_turn(agent, "question", interactive=False)
    assert store.list() == "(no memories found)"
    assert controller.set_auto(True) == "Memory auto-save on for this CLI process"

    workflow = MemoryWorkflow(store, ScriptedLLM([LLMResponse(content=extracted)]))
    controller.workflow = workflow
    agent.chat = lambda prompt: "answer"
    agent.last_turn_completed = True
    controller.after_turn(agent, "question", interactive=False)
    assert "Use Chinese" in store.read("style")
    journal = json.loads(next(store.directory.glob("op_*.json")).read_text(encoding="utf-8"))
    assert (journal["source"], journal["mode"]) == ("cli_turn", "auto")
    assert controller.undo(agent) == "Undid memory style"


def test_plan_mode_blocks_memory_writes_and_undo(tmp_path):
    store = MemoryStore(tmp_path)
    store.save_candidate("style", "format", "Use Chinese", "user")
    notices = []
    controller = MemoryCLI(MemoryWorkflow(store, ScriptedLLM([])), store.catalog, notify=notices.append)
    controller.set_auto(True)
    agent = Agent(ScriptedLLM([LLMResponse(content="plan")]), tools=[], context_catalog=controller.context)
    agent.plan_mode = True
    controller.begin_turn("question")
    agent.chat("question")
    controller.after_turn(agent, "question", interactive=True)
    assert controller.undo(agent).startswith("Error: plan mode")
    assert "Use Chinese" in store.read("style")


def test_auto_mode_never_silently_replaces_existing_memory(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "old", "Old style")
    selection = LLMResponse(content="[]")
    update = LLMResponse(content='[{"name":"style","description":"new","type":"feedback","content":"New style"}]')
    notices = []
    controller = MemoryCLI(MemoryWorkflow(store, ScriptedLLM([selection, update])), store.catalog, notify=notices.append)
    controller.set_auto(True)
    agent = Agent(ScriptedLLM([LLMResponse(content="answer")]), tools=[], context_catalog=controller.context)
    controller.begin_turn("question")
    agent.chat("question")
    controller.after_turn(agent, "question", interactive=False)
    assert "Old style" in store.read("style")
    assert any("confirmation" in notice for notice in notices)


def test_auto_mode_still_needs_separate_merge_approval(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(10):
        store.remember(f"item{index}", f"fact {index}", f"Body {index}")
    merge = LLMResponse(content='[{"name":"merged","description":"summary","type":"project","content":"Combined facts"}]')
    notices = []
    controller = MemoryCLI(
        MemoryWorkflow(store, ScriptedLLM([merge])), store.catalog,
        notify=notices.append, ask_merge=lambda: False,
    )
    controller.set_auto(True)
    agent = Agent(ScriptedLLM([]), tools=[])
    controller.review_consolidation(agent, interactive=True, explicit=True)
    assert len(store.records()) == 10
    assert any("confirmation required" in notice for notice in notices)
    assert any("DELETE item0" in notice for notice in notices)

    controller.workflow = MemoryWorkflow(store, ScriptedLLM([merge]))
    controller.ask_merge = lambda: True
    controller.review_consolidation(agent, interactive=True, explicit=True)
    assert len(store.records()) == 1
    assert controller.undo(agent) == "Undid consolidation"
    assert len(store.records()) == 10


@pytest.mark.parametrize(("args", "expected_auto"), [
    (["-p", "hello"], True),
    (["-p", "hello", "--yes"], True),
    (["-p", "hello", "--memory-auto"], True),
    (["--resume", "saved", "-p", "hello"], True),
])
def test_main_safe_memory_auto_is_default_across_one_shot_modes(tmp_path, monkeypatch, args, expected_auto):
    seen = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", ["cluecoder", *args])
    monkeypatch.setattr(cli_module, "_run_once", lambda agent, prompt, memory, journal: seen.append(memory.auto))
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    if "--resume" in args:
        session_module.save_session([], "test-model", "saved")
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    cli_module.main()
    assert seen == [expected_auto]


def test_memory_auto_flag_requires_one_shot_prompt(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cluecoder", "--memory-auto"])
    with pytest.raises(SystemExit) as error:
        cli_module._parse_args()
    assert error.value.code == 2


def test_real_one_shot_runner_shows_candidate_without_saving(tmp_path, capsys):
    store = MemoryStore(tmp_path)
    notices = []
    candidate = '[{"name":"style","description":"format","type":"user","content":"Use Chinese"}]'
    controller = MemoryCLI(
        MemoryWorkflow(store, ScriptedLLM([LLMResponse(content=candidate)])),
        store.catalog, notify=notices.append,
    )
    agent = Agent(ScriptedLLM([LLMResponse(content="answer")]), tools=[], context_catalog=controller.context)
    cli_module._run_once(agent, "question", controller)
    assert "answer" in capsys.readouterr().out
    assert any("Use Chinese" in notice for notice in notices)
    assert store.list() == "(no memories found)"


def test_repl_memory_mode_survives_reset_and_undo_uses_memory_stack(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    store.save_candidate("style", "format", "Use Chinese", "user")
    controller = MemoryCLI(MemoryWorkflow(store, ScriptedLLM([])), store.catalog, notify=lambda message: None)
    agent = Agent(ScriptedLLM([]), tools=[], context_catalog=controller.context)
    answers = iter(["/memory auto on", "/reset", "/memory undo", "quit"])
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(cli_module, "FileHistory", lambda path: None)
    cli_module._repl(agent, Config(), memory=controller)
    assert controller.auto is True
    assert store.read("style").startswith("Error")


def test_repl_memory_commands_handle_unavailable_review_queue(tmp_path, monkeypatch, capsys):
    store = MemoryStore(tmp_path)
    controller = MemoryCLI(MemoryWorkflow(store, ScriptedLLM([])), store.catalog, notify=lambda message: None)
    agent = Agent(ScriptedLLM([]), tools=[], context_catalog=controller.context)
    answers = iter(["/memory review unknown", "/memory accept bad extra", "/memory process", "quit"])
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(cli_module, "FileHistory", lambda path: None)
    cli_module._repl(agent, Config(), memory=controller)
    output = capsys.readouterr().out
    assert "Unknown pending memory review" in output
    assert "Memory review queue unavailable" in output
    assert "Memory extraction queue unavailable" in output


def test_memory_command_controller_preserves_queue_guards_and_accepts_review(tmp_path):
    from tests.test_extraction_queue import _queue

    candidate = ('[{"name":"language","description":"response language","type":"user",'
                 '"content":"I prefer English answers","source_event_ids":[1]}]')
    journal, project, user, queue = _queue(tmp_path, [candidate])
    user.save_candidate("language", "response language", "I prefer Chinese answers", "user")
    event_id = journal.append_message({"role": "user", "content": "I prefer English answers"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    review_id = queue.pending_reviews()[0]["id"]
    notices = []
    controller = MemoryCLI(queue.workflow, project.catalog, notify=notices.append, queue=queue)
    agent = Agent(ScriptedLLM([]), tools=[])
    reading = []
    controller.on_reading_change = reading.append
    controller.handle_command("/memory reading off", agent, notices.append)
    controller.handle_command("/memory auto off", agent, notices.append)
    controller.handle_command("/memory extraction off", agent, notices.append)
    assert reading == [False] and not controller.reading_enabled and not queue.enabled and not queue.auto
    agent.plan_mode = True
    for command in ("/memory accept " + review_id, "/memory reject " + review_id, "/memory process", "/memory undo"):
        controller.handle_command(command, agent, notices.append)
    assert all("plan mode" in notice for notice in notices[-4:])
    assert len(queue.pending_reviews()) == 1 and user.records()[0]["content"] == "I prefer Chinese answers"
    agent.plan_mode = False
    controller.handle_command("/memory review " + review_id, agent, notices.append)
    assert notices[-1]["id"] == review_id
    controller.handle_command("/memory accept " + review_id, agent, notices.append)
    assert notices[-1].startswith("Saved") and user.records()[0]["content"] == "I prefer English answers"
    controller.handle_command("/memory undo", agent, notices.append)
    assert notices[-1].startswith("Undid") and user.records()[0]["content"] == "I prefer Chinese answers"
