"""Evaluation mode only affects the isolated one-shot child process."""

import json
import sys

from tracecoder import cli as cli_module
from tracecoder import config as config_module
from tracecoder import session as session_module
from tracecoder.agent import Agent
from tracecoder.demo import ScriptedLLM
from tracecoder.llm import LLMResponse, ToolCall


def test_ordinary_config_still_loads_dotenv(tmp_path, monkeypatch):
    seen = []
    monkeypatch.delenv("TRACECODER_EVAL_MODE", raising=False)
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: seen.append(True))
    config_module.Config.from_env()
    assert seen == [True]


def test_eval_cli_skips_dotenv_and_memory_model_calls(tmp_path, monkeypatch):
    created = []

    class OfflineModel(ScriptedLLM):
        def __init__(self, model, api_key, base_url=None, **kwargs):
            created.append(self)
            super().__init__([LLMResponse(content="done", prompt_tokens=3, completion_tokens=2)], model=model)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACECODER_EVAL_MODE", "1")
    monkeypatch.setenv("TRACECODER_EVAL_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("TRACECODER_EVAL_MAX_CALLS", "50")
    monkeypatch.setenv("TRACECODER_EVAL_MAX_TOKENS", "30000")
    monkeypatch.setenv("TRACECODER_EVAL_TURN_PATH", str(tmp_path / "turn.json"))
    monkeypatch.setenv("TRACECODER_API_KEY", "offline-test-key")
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: (_ for _ in ()).throw(AssertionError("dotenv loaded")))
    monkeypatch.setattr(cli_module, "LLM", OfflineModel)
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sys, "argv", ["tracecoder", "--yes", "-p", "hello"])
    cli_module.main()
    assert len(created) == 1
    assert json.loads((tmp_path / "usage.json").read_text()) == {
        "calls": 1, "tokens": 5, "usage_unknown": False, "limit": None,
    }
    assert json.loads((tmp_path / "turn.json").read_text()) == {"completed": True, "reason": "completed"}


def test_eval_round_limit_records_incomplete_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACECODER_EVAL_MODE", "1")
    monkeypatch.setenv("TRACECODER_EVAL_TURN_PATH", str(tmp_path / "turn.json"))
    model = ScriptedLLM([LLMResponse(tool_calls=[ToolCall(str(i), "missing", {})]) for i in range(2)])
    cli_module._run_once(Agent(model, tools=[], max_rounds=2), "work")
    assert json.loads((tmp_path / "turn.json").read_text()) == {"completed": False, "reason": "round_limit"}


def test_eval_subagent_tool_trace_is_separate_from_parent_messages(tmp_path, monkeypatch):
    (tmp_path / "probe.txt").write_text("child-observation", encoding="utf-8")
    created = []

    class OfflineModel(ScriptedLLM):
        def __init__(self, model, api_key, base_url=None, **kwargs):
            created.append(self)
            self.available_tools = []
            super().__init__([
                LLMResponse(tool_calls=[ToolCall("parent", "agent", {"task": "read probe.txt"})],
                            prompt_tokens=1, completion_tokens=1),
                LLMResponse(tool_calls=[ToolCall("child", "read_file", {"file_path": "probe.txt"})],
                            prompt_tokens=1, completion_tokens=1),
                LLMResponse(content="child done", prompt_tokens=1, completion_tokens=1),
                LLMResponse(content="parent done", prompt_tokens=1, completion_tokens=1),
            ], model=model)

        def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
            self.available_tools.append({tool["function"]["name"] for tool in tools or []})
            return super().chat(messages, tools, on_token, max_output_tokens)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACECODER_EVAL_MODE", "1")
    monkeypatch.setenv("TRACECODER_EVAL_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("TRACECODER_EVAL_MAX_CALLS", "50")
    monkeypatch.setenv("TRACECODER_EVAL_MAX_TOKENS", "30000")
    monkeypatch.setenv("TRACECODER_API_KEY", "offline-test-key")
    monkeypatch.setattr(cli_module, "LLM", OfflineModel)
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sys, "argv", ["tracecoder", "--yes", "-p", "read probe.txt"])
    cli_module.main()
    events = [json.loads(line) for line in next((tmp_path / "sessions").glob("*/events.jsonl")).read_text().splitlines()]
    child = [event["data"] for event in events if event["kind"] == "note"
             and event["data"].get("type") == "subagent_message"]
    assert any(message["data"].get("tool_calls", [{}])[0].get("function", {}).get("name") == "read_file"
               for message in child if message["data"].get("tool_calls"))
    assert any("child-observation" in (message["data"].get("content") or "") for message in child)
    assert all(event["data"].get("message", {}).get("tool_call_id") != "child"
               for event in events if event["kind"] == "message")
    assert "agent" in created[0].available_tools[0]
    assert "agent" not in created[0].available_tools[1]
