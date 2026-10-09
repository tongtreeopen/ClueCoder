import json
from typing import ClassVar

import pytest

from tracecoder import cli as cli_module
from tracecoder import session as session_module
from tracecoder.agent import Agent
from tracecoder.cli_memory import MemoryCLI
from tracecoder.config import Config
from tracecoder.demo import ScriptedLLM
from tracecoder.goal import GoalController
from tracecoder.goal_evaluator import GoalEvaluator
from tracecoder.llm import LLMResponse
from tracecoder.memory import MemoryStore
from tracecoder.memory_workflow import MemoryWorkflow
from tracecoder.permissions import Permission
from tracecoder.session_journal import SessionJournal
from tracecoder.tools.bash import DisabledBashTool
from tracecoder.tools.work import WorkUpdateTool
from tracecoder.tools.write import WriteFileTool
from tracecoder.work_state import WorkStateStore
from tracecoder.workspace import WorkspacePolicy


def _session(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "model")
    return journal, WorkStateStore(journal)


def _check(journal, command="pytest -q", content="2 passed"):
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "check", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": command})}},
            ],
        }
    )
    return journal.append_message({"role": "tool", "tool_call_id": "check", "content": content})


def test_goal_state_restores_and_replacement_resets_budget_and_work(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    work.update(blockers=["Need tests"], step="Investigate")
    goal.record_usage(12)
    goal.set("Fix another bug")
    assert goal.continuations == 0 and goal.tokens == 0
    assert work.card["blockers"] == [] and work.card["step"] == ""
    restored_journal = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    restored = GoalController(restored_journal, WorkStateStore(restored_journal), GoalEvaluator(ScriptedLLM([])))
    assert restored.active and restored.text == "Fix another bug" and restored.tokens == 0
    restored.off()
    assert GoalController(restored_journal, work, GoalEvaluator(ScriptedLLM([]))).status == "cancelled"


def test_goal_evidence_is_read_only_and_stales_after_write(tmp_path):
    journal, work = _session(tmp_path)
    work.ensure("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    revision = work.card["revision"]
    assert work.goal_evidence(1)[0] == [passed]
    assert work.card["revision"] == revision
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "write", "type": "function", "function": {"name": "write_file", "arguments": "{}"}},
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "write", "content": "written"})
    assert not work.goal_evidence(1)[0]


def test_earlier_acceptance_check_stales_even_if_later_check_passes(tmp_path):
    journal, work = _session(tmp_path)
    work.ensure("Fix bug")
    first = _check(journal, command="pytest -q")
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "write", "type": "function", "function": {"name": "write_file", "arguments": "{}"}},
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "write", "content": "written"})
    second = _check(journal, command="ruff check .")
    work.update(
        criteria=[
            {"description": "tests", "kind": "check", "event_id": first, "command": "pytest -q"},
            {"description": "lint", "kind": "check", "event_id": second, "command": "ruff check ."},
        ]
    )
    assert not work.goal_evidence(1)[0]


def test_user_confirmation_stales_after_subsequent_write(tmp_path):
    journal, work = _session(tmp_path)
    work.ensure("Review result")
    journal.append_event("note", {"type": "user_work_confirmation", "revision": work.card["revision"]})
    work.confirm()
    assert work.goal_evidence(1)[0]
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "write", "type": "function", "function": {"name": "write_file", "arguments": "{}"}},
            ],
        }
    )
    assert not work.goal_evidence(1)[0]


def test_goal_buffers_rejected_candidate_and_reuses_same_agent_loop(tmp_path):
    journal, work = _session(tmp_path)
    evaluator = GoalEvaluator(
        ScriptedLLM([LLMResponse(content='{"ok":true,"reason":"done","impossible":false,"evidence_event_ids":[4]}')])
    )
    goal = GoalController(journal, work, evaluator)
    goal.set("Fix bug")
    passed = _check(journal)
    assert passed == 4  # goal/work events precede the check
    assert f"event:{passed}" in goal.feedback()
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    agent = Agent(ScriptedLLM([LLMResponse(content="final")]), tools=[], on_message=journal.append_message)
    agent.goal_controller = goal
    streamed = []
    assert agent.chat("report", on_token=streamed.append) == "final"
    assert streamed == ["final"] and goal.status == "achieved" and agent.last_turn_completed


