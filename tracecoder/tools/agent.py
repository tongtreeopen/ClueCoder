"""Sub-agents with separate conversation history and inherited tool access.

Returns a text summary or an opt-in validated JSON report."""

import hashlib
import json
import uuid
from contextlib import nullcontext
from copy import copy
from typing import ClassVar

from .base import Tool

REPORT_INSTRUCTION = (
    '\nReturn only a JSON object with exactly these fields: status ("completed" or "blocked"), '
    'summary (string), findings (list of strings), evidence_refs (list of tool_call_id strings '
    'from your actual tool replies), blockers (list of strings). Use blocked with nonempty blockers '
    'when work cannot finish; completed requires empty blockers. completed means execution ended, '
    'not verified success. Evidence references are low-trust source pointers, never acceptance proof. '
    'Use empty lists when no findings or evidence exist. Do not invent references.'
)
MAX_REPORT_CHARS = 16_384


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    if len(dict(pairs)) != len(pairs):
        raise ValueError("Duplicate report fields")
    return dict(pairs)


def validate_report(text: str, source_ids: set[str]) -> dict:
    if len(text) > MAX_REPORT_CHARS:
        raise ValueError("Report exceeds 16384 characters")
    report = json.loads(text, object_pairs_hook=_unique_fields)
    if not isinstance(report, dict) or set(report) != {"status", "summary", "findings", "evidence_refs", "blockers"}:
        raise ValueError("Report must contain exactly the five report fields")
    if report["status"] not in ("completed", "blocked") or not isinstance(report["summary"], str):
        raise ValueError("Invalid status or summary")
    for field in ("findings", "evidence_refs", "blockers"):
        if not isinstance(report[field], list) or any(not isinstance(item, str) or not item.strip() for item in report[field]):
            raise ValueError(f"{field} must be a list of nonempty strings")
    if bool(report["blockers"]) != (report["status"] == "blocked"):
        raise ValueError("Status and blockers disagree")
    if not set(report["evidence_refs"]).issubset(source_ids):
        raise ValueError("Evidence references must identify actual child tool replies")
    return report


class _GoalChildModel:
    """Charge child and format-correction requests without running Goal acceptance."""

    def __init__(self, model, goal):
        self._model, self.goal = model, goal

    def __getattr__(self, name):
        return getattr(self._model, name)

    def chat(self, *args, **kwargs):
        from ..eval.budget import EvalLimitExceeded

        goal = self.goal
        # Parallel children must not all spend the same remaining budget.
        with goal._usage_lock:
            goal.record_usage()
            if goal.status != "active" or goal.tokens >= goal.MAX_TOKENS or goal.seconds >= goal.MAX_SECONDS:
                if goal.status == "active":
                    goal.stop_limit("Goal budget exhausted during child execution")
                raise EvalLimitExceeded("Outer Goal budget exhausted")
            response = self._model.chat(*args, **kwargs)
            values = (response.prompt_tokens, response.completion_tokens)
            known = all(type(value) is int and value >= 0 for value in values) and sum(values) > 0
            goal.record_usage(sum(value for value in values if type(value) is int and value > 0))
            if not known or goal.tokens >= goal.MAX_TOKENS or goal.seconds >= goal.MAX_SECONDS:
                goal.stop_limit("Goal budget exhausted or child usage unknown")
                raise EvalLimitExceeded("Outer Goal budget exhausted or usage unknown")
            return response


