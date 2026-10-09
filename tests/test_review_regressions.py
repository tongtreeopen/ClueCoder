"""Regression cases from the release-candidate concurrency and child audit."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tracecoder.agent import Agent
from tracecoder.demo import ScriptedLLM
from tracecoder.goal import GoalController
from tracecoder.goal_evaluator import GoalEvaluator
from tracecoder.llm import LLMResponse, ToolCall
from tracecoder.session_journal import SessionJournal
from tracecoder.tools.agent import AgentTool
from tracecoder.tools.work import WorkUpdateTool
from tracecoder.tools.workflow import WorkflowTool
from tracecoder.tools.write import WriteFileTool
from tracecoder.work_state import WorkStateStore
from tracecoder.workspace import WorkspacePolicy


def state(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "scripted")
    work = WorkStateStore(journal)
    work.ensure("Review source")
    return journal, work


def test_parallel_work_calls_preserve_recoverable_revisions(tmp_path):
    journal, work = state(tmp_path)
    agent = Agent(ScriptedLLM([]), tools=[WorkUpdateTool(work)])
    replies = agent._exec_tools_parallel([
        ToolCall("step", "work_update", {"step": "Inspect"}),
        ToolCall("next", "work_update", {"next_action": "Test"}),
    ])
    assert all("Error" not in reply for reply in replies)
    restored = WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path))
    assert restored.card["revision"] == 3
    assert restored.card["step"] == "Inspect"
    assert restored.card["next_action"] == "Test"


def test_separate_work_stores_refresh_before_update(tmp_path):
    journal, first = state(tmp_path)
    second = WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path))
    first.update(step="Inspect")
    second.update(next_action="Test")
    restored = WorkStateStore(SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path))
    assert restored.card["revision"] == 3
    assert restored.card["step"] == "Inspect"
    assert restored.card["next_action"] == "Test"


def test_child_workflow_binding_is_isolated(tmp_path, monkeypatch):
    journal, work = state(tmp_path)
    workflow = WorkflowTool(tmp_path)
    delegate = AgentTool()
    model = ScriptedLLM([LLMResponse(content="child done")])
    parent = Agent(model, tools=[delegate, workflow])
    goal = GoalController(journal, work, GoalEvaluator())
    goal.set("Review source")
    parent.goal_controller = goal
    parent.work_state = work
    delegate.execute("inspect")
    assert workflow._parent_agent is parent
    assert workflow._parent_agent.goal_controller is goal
    assert workflow._parent_agent.work_state is work

    captured = []

    def run(runtime, *args):
        captured.append((runtime.goal, runtime.on_usage))
        return {"run_id": "probe", "status": "completed", "artifacts": "probe"}

    monkeypatch.setattr("tracecoder.tools.workflow.WorkflowRuntime.run", run)
    assert json.loads(workflow.execute("review-changes"))["status"] == "completed"
    assert captured == [(goal, parent.on_usage)]
    assert journal.events[-1]["data"]["type"] == "workflow_run"


def test_child_refuses_disallowed_file_and_can_write_allowed_file(tmp_path):
    delegate = AgentTool()
    model = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("bad", "write_file", {"file_path": "forbidden.py", "content": "bad"})]),
        LLMResponse(tool_calls=[ToolCall("good", "write_file", {"file_path": "solution.py", "content": "good"})]),
        LLMResponse(content="done"),
    ])
    parent = Agent(model, tools=[delegate, WriteFileTool(WorkspacePolicy(tmp_path))])
    parent.configure_eval_scope(tmp_path, ["solution.py"])
    delegate.execute("write")
    assert not (tmp_path / "forbidden.py").exists()
    assert (tmp_path / "solution.py").read_text() == "good"


@pytest.mark.parametrize("known", [True, False])
def test_child_exhaustion_blocks_tools_and_future_requests(tmp_path, known):
    journal, work = state(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator())
    goal.MAX_TOKENS = 10
    goal.set("Review source")
    goal.begin()
    delegate = AgentTool()
    model = ScriptedLLM([LLMResponse(
        tool_calls=[ToolCall("write", "write_file", {"file_path": "bad.py", "content": "bad"})],
        prompt_tokens=25 if known else None, completion_tokens=5 if known else None,
    )])
    parent = Agent(model, tools=[delegate, WriteFileTool(WorkspacePolicy(tmp_path))])
    parent.goal_controller = goal
    result = json.loads(delegate.execute("inspect", output_format="structured"))
    assert result["status"] == "limit"
    assert goal.status == "limit"
    assert goal.tokens == (30 if known else 0)
    assert not (tmp_path / "bad.py").exists()
    # The model has no responses left: this must stop before calling it again.
    assert json.loads(delegate.execute("inspect", output_format="structured"))["status"] == "limit"


def test_parallel_children_share_remaining_goal_budget(tmp_path):
    journal, work = state(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator())
    goal.MAX_TOKENS = 10
    goal.set("Review source")
    goal.begin()
    delegate = AgentTool()
    model = ScriptedLLM([LLMResponse(content="done", prompt_tokens=25, completion_tokens=5)])
    parent = Agent(model, tools=[delegate])
    parent.goal_controller = goal
    start = Barrier(2)

    def run():
        start.wait()
        return json.loads(delegate.execute("inspect", output_format="structured"))["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results == ["limit", "limit"]
    assert goal.tokens == 30


def test_child_format_correction_charges_same_goal(tmp_path):
    journal, work = state(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator())
    goal.MAX_TOKENS = 40
    goal.set("Review source")
    goal.begin()
    delegate = AgentTool()
    model = ScriptedLLM([
        LLMResponse(content="invalid report", prompt_tokens=8, completion_tokens=2),
        LLMResponse(content="{}", prompt_tokens=25, completion_tokens=5),
    ])
    parent = Agent(model, tools=[delegate])
    parent.goal_controller = goal
    assert json.loads(delegate.execute("inspect", output_format="structured"))["status"] == "limit"
    assert goal.tokens == 40 and goal.status == "limit"


def test_child_usage_below_limit_does_not_run_goal_acceptance(tmp_path):
    journal, work = state(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator())
    goal.MAX_TOKENS = 100
    goal.set("Review source")
    goal.begin()
    delegate = AgentTool()
    model = ScriptedLLM([LLMResponse(content="done", prompt_tokens=25, completion_tokens=5)])
    parent = Agent(model, tools=[delegate])
    parent.goal_controller = goal
    assert delegate.execute("inspect") == "[Sub-agent completed]\ndone"
    assert goal.tokens == 30 and goal.status == "active"
    assert goal.pending_candidate is None
    assert goal.evaluator.last_response is None