def test_goal_without_evidence_has_bounded_continuations_and_never_streams_candidates(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    agent = Agent(ScriptedLLM([LLMResponse(content=f"done {i}") for i in range(4)]), tools=[], on_message=journal.append_message)
    agent.goal_controller = goal
    streamed = []
    answer = agent.chat("Start", on_token=streamed.append)
    assert goal.status == "limit" and goal.continuations == 3
    assert streamed == [] and "candidate unaccepted" in answer and agent.last_turn_completed
    assert "done 0" in str(journal.events)


@pytest.mark.parametrize(("wrapper",), [("{}",), ("```json\n{}\n```",), ("```\n{}\n```",)])
def test_blocked_goal_returns_json_without_retry_or_success_promotion(tmp_path, wrapper):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Only accept the fixed check; do not rerun")
    failed = _check(journal, content="Error: exit code 1")
    work.update(blockers=["Fixed check failed; rerun forbidden"])
    payload = {"verified": False, "reason": "Fixed check failed", "evidence_event_ids": [failed]}
    candidate = wrapper.format(json.dumps(payload))
    agent = Agent(
        ScriptedLLM(
            [LLMResponse(content=candidate, prompt_tokens=5, completion_tokens=7, reasoning_content="Preserve native history")]
        ),
        tools=[],
        on_message=journal.append_message,
    )
    agent.goal_controller = goal
    assert json.loads(agent.chat("Return a single JSON object")) == payload
    assert json.loads(agent.messages[-1]["content"]) == payload
    assert agent.messages[-1]["reasoning_content"] == "Preserve native history"
    assert journal.events[goal.candidate_event_id - 1]["data"]["message"]["content"] == candidate
    assert goal.status == "needs_user" and goal.continuations == 0 and goal.tokens == 12
    assert work.card["status"] == "blocked" and work.card["criteria"] == []
    assert (
        sum(
            event["data"].get("message", {}).get("role") == "assistant" and bool(event["data"]["message"].get("tool_calls"))
            for event in journal.events
        )
        == 1
    )


@pytest.mark.parametrize(
    ("candidate",),
    [
        ('```json\n{"verified":true}\n```',),
        ('Explanation\n```json\n{"verified":false}\n```',),
        ('```json\n{"verified":false,}\n```',),
        ('```json\n[{"verified":false}]\n```',),
    ],
)
def test_blocked_report_does_not_repair_facts_or_malformed_json(tmp_path, candidate):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    work.update(blockers=["Cannot verify"])
    agent = Agent(ScriptedLLM([LLMResponse(content=candidate)]), tools=[], on_message=journal.append_message)
    agent.goal_controller = goal
    assert "candidate unaccepted" in agent.chat("Return JSON")
    assert agent.messages[-1]["content"] == candidate and goal.status == "needs_user"


def test_json_report_without_goal_is_unchanged():
    candidate = '```json\n{"verified":false}\n```'
    assert Agent(ScriptedLLM([LLMResponse(content=candidate)]), tools=[]).chat("Return JSON") == candidate


def test_empty_truncated_worker_stops_without_poisoning_history_or_retry(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Run the fixed check")
    model = ScriptedLLM(
        [LLMResponse(content="", reasoning_content="unfinished", finish_reason="length", prompt_tokens=9, completion_tokens=1200)]
    )
    agent = Agent(model, tools=[], on_message=journal.append_message)
    agent.goal_controller = goal
    assert "Model returned no final content" in agent.chat("Verify")
    assert goal.status == "limit" and goal.tokens == 1209 and goal.continuations == 0
    assert not any(message["role"] == "assistant" for message in agent.messages)
    assert any(event["data"].get("type") == "goal_worker_output" for event in journal.events)


@pytest.mark.parametrize(
    ("answer",),
    [
        ["not json"],
        ['{"ok":true,"reason":"yes","impossible":true,"evidence_event_ids":[4]}'],
        ['{"ok":true,"reason":"yes","impossible":false,"evidence_event_ids":[999]}'],
        ['{"ok":true,"reason":"yes","impossible":false,"evidence_event_ids":[]}'],
    ],
)
def test_evaluator_errors_and_missing_citations_never_achieve(tmp_path, answer):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([LLMResponse(content=answer[0])])))
    goal.set("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    assert goal.decide("done") != "achieved"
    assert goal.status != "achieved" and not work.card["archived"]


def test_permission_refusal_needs_user_and_does_not_retry(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Write file")
    target = tmp_path / "result.txt"
    from tracecoder.llm import ToolCall

    agent = Agent(
        ScriptedLLM(
            [
                LLMResponse(tool_calls=[ToolCall("write", "write_file", {"file_path": str(target), "content": "x"})]),
                LLMResponse(content="done"),
            ]
        ),
        tools=[WriteFileTool(WorkspacePolicy(tmp_path))],
        permission=Permission(),
        on_message=journal.append_message,
    )
    agent.goal_controller = goal
    assert "needs_user" in agent.chat("Start")
    assert not target.exists() and goal.continuations == 0
    assert any(event["data"].get("status") == "denied" for event in journal.events if event["kind"] == "note")


def test_user_confirmation_is_evidence_for_independent_evaluator(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(
        journal,
        work,
        GoalEvaluator(
            ScriptedLLM(
                [
                    LLMResponse(content='{"ok":true,"reason":"confirmed","impossible":false,"evidence_event_ids":[3]}'),
                ]
            )
        ),
    )
    goal.set("Review the result")
    assert journal.append_event("note", {"type": "user_work_confirmation", "revision": work.card["revision"]}) == 3
    work.confirm()
    assert goal.decide("Done") == "achieved"


def test_goal_resume_keeps_continuation_budget(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    assert goal.decide("No test yet") == "continue"
    restored_journal = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    restored = GoalController(restored_journal, WorkStateStore(restored_journal), GoalEvaluator(ScriptedLLM([])))
    assert restored.continuations == 1 and restored.active
    assert restored.decide("Still no test") == "continue"
    assert restored.decide("Still no test") == "continue"
    assert restored.decide("Still no test") == "limit"


def test_user_can_confirm_after_handoff_then_retry_without_budget_reset(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Review result")
    for _ in range(3):
        assert goal.decide("No evidence yet") == "continue"
    assert goal.decide("Hand back") == "limit"
    confirmation = journal.append_event(
        "note",
        {
            "type": "user_work_confirmation",
            "revision": work.card["revision"],
        },
    )
    work.confirm()
    goal.evaluator = GoalEvaluator(
        ScriptedLLM(
            [
                LLMResponse(
                    content=json.dumps(
                        {
                            "ok": True,
                            "reason": "confirmed",
                            "impossible": False,
                            "evidence_event_ids": [confirmation],
                        }
                    )
                )
            ]
        )
    )
    goal.resume_user()
    assert goal.continuations == 3 and goal.decide("Reviewed") == "achieved"


def test_final_candidate_after_three_continuations_can_still_achieve(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    for _ in range(3):
        assert goal.decide("Not yet") == "continue"
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    # Evaluator cites the actual journal event, not a model-generated claim.
    goal.evaluator = GoalEvaluator(
        ScriptedLLM(
            [
                LLMResponse(
                    content=json.dumps(
                        {
                            "ok": True,
                            "reason": "passed",
                            "impossible": False,
                            "evidence_event_ids": [passed],
                        }
                    )
                )
            ]
        )
    )
    assert goal.decide("Done") == "achieved"


def test_resumed_pending_candidate_is_evaluated_without_worker_replay(tmp_path, capsys):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    candidate_id = journal.append_message({"role": "assistant", "content": "Done"})
    goal._save(pending_candidate="Done", candidate_event_id=candidate_id)
    restored_journal = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    restored_work = WorkStateStore(restored_journal)
    evaluator = GoalEvaluator(
        ScriptedLLM(
            [
                LLMResponse(
                    content=json.dumps(
                        {
                            "ok": True,
                            "reason": "passed",
                            "impossible": False,
                            "evidence_event_ids": [passed],
                        }
                    )
                )
            ]
        )
    )
    restored = GoalController(restored_journal, restored_work, evaluator)
    agent = Agent(ScriptedLLM([]), tools=[], on_message=restored_journal.append_message)
    agent.goal_controller = restored
    cli_module._run_once(agent, "continue", journal=restored_journal)
    assert restored.status == "achieved" and "Done" in capsys.readouterr().out


def test_one_shot_goal_limit_exits_nonzero_without_accepted_answer(tmp_path, capsys):
    journal, work = _session(tmp_path)
    agent = Agent(ScriptedLLM([LLMResponse(content="done") for _ in range(4)]), tools=[], on_message=journal.append_message)
    agent.goal_controller = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    with pytest.raises(SystemExit) as error:
        cli_module._run_once(agent, "/goal Fix bug", journal=journal)
    assert error.value.code == 1
    assert "candidate unaccepted" in capsys.readouterr().out


def test_time_and_reported_token_limits_fail_closed(tmp_path):
    journal, work = _session(tmp_path)
    now = [0.0]
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])), clock=lambda: now[0])
    goal.set("Fix bug")
    goal.begin()
    now[0] = 901.0
    assert goal.decide("done") == "limit"
    goal.set("Fix another bug")
    goal.record_usage(99_999)
    assert goal.tokens == 99_999 and goal.status == "active"
    assert goal.decide("done", worker_tokens=1) == "limit"
    assert goal.tokens == 100_000 and goal.seconds == 0


def test_evaluator_failed_parse_still_counts_reported_usage(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(
        journal,
        work,
        GoalEvaluator(
            ScriptedLLM(
                [
                    LLMResponse(content="not json", prompt_tokens=12, completion_tokens=5),
                ]
            )
        ),
    )
    goal.set("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    assert goal.decide("done") == "error" and goal.tokens == 17


def test_evaluator_truncation_is_recorded_and_cannot_accept(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(
        journal,
        work,
        GoalEvaluator(
            ScriptedLLM(
                [
                    LLMResponse(
                        content='{"ok":true,"reason":"done","impossible":false,"evidence_event_ids":[4]}',
                        prompt_tokens=12,
                        completion_tokens=500,
                        finish_reason="length",
                    ),
                ]
            )
        ),
    )
    goal.set("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    assert goal.decide("done") == "error" and goal.tokens == 512
    assert "truncated" in goal.reason
    recorded = [e["data"] for e in journal.events if e["data"].get("type") == "goal_evaluation"]
    assert recorded[0]["finish_reason"] == "length" and recorded[0]["content"].startswith('{"ok":true')


def test_evaluator_context_preserves_activity_without_promoting_it(tmp_path):
    from tracecoder.eval.budget import BudgetedModel

    class InspectingEvaluator(ScriptedLLM):
        is_deepseek = True

        def chat(self, messages, tools=None, on_token=None, max_output_tokens=None, extra_body=None, response_format=None):
            payload = json.loads(messages[-1]["content"])
            assert tools is None and max_output_tokens == 2000
            assert extra_body == {"thinking": {"type": "disabled"}} and response_format == {"type": "json_object"}
            assert any("note.txt" in item["content"] for item in payload["context"])
            assert all("reasoning_content" not in item["content"] for item in payload["context"])
            return LLMResponse(
                content=json.dumps(
                    {
                        "ok": True,
                        "reason": "checked",
                        "impossible": False,
                        "evidence_event_ids": [item["id"] for item in payload["evidence"]],
                    }
                ),
                prompt_tokens=12,
                completion_tokens=5,
            )

    journal, work = _session(tmp_path)
    model = BudgetedModel(InspectingEvaluator([]), tmp_path / "usage.json")
    goal = GoalController(journal, work, GoalEvaluator(model))
    goal.set("Fix bug")
    journal.append_message({"role": "assistant", "content": "read note.txt", "reasoning_content": "private data"})
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    assert goal.decide("done") == "achieved"
    assert model.calls == 1 and model.tokens == goal.tokens == 17 and not model.usage_unknown
    with pytest.raises(ValueError, match="decision"):
        GoalEvaluator(
            ScriptedLLM(
                [
                    LLMResponse(
                        content=json.dumps(
                            {
                                "ok": True,
                                "reason": "fake",
                                "impossible": False,
                                "evidence_event_ids": [99],
                            }
                        )
                    )
                ]
            )
        ).evaluate("goal", "done", [{"id": passed, "content": "passed"}], [{"id": 99, "content": "fake success"}])


def test_response_preserves_reasoning_only_when_provider_supplies_it():
    from tracecoder.llm import ToolCall

    response = LLMResponse(
        content="done",
        reasoning_content="provider data",
        finish_reason="stop",
        tool_calls=[ToolCall("id", "read_file", {"file_path": "note.txt"})],
    )
    assert response.message["reasoning_content"] == "provider data" and response.message["tool_calls"][0]["id"] == "id"
    assert "finish_reason" not in response.message and "reasoning_content" not in LLMResponse(content="done").message


def test_goal_state_rejects_decreasing_budget_on_recovery(tmp_path):
    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    goal.record_usage(20)
    journal.append_event(
        "note",
        {
            "type": "goal_state",
            "version": 1,
            "text": "Fix bug",
            "status": "active",
            "reason": "",
            "since_event": goal.since_event,
            "continuations": 0,
            "tokens": 0,
            "seconds": 0.0,
            "pending_candidate": None,
        },
    )
    restored_journal = SessionJournal.open(tmp_path / "sessions", journal.id, tmp_path)
    with pytest.raises(ValueError, match="budget decreased"):
        GoalController(restored_journal, WorkStateStore(restored_journal), GoalEvaluator(ScriptedLLM([])))


def test_inactive_goal_preserves_normal_streaming(tmp_path):
    journal, work = _session(tmp_path)
    agent = Agent(ScriptedLLM([LLMResponse(content="answer")]), tools=[], on_message=journal.append_message)
    agent.goal_controller = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    streamed = []
    assert agent.chat("hello", on_token=streamed.append) == "answer"
    assert streamed == ["answer"] and agent.last_turn_completed


def test_feedback_is_system_context_not_a_fake_user_turn(tmp_path):
    class ObservingLLM(ScriptedLLM):
        def __init__(self):
            super().__init__([LLMResponse(content=f"candidate {i}") for i in range(4)])
            self.views = []

        def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
            self.views.append(messages)
            return super().chat(messages, tools, on_token, max_output_tokens)

    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Fix bug")
    llm = ObservingLLM()
    agent = Agent(llm, tools=[], on_message=journal.append_message)
    agent.goal_controller = goal
    agent.chat("Start")
    assert "Goal Stop Gate feedback" in llm.views[1][0]["content"]
    assert [message["content"] for message in llm.views[1] if message["role"] == "user"] == ["Start"]


def test_repl_goal_waits_in_plan_mode_and_can_be_cancelled(tmp_path, monkeypatch):
    journal, work = _session(tmp_path)
    agent = Agent(ScriptedLLM([]), tools=[])
    agent.goal_controller = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    replies = iter(["/plan", "/goal Fix bug", "/goal", "/goal off", "quit"])
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(cli_module, "FileHistory", lambda path: None)
    cli_module._repl(agent, Config())
    assert agent.goal_controller.status == "cancelled"
    assert work.card["goal"] == "Fix bug" and agent.messages == []


def test_auto_consent_cannot_turn_disabled_bash_into_goal_evidence(tmp_path):
    from tracecoder.llm import ToolCall

    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.set("Run tests")
    agent = Agent(
        ScriptedLLM(
            [
                LLMResponse(tool_calls=[ToolCall("bash", "bash", {"command": "pytest -q"})]),
                *[LLMResponse(content="tests passed") for _ in range(4)],
            ]
        ),
        tools=[DisabledBashTool()],
        permission=Permission(allow_all=True),
        on_message=journal.append_message,
    )
    agent.goal_controller = goal
    assert "Goal limit" in agent.chat("Run tests")
    assert goal.status == "limit" and not work.card["archived"]


def test_independent_evaluator_receives_no_tools(tmp_path):
    class InspectingEvaluator(ScriptedLLM):
        def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
            assert tools is None and all(message["role"] in {"system", "user"} for message in messages)
            return super().chat(messages, tools, on_token, max_output_tokens)

    journal, work = _session(tmp_path)
    goal = GoalController(
        journal,
        work,
        GoalEvaluator(
            InspectingEvaluator(
                [
                    LLMResponse(content='{"ok":true,"reason":"passed","impossible":false,"evidence_event_ids":[4]}'),
                ]
            )
        ),
    )
    goal.set("Fix bug")
    passed = _check(journal)
    work.update(criteria=[{"description": "tests", "kind": "check", "event_id": passed, "command": "pytest -q"}])
    assert goal.decide("Done") == "achieved"


def test_repl_reset_starts_new_session_without_old_goal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    journal = session_module.start_journal(tmp_path, "scripted")
    work = WorkStateStore(journal)
    memory = MemoryCLI(MemoryWorkflow(MemoryStore(tmp_path), ScriptedLLM([])), lambda: "", notify=lambda text: None)
    agent = Agent(ScriptedLLM([]), tools=[])
    cli_module._bind_work(agent, journal, memory, work)
    agent.goal_controller = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    agent.goal_controller.set("Old goal")
    replies = iter(["/reset", "quit"])
    monkeypatch.setattr(cli_module, "pt_prompt", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(cli_module, "FileHistory", lambda path: None)
    cli_module._repl(agent, Config(), memory=memory, journal=journal)
    assert agent.goal_controller.text == "" and not agent.goal_controller.active
    assert memory.journal.id != journal.id
    assert GoalController(journal, work, GoalEvaluator(ScriptedLLM([]))).active


def test_goal_end_to_end_registers_real_result_then_accepts_once(tmp_path):
    from tracecoder.llm import ToolCall

    class TestBash:
        name = "bash"
        description = "Offline check"
        parameters: ClassVar = {"type": "object", "properties": {"command": {"type": "string"}}}

        def schema(self):
            return {
                "type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
            }

        def execute(self, command):
            assert command == "pytest -q"
            return "2 passed"

    journal, work = _session(tmp_path)
    goal = GoalController(journal, work, GoalEvaluator(None))
    goal.set("Fix bug and pass tests")

    class Worker:
        def __init__(self):
            self.round = 0

        def chat(self, messages, tools=None, on_token=None):
            self.round += 1
            if self.round == 1:
                return LLMResponse(
                    tool_calls=[ToolCall("test", "bash", {"command": "pytest -q"})], prompt_tokens=2, completion_tokens=3
                )
            if self.round == 2:
                event_id = next(
                    event["seq"]
                    for event in journal.events
                    if event["kind"] == "message" and event["data"]["message"].get("tool_call_id") == "test"
                )
                assert f"event:{event_id}" in messages[0]["content"]
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            "work",
                            "work_update",
                            {
                                "criteria": [
                                    {"description": "tests", "kind": "check", "command": "pytest -q", "event_id": event_id},
                                ]
                            },
                        )
                    ],
                    prompt_tokens=2,
                    completion_tokens=3,
                )
            return LLMResponse(content="Done", prompt_tokens=2, completion_tokens=3)

    class Evaluator:
        def chat(self, messages, tools=None, max_output_tokens=None):
            assert tools is None
            evidence = json.loads(messages[1]["content"])["evidence"]
            return LLMResponse(
                content=json.dumps(
                    {
                        "ok": True,
                        "reason": "tests passed",
                        "impossible": False,
                        "evidence_event_ids": [item["id"] for item in evidence],
                    }
                ),
                prompt_tokens=2,
                completion_tokens=1,
            )

    goal.evaluator = GoalEvaluator(Evaluator())
    agent = Agent(
        Worker(),
        tools=[TestBash(), WorkUpdateTool(work)],
        permission=Permission(allow_all=True),
        on_message=journal.append_message,
    )
    agent.goal_controller = goal
    streamed = []
    assert agent.chat("Start", on_token=streamed.append) == "Done"
    assert streamed == ["Done"] and goal.status == "achieved" and goal.tokens == 18
    assert work.card["status"] == "completed"
