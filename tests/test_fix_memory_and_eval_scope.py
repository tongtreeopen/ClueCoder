import json
from typing import ClassVar

from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.extraction_queue import ExtractionQueue
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.memory import MemoryStore
from cluecoder.session_journal import SessionJournal
from cluecoder.tools.base import Tool
from cluecoder.tools.write import WriteFileTool
from cluecoder.workspace import WorkspacePolicy


class RecordingModel(ScriptedLLM):
    def __init__(self, responses):
        super().__init__([LLMResponse(content=json.dumps(response)) for response in responses])
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return super().chat(messages, **kwargs)


def test_long_sources_are_complete_and_correction_replaces_old_record_after_review(tmp_path):
    project, user = MemoryStore(tmp_path), MemoryStore(tmp_path, "user", tmp_path / "user/memory")
    user.save_candidate("language", "default reply language", "Use Chinese", "user")
    correction = {"name": "new-language", "description": "default reply language", "content": "Use English",
                  "type": "user", "source_event_ids": [1], "existing_name": "language", "relation": "update"}
    model = RecordingModel([[], [correction]])
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "probe")
    journal.append_message({"role": "user", "content": "ordinary log " * 1100 + "Use English"})
    queue = ExtractionQueue(journal.root, tmp_path, project, user, model)
    queue.enqueue(journal, 1, 1)
    assert queue.process_pending(limit=1) == 1
    assert len(model.prompts) == 2 and "Use English" in model.prompts[-1]
    assert all("Existing records:" in prompt and "language" in prompt for prompt in model.prompts)
    pending = queue.pending_reviews()
    assert len(pending) == 1 and pending[0]["name"] == "language"
    assert user.records()[0]["content"] == "Use Chinese"
    assert queue.accept(pending[0]["id"]).startswith("Saved")
    assert len(user.records()) == 1 and user.records()[0]["content"] == "Use English"
    assert queue.undo_last().startswith("Undid") and user.records()[0]["content"] == "Use Chinese"


def test_semantic_duplicate_with_different_name_does_not_save(tmp_path):
    project, user = MemoryStore(tmp_path), MemoryStore(tmp_path, "user", tmp_path / "user/memory")
    user.save_candidate("language", "default reply language", "Use Chinese", "user")
    model = RecordingModel([[{"name": "prefer-zh", "description": "same preference", "content": "Prefer Chinese answers",
                             "type": "user", "source_event_ids": [1], "existing_name": "language", "relation": "duplicate"}]])
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "probe")
    journal.append_message({"role": "user", "content": "Prefer Chinese answers"})
    queue = ExtractionQueue(journal.root, tmp_path, project, user, model)
    queue.enqueue(journal, 1, 1)
    assert queue.process_pending() == 1 and queue.pending_reviews() == []
    assert len(user.records()) == 1 and user.records()[0]["content"] == "Use Chinese"


def test_generated_label_punctuation_does_not_drop_supported_fact(tmp_path):
    project, user = MemoryStore(tmp_path), MemoryStore(tmp_path, "user", tmp_path / "user/memory")
    model = RecordingModel([[{"name": "Python 3.13", "description": "project version", "content": "Use Python 3.13",
                             "type": "project", "source_event_ids": [1], "existing_name": None, "relation": "new"}]])
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "probe")
    journal.append_message({"role": "user", "content": "Use Python 3.13"})
    queue = ExtractionQueue(journal.root, tmp_path, project, user, model)
    queue.enqueue(journal, 1, 1)
    assert queue.process_pending() == 1 and queue.pending_reviews() == []
    assert project.records()[0]["name"] == "Python 3 13" and project.records()[0]["source_event_ids"] == [1]


def test_eval_file_tools_refuse_forbidden_paths_but_allow_solution(tmp_path):
    tool = WriteFileTool(WorkspacePolicy(tmp_path))
    model = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("bad", "write_file", {"file_path": "test_solution.py", "content": "extra"})]),
        LLMResponse(tool_calls=[ToolCall("ok", "write_file", {"file_path": "solution.py", "content": "fixed"})]),
        LLMResponse(content="done"),
    ])
    agent = Agent(model, [tool])
    agent.configure_eval_scope(tmp_path, ["solution.py"])
    assert agent.chat("Modify only solution.py") == "done" and agent.last_turn_completed
    assert not (tmp_path / "test_solution.py").exists() and (tmp_path / "solution.py").read_text() == "fixed"
    assert any("outside the allowed" in m.get("content", "") for m in agent.messages if m["role"] == "tool")


def test_eval_final_patch_check_catches_shell_side_effect_and_requests_cleanup(tmp_path):
    class CleanupTool(Tool):
        name = "cleanup"
        description = "Remove the temporary test file"
        parameters: ClassVar = {"type": "object", "properties": {}}

        def execute(self):
            (tmp_path / "test_solution.py").unlink()
            return "removed"

    model = ScriptedLLM([
        LLMResponse(content="candidate"),
        LLMResponse(tool_calls=[ToolCall("cleanup", "cleanup", {})]),
        LLMResponse(content="done"),
    ])
    agent = Agent(model, [CleanupTool()])
    agent.configure_eval_scope(tmp_path, ["solution.py"])
    (tmp_path / "test_solution.py").write_text("shell-created file")
    assert agent.chat("finish") == "done" and agent.last_turn_completed
    assert not (tmp_path / "test_solution.py").exists()
