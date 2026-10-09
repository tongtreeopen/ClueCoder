"""Compression keeps a valid tool-call transcript and recent context."""

import subprocess
from types import SimpleNamespace

import httpx
from openai import BadRequestError

from cluecoder.agent import Agent
from cluecoder.context import ContextManager
from cluecoder.context_assembler import ContextAssembler
from cluecoder.llm import LLMResponse
from cluecoder.session_journal import SessionJournal
from cluecoder.work_state import WorkStateStore


def _conversation():
    messages = []
    for index in range(8):
        messages.extend([
            {"role": "user", "content": f"request {index}"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{index}"}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": f"result {index}"},
        ])
    return messages


def _assert_paired(messages):
    seen = set()
    for message in messages:
        if message.get("tool_calls"):
            seen.update(call["id"] for call in message["tool_calls"])
        if message["role"] == "tool":
            assert message["tool_call_id"] in seen


def test_summarize_and_emergency_collapse_keep_recent_tool_pairs():
    context = ContextManager(max_tokens=1000)
    messages = _conversation()
    assert context._summarize_old(messages, None, keep_recent=5)
    _assert_paired(messages)
    assert messages[-1]["content"] == "result 7"
    assert any("request 7" in (message.get("content") or "") for message in messages)
    context._hard_collapse(messages, None)
    _assert_paired(messages)
    assert messages[-1]["content"] == "result 7"


def test_failed_summary_falls_back_without_orphaning_tool_results():
    class FailingLLM:
        def chat(self, messages):
            raise RuntimeError("offline failure")

    context = ContextManager(max_tokens=1000)
    messages = _conversation()
    assert context._summarize_old(messages, FailingLLM(), keep_recent=4)
    assert messages[0]["content"].startswith("[Context compressed")
    _assert_paired(messages)


def test_prompt_too_long_retries_once_with_emergency_view(tmp_path):
    class Provider:
        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None, on_token=None):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise BadRequestError("maximum context length exceeded", response=httpx.Response(
                    400, request=httpx.Request("POST", "https://example.invalid")), body={"code": "context_length_exceeded"})
            return LLMResponse(content="done")

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    llm = Provider()
    agent = Agent(llm, tools=[], max_context_tokens=10000, on_message=journal.append_message)
    agent.context_assembler = ContextAssembler(journal, work, lambda: "", max_tokens=10000)
    agent.messages = [{"role": "user", "content": "old request"},
                      {"role": "assistant", "content": "old answer"}] * 4

    assert agent.chat("current request") == "done"
    assert len(llm.calls) == 2
    assert "current request" in str(llm.calls[1])
    assert "old request" not in str(llm.calls[1])


def test_oversized_current_request_is_rejected_before_provider_call(tmp_path):
    class Provider:
        calls = 0

        def chat(self, messages, tools=None, on_token=None):
            self.calls += 1
            return LLMResponse(content="should not run")

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    llm = Provider()
    agent = Agent(llm, tools=[], max_context_tokens=1200, on_message=journal.append_message)
    agent.context_assembler = ContextAssembler(journal, work, lambda: "", max_tokens=1200)
    request = "界" * 5000
    try:
        agent.chat(request)
    except ValueError as error:
        assert "Context budget exceeded" in str(error)
    else:
        assert False, "oversized current request should fail before the provider call"
    assert llm.calls == 0
    assert agent.messages == [{"role": "user", "content": request}]


def test_cumulative_budget_prompts_are_eval_only_and_one_time(tmp_path, monkeypatch):
    class Budget:
        usage_unknown = False
        tokens = 60
        max_tokens = 100

    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    journal = SessionJournal.create(tmp_path / "sessions", workspace, "m")
    agent = Agent(Budget(), tools=[])
    agent.work_state = SimpleNamespace(journal=journal)
    monkeypatch.setenv("CLUECODER_EVAL_MODE", "1")
    assert "60%" in agent._budget_checkpoint()
    assert agent._budget_checkpoint() == ""

    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_fix.py").write_text("print('changed')", encoding="utf-8")
    agent.llm.tokens = 80
    assert "80%" in agent._budget_checkpoint()
    monkeypatch.delenv("CLUECODER_EVAL_MODE")
    agent.llm.tokens = 90
    assert agent._budget_checkpoint() == ""