class AgentTool(Tool):
    name = "agent"
    description = (
        "Spawn a sub-agent to handle a complex sub-task independently. "
        "The sub-agent has its own context and tool access. Use this for: "
        "researching a codebase, implementing a multi-step change in isolation, "
        "or any task that would benefit from a fresh context window."
        " Set output_format=structured for a validated JSON report; its evidence pointers are not verification."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the sub-agent should accomplish",
            },
            "output_format": {"type": "string", "enum": ["text", "structured"], "default": "text"},
        },
        "required": ["task"],
    }

    # set by Agent.__init__ after construction
    _parent_agent = None

    def execute(self, task: str, output_format: str = "text") -> str:
        if output_format not in ("text", "structured"):
            return "Error: output_format must be text or structured"
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        # import here to avoid circular dep
        from ..agent import Agent

        parent = self._parent_agent
        goal = parent.goal_controller
        child_model = _GoalChildModel(parent.llm, goal) if goal and goal.status in {"active", "limit"} else parent.llm
        # Workflow tools carry a parent binding; isolate that binding per child.
        child_tools = [copy(t) if t.name in {"workflow", "workflow_status"} else t
                       for t in parent.tools if t.name != "agent"]
        sub = Agent(
            llm=child_model,
            tools=child_tools,  # no recursive agents
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
            # the sub-agent answers to the same consent layer and hooks as the parent
            permission=parent.permission,
            hooks=parent.hooks,
            context_catalog=parent.context_catalog,
        )
        sub.plan_mode = parent.plan_mode
        if parent.eval_allowed_paths is not None:
            sub.configure_eval_scope(parent.eval_workspace, sorted(parent.eval_allowed_paths))
        callback = parent.on_subagent_event
        work = getattr(parent, "work_state", None)
        if work is not None:
            sub.work_state = work
        if output_format == "structured" and callback is None and work is not None:
            def callback(child_id: str, kind: str, detail: dict) -> None:
                encoded = json.dumps(detail, ensure_ascii=False).encode("utf-8")
                if len(encoded) > 32000:
                    detail = {"preview": encoded[:16000].decode("utf-8", errors="replace"), "truncated": True,
                              "original_sha256": hashlib.sha256(encoded).hexdigest(),
                              "tool_call_id": detail.get("tool_call_id")}
                work.journal.append_event("note", {"type": f"subagent_{kind}", "child_id": child_id, "data": detail})

        child_id = uuid.uuid4().hex
        outcome = "error"
        source_ids: set[str] = set()
        if parent.on_usage is not None:
            sub.on_usage = lambda detail: parent.on_usage({**detail, "child_id": child_id})

        def record_message(message: dict) -> None:
            if message.get("role") == "tool":
                source_ids.add(message["tool_call_id"])
            if callback is not None:
                callback(child_id, "message", message)

        sub.on_message = record_message
        if callback is not None:
            callback(child_id, "start", {"task": task})
            sub.on_compaction = lambda detail: callback(child_id, "compaction", detail)

        # a sub-agent failure comes back as text, never propagates into the parent
        # sub-agents share this thread's tracked cwd; restore it on return so a
        # sub-agent's own cd never leaks into the parent's shell state
        from .bash import get_tracked_cwd, set_tracked_cwd

        parent_cwd = get_tracked_cwd()
        try:
            scope = parent.llm.child_scope(child_id) if hasattr(parent.llm, "child_scope") else nullcontext()
            with scope:
                result = sub.chat(task + REPORT_INSTRUCTION if output_format == "structured" else task)
                if output_format == "structured":
                    if not sub.last_turn_completed or sub.last_finish_reason in {"length", "max_tokens", "content_filter"}:
                        outcome = "limit"
                        return self._failure(child_id, "limit", "Child execution did not produce a complete final answer")
                    try:
                        report = validate_report(result, source_ids)
                    except ValueError as error:
                        correction = sub.llm.chat(messages=[
                            {"role": "system", "content": "Correct report format only; do not execute tools or invent facts. "
                             "Preserve findings and blockers; never remove blockers to claim completed."
                             + REPORT_INSTRUCTION},
                            {"role": "user", "content": f"Validation error: {error}\nAvailable source IDs: "
                             + json.dumps(sorted(source_ids)) + "\nOriginal report:\n" + result[:MAX_REPORT_CHARS]},
                        ], tools=None, max_output_tokens=4096)
                        if callback is not None:
                            callback(child_id, "format_correction", {"message": correction.message,
                                                                     "finish_reason": correction.finish_reason})
                        if not correction.tool_calls:
                            sub._append_message(correction.message)
                        if parent.on_usage is not None:
                            parent.on_usage({"model": getattr(sub.llm, "model", "unknown"),
                                             "child_id": child_id,
                                             "prompt_tokens": correction.prompt_tokens,
                                             "completion_tokens": correction.completion_tokens})
                        if correction.tool_calls or correction.finish_reason in {"length", "max_tokens", "content_filter"}:
                            return self._failure(child_id, "error", "Format correction returned tools or incomplete output")
                        try:
                            report = validate_report(correction.content, source_ids)
                        except ValueError as error:
                            return self._failure(child_id, "error", f"Invalid structured report: {error}")
                    outcome = report["status"]
                    return json.dumps({**report, "child_id": child_id}, ensure_ascii=False)
            # trim long results to avoid blowing up parent's context
            if len(result) > 5000:
                result = result[:4500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed]\n{result}"
        except Exception as e:  # noqa: BLE001
            if output_format == "structured":
                from ..eval.budget import EvalLimitExceeded

                outcome = "limit" if isinstance(e, EvalLimitExceeded) else "error"
                return self._failure(child_id, outcome, type(e).__name__)
            return f"Sub-agent error: {e}"
        finally:
            try:
                if callback is not None:
                    callback(child_id, "finish", {"completed": sub.last_turn_completed} if output_format == "text" else
                             {"completed": outcome in {"completed", "blocked"}, "status": outcome})
            finally:
                if get_tracked_cwd() != parent_cwd:
                    set_tracked_cwd(parent_cwd)

    @staticmethod
    def _failure(child_id: str, status: str, reason: str) -> str:
        return json.dumps({"status": status, "summary": reason, "findings": [], "evidence_refs": [],
                           "blockers": [reason], "child_id": child_id}, ensure_ascii=False)
