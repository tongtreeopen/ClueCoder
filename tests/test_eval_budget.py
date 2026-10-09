"""Worker budget behavior through the same Agent interface used by the CLI."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.eval.budget import BudgetedModel, EvalLimitExceeded
from cluecoder.llm import EVAL_TRANSIENT_ATTEMPTS, LLM, LLMResponse, ToolCall
from cluecoder.tools.agent import AgentTool


def test_budget_counts_subagent_and_refuses_call_51(tmp_path):
    script = [
        LLMResponse(tool_calls=[ToolCall("a", "agent", {"task": "inspect"})], prompt_tokens=1, completion_tokens=1),
        LLMResponse(content="sub done", prompt_tokens=1, completion_tokens=1),
        LLMResponse(content="parent done", prompt_tokens=1, completion_tokens=1),
    ] + [LLMResponse(content="again", prompt_tokens=1, completion_tokens=1) for _ in range(47)]
    model = BudgetedModel(ScriptedLLM(script), tmp_path / "usage.json")
    assert Agent(model, tools=[AgentTool()]).chat("go") == "parent done"
    assert json.loads((tmp_path / "usage.json").read_text())["calls"] == 3
    calls = json.loads((tmp_path / "model_calls.json").read_text())["calls"]
    assert [call["actor"] for call in calls] == ["parent", calls[1]["actor"], "parent"]
    assert calls[1]["actor"].startswith("child:")
    assert [call["call_index"] for call in calls] == [1, 2, 3]
    assert [call["cumulative_tokens"] for call in calls] == [2, 4, 6]
    assert [call["cumulative_prompt_tokens"] for call in calls] == [1, 2, 3]
    assert [call["cumulative_completion_tokens"] for call in calls] == [1, 2, 3]
    for _ in range(47):
        model.chat([])
    with pytest.raises(EvalLimitExceeded):
        model.chat([])
    assert json.loads((tmp_path / "usage.json").read_text())["calls"] == 50
    trace = json.loads((tmp_path / "model_calls.json").read_text())
    assert len(trace["calls"]) == 50 and trace["limit"] == "calls" and trace["rejected_attempts"] == 1


def test_budget_refuses_after_100000_tokens(tmp_path):
    model = BudgetedModel(ScriptedLLM([
        LLMResponse(content="done", prompt_tokens=20_000, completion_tokens=10_000),
        LLMResponse(content="done", prompt_tokens=60_000, completion_tokens=10_000),
    ]), tmp_path / "usage.json")
    model.chat([])
    model.chat([])
    with pytest.raises(EvalLimitExceeded):
        model.chat([])
    assert json.loads((tmp_path / "usage.json").read_text()) == {
        "calls": 2, "tokens": 100_000, "usage_unknown": False, "limit": "tokens",
    }
    trace = json.loads((tmp_path / "model_calls.json").read_text())
    assert len(trace["calls"]) == 2 and trace["limit"] == "tokens"
    assert trace["calls"][0]["prompt_tokens"] == 20_000
    assert trace["calls"][0]["completion_tokens"] == 10_000


@pytest.mark.parametrize(("prompt_tokens", "completion_tokens"), [(0, 0), (None, None)])
def test_missing_usage_is_unknown(tmp_path, prompt_tokens, completion_tokens):
    model = BudgetedModel(ScriptedLLM([
        LLMResponse(content="done", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    ]), tmp_path / "usage.json")
    model.chat([])
    assert json.loads((tmp_path / "usage.json").read_text()) == {
        "calls": 1, "tokens": 0, "usage_unknown": True, "limit": None,
    }
    call = json.loads((tmp_path / "model_calls.json").read_text())["calls"][0]
    assert call["usage_status"] == ("zero" if prompt_tokens == 0 else "missing")
    assert call["cumulative_usage_unknown"] is True


def test_inflight_call_is_durably_unknown_until_usage_arrives(tmp_path):
    usage_path = tmp_path / "usage.json"

    class InspectingModel(ScriptedLLM):
        def chat(self, *args, **kwargs):
            assert json.loads(usage_path.read_text()) == {
                "calls": 1, "tokens": 0, "usage_unknown": True, "limit": None,
            }
            assert json.loads((tmp_path / "model_calls.json").read_text())["calls"][0]["outcome"] == "in_flight"
            return super().chat(*args, **kwargs)

    model = BudgetedModel(InspectingModel([
        LLMResponse(content="done", prompt_tokens=2, completion_tokens=1),
    ]), usage_path)
    model.chat([])
    assert json.loads(usage_path.read_text())["usage_unknown"] is False
    assert json.loads((tmp_path / "model_calls.json").read_text())["calls"][0]["outcome"] == "completed"


def test_interrupted_provider_call_retains_inflight_usage(tmp_path):
    class InterruptedModel(ScriptedLLM):
        def chat(self, *args, **kwargs):
            raise KeyboardInterrupt

    model = BudgetedModel(InterruptedModel([]), tmp_path / "usage.json")
    with pytest.raises(KeyboardInterrupt):
        model.chat([])
    trace = json.loads((tmp_path / "model_calls.json").read_text())
    assert trace["calls"] == [{
        "call_index": 1, "actor": "parent", "outcome": "in_flight",
        "prompt_tokens": None, "completion_tokens": None, "usage_status": "missing",
        "cumulative_tokens": 0, "cumulative_prompt_tokens": 0,
        "cumulative_completion_tokens": 0, "cumulative_usage_unknown": True,
    }]
    assert json.loads((tmp_path / "usage.json").read_text())["usage_unknown"] is True


@contextmanager
def local_provider(replies):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.server.requests += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            status, usage = replies.pop(0)
            if status == 200:
                body = (
                    'data: {"id":"test","object":"chat.completion.chunk","created":1,'
                    '"model":"test","choices":[{"index":0,"delta":{"content":"ok"},'
                    '"finish_reason":null}]}\n\n'
                    + 'data: {"id":"test","object":"chat.completion.chunk","created":1,'
                    '"model":"test","choices":[],"usage":' + json.dumps(usage) + '}\n\n'
                    + 'data: [DONE]\n\n'
                ).encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps({"error": {
                    "message": usage if isinstance(usage, str) else "temporary provider failure",
                    "type": "invalid_request_error" if status == 400 else "server_error",
                }}).encode()
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = 0
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_eval_counts_one_call_even_when_the_provider_keeps_failing(tmp_path):
    # Retrying a 5xx is not a new logical call: the budget and the trace stay at
    # one entry, while the provider sees the bounded number of attempts.
    errors = [(500, None)] * 6
    with local_provider(errors + [(200, {"prompt_tokens": 2, "completion_tokens": 1})]) as (url, server):
        model = BudgetedModel(LLM("test", "offline-key", base_url=url), tmp_path / "usage.json", max_calls=1)
        with pytest.raises(Exception, match="temporary provider failure"):
            model.chat([{"role": "user", "content": "hello"}])
        assert server.requests == EVAL_TRANSIENT_ATTEMPTS, "expected one logical call to make bounded attempts"
        assert json.loads((tmp_path / "usage.json").read_text()) == {
            "calls": 1, "tokens": 0, "usage_unknown": True, "limit": None,
        }
        trace = json.loads((tmp_path / "model_calls.json").read_text())
        assert len(trace["calls"]) == 1
        assert trace["calls"][0]["outcome"] == "provider_error"
        assert trace["calls"][0]["exception_type"] and "temporary provider failure" not in json.dumps(trace)
        with pytest.raises(EvalLimitExceeded):
            model.chat([{"role": "user", "content": "again"}])
        # The exhausted budget is rejected before the provider, so no new request.
        assert server.requests == EVAL_TRANSIENT_ATTEMPTS


def test_eval_disables_parameter_fallback_after_one_request(tmp_path):
    with local_provider([
        (400, "Unsupported parameter: 'max_tokens'"),
        (200, {"prompt_tokens": 2, "completion_tokens": 1}),
    ]) as (url, server):
        model = BudgetedModel(LLM("test", "offline-key", base_url=url, max_tokens=10),
                              tmp_path / "usage.json", max_calls=1)
        with pytest.raises(Exception, match="Unsupported parameter"):
            model.chat([{"role": "user", "content": "hello"}])
        assert server.requests == 1
        assert json.loads((tmp_path / "usage.json").read_text())["usage_unknown"] is True


@pytest.mark.parametrize(("usage", "known_tokens"), [
    ({"prompt_tokens": 3, "completion_tokens": None}, 3),
    ({"completion_tokens": 4}, 4),
    ({"prompt_tokens": None, "completion_tokens": 2}, 2),
    ({"prompt_tokens": 5}, 5),
])
def test_partial_provider_usage_stays_unknown(tmp_path, usage, known_tokens):
    with local_provider([(200, usage)]) as (url, server):
        model = BudgetedModel(LLM("test", "offline-key", base_url=url), tmp_path / "usage.json")
        model.chat([{"role": "user", "content": "hello"}])
        assert server.requests == 1
        record = json.loads((tmp_path / "usage.json").read_text())
        assert record["usage_unknown"] is True
        assert record["tokens"] == known_tokens
        call = json.loads((tmp_path / "model_calls.json").read_text())["calls"][0]
        assert call["usage_status"] == "partial" and call["cumulative_tokens"] == known_tokens
        assert min((call["prompt_tokens"], call["completion_tokens"]), key=lambda x: x is None) == known_tokens
