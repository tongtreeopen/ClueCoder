import json

import pytest

from tracecoder.agent import Agent
from tracecoder.demo import ScriptedLLM
from tracecoder.eval.budget import BudgetedModel
from tracecoder.llm import LLMResponse, ToolCall
from tracecoder.session_journal import SessionJournal
from tracecoder.tools.agent import AgentTool
from tracecoder.tools.read import ReadFileTool
from tracecoder.work_state import WorkStateStore
from tracecoder.workspace import WorkspacePolicy


def report(**changes):
    return json.dumps({"status": "completed", "summary": "Read the file", "findings": [],
                       "evidence_refs": [], "blockers": [], **changes})


class RecordingModel(ScriptedLLM):
    def __init__(self, script):
        super().__init__(script)
        self.requests = []

    def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
        self.requests.append({"messages": messages, "tools": tools, "max_output_tokens": max_output_tokens})
        return super().chat(messages, tools, on_token, max_output_tokens)


def test_structured_result_preserves_long_json_and_text_default():
    report = {"status": "completed", "summary": "x" * 6000,
              "findings": ["A finding"], "evidence_refs": [], "blockers": []}
    tool = AgentTool()
    Agent(ScriptedLLM([LLMResponse(content=json.dumps(report)), LLMResponse(content="plain")]), tools=[tool])
    result = json.loads(tool.execute(task="review", output_format="structured"))
    assert all(result[key] == value for key, value in report.items())
    assert result["child_id"] and tool.execute(task="review") == "[Sub-agent completed]\nplain"


def test_real_tool_sources_and_child_trace_stay_separate(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("actual observation", encoding="utf-8")
    events = []
    parent = Agent(RecordingModel([
        LLMResponse(tool_calls=[ToolCall("delegate", "agent", {"task": "read", "output_format": "structured"})]),
        LLMResponse(tool_calls=[ToolCall("read-1", "read_file", {"file_path": str(path)})]),
        LLMResponse(content=report(evidence_refs=["read-1"])),
        LLMResponse(content="parent done"),
    ]), tools=[AgentTool(), ReadFileTool(WorkspacePolicy(tmp_path))],
        on_subagent_event=lambda child_id, kind, data: events.append((child_id, kind, data)))
    assert parent.chat("review") == "parent done"
    result = json.loads(next(message["content"] for message in parent.messages if message["role"] == "tool"))
    assert result["evidence_refs"] == ["read-1"] and result["status"] == "completed"
    assert any(child == result["child_id"] and kind == "message" and data.get("tool_call_id") == "read-1"
               and "actual observation" in data["content"] for child, kind, data in events)
    assert not any(message.get("tool_call_id") == "read-1" for message in parent.messages)


@pytest.mark.parametrize(("invalid",), [
    ("```json\n" + report() + "\n```",),
    (report(evidence_refs=["invented"]),),
    (report(findings="not a list"),),
    (report(status="completed", blockers=["Cannot finish"]),),
    (report(status="blocked"),),
    (report(extra="unknown"),),
    ("[]",),
    (report(summary=None),),
    (report(blockers=[False]),),
    ('{"status":"blocked",' + report()[1:],),
    (report(summary="x" * 17000),),
])
def test_one_tool_free_correction_counts_shared_budget(tmp_path, invalid):
    provider = RecordingModel([LLMResponse(content=invalid, prompt_tokens=5, completion_tokens=2),
                               LLMResponse(content=report(status="blocked", blockers=["Need input"]),
                                           prompt_tokens=7, completion_tokens=3)])
    model = BudgetedModel(provider, tmp_path / "usage.json")
    tool = AgentTool()
    Agent(model, tools=[tool, ReadFileTool()])
    result = json.loads(tool.execute("read", output_format="structured"))
    assert result["status"] == "blocked" and result["blockers"] == ["Need input"]
    assert len(provider.requests) == 2 and provider.requests[-1]["tools"] is None
    assert provider.requests[-1]["max_output_tokens"] == 4096
    assert model.calls == 2 and model.tokens == 17
    calls = json.loads((tmp_path / "model_calls.json").read_text())["calls"]
    assert {call["actor"] for call in calls} == {"child:" + result["child_id"]}


@pytest.mark.parametrize(("correction",), [
    (LLMResponse(content="still not JSON"),),
    (LLMResponse(content=report(), finish_reason="length"),),
    (LLMResponse(content=report(evidence_refs=["fabricated"])),),
    (LLMResponse(content=report(), tool_calls=[ToolCall("write", "write_file", {})]),),
])
def test_invalid_correction_never_completes_or_executes_tools(correction):
    model = RecordingModel([LLMResponse(content="invalid"), correction])
    events = []
    tool = AgentTool()
    Agent(model, tools=[tool], on_subagent_event=lambda child, kind, data: events.append((kind, data)))
    result = json.loads(tool.execute("review", output_format="structured"))
    assert result["status"] == "error" and result["blockers"] and result["evidence_refs"] == []
    assert len(model.requests) == 2 and model.requests[-1]["tools"] is None
    assert not any(kind == "message" and data.get("role") == "tool" for kind, data in events)
    assert not any(kind == "message" and data.get("tool_calls") for kind, data in events)
    assert events[-1] == ("finish", {"completed": False, "status": "error"})


@pytest.mark.parametrize(("limit",), [("calls",), ("tokens",)])
def test_correction_budget_exhaustion_returns_limit(tmp_path, limit):
    provider = RecordingModel([LLMResponse(content="invalid", prompt_tokens=5, completion_tokens=2)])
    model = BudgetedModel(provider, tmp_path / "usage.json", max_calls=1 if limit == "calls" else 5,
                          max_tokens=7 if limit == "tokens" else 100)
    tool = AgentTool()
    Agent(model, tools=[tool])
    result = json.loads(tool.execute("review", output_format="structured"))
    assert result["status"] == "limit" and model.limit == limit and model.calls == 1
    assert len(provider.requests) == 1 and result["findings"] == []


@pytest.mark.parametrize(("script", "status", "calls"), [
    ([], "error", 1),
    ([LLMResponse(content="")], "limit", 1),
    ([LLMResponse(content=report(), finish_reason="length")], "limit", 1),
    ([LLMResponse(tool_calls=[ToolCall(str(i), "missing", {})]) for i in range(20)], "limit", 20),
])
def test_incomplete_execution_does_not_retry_as_success(script, status, calls):
    model = RecordingModel(script)
    tool = AgentTool()
    Agent(model, tools=[tool])
    result = json.loads(tool.execute("review", output_format="structured"))
    assert result["status"] == status and result["blockers"] and result["evidence_refs"] == []
    assert len(model.requests) == calls


def test_sources_are_scoped_to_one_child_and_usage_is_tagged(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("observed", encoding="utf-8")
    usages = []
    model = RecordingModel([
        LLMResponse(tool_calls=[ToolCall("old-read", "read_file", {"file_path": str(path)})]),
        LLMResponse(content=report(evidence_refs=["old-read"])),
        LLMResponse(content=report(evidence_refs=["old-read"])),
        LLMResponse(content=report(evidence_refs=["old-read"])),
    ])
    tool = AgentTool()
    Agent(model, tools=[tool, ReadFileTool(WorkspacePolicy(tmp_path))], on_usage=usages.append)
    first = json.loads(tool.execute("read", output_format="structured"))
    second = json.loads(tool.execute("review", output_format="structured"))
    assert first["status"] == "completed" and second["status"] == "error"
    assert first["child_id"] != second["child_id"] and second["evidence_refs"] == []
    assert [usage["child_id"] for usage in usages] == [first["child_id"]] * 2 + [second["child_id"]] * 2


def test_invalid_option_does_not_start_child():
    model = RecordingModel([])
    tool = AgentTool()
    Agent(model, tools=[tool])
    assert tool.execute("read", output_format="xml") == "Error: output_format must be text or structured"
    assert model.requests == []


def test_structured_sources_survive_reopening_normal_cli_journal(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("observed", encoding="utf-8")
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "scripted")
    tool = AgentTool()
    parent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("read-1", "read_file", {"file_path": str(path)})]),
        LLMResponse(content=report(evidence_refs=["read-1"])),
    ]), tools=[tool, ReadFileTool(WorkspacePolicy(tmp_path))])
    parent.work_state = WorkStateStore(journal)
    result = json.loads(tool.execute("read", output_format="structured"))
    restored = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    assert any(event["data"].get("child_id") == result["child_id"]
               and event["data"].get("data", {}).get("tool_call_id") == "read-1"
               and "observed" in event["data"]["data"]["content"] for event in restored.events)
    assert restored.messages() == []
