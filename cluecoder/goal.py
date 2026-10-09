"""Durable, opt-in stop gate for a CLI session."""

from __future__ import annotations

import json
import math
import time
from threading import RLock
from typing import ClassVar

from .goal_evaluator import GoalEvaluator
from .session_journal import SessionJournal
from .work_state import WorkStateStore


class GoalController:
    MAX_CONTINUATIONS = 3
    MAX_SECONDS = 900
    MAX_TOKENS = 100_000
    STATUSES: ClassVar = {"active", "achieved", "needs_user", "limit", "failed", "error", "cancelled"}

    def __init__(self, journal: SessionJournal, work: WorkStateStore, evaluator: GoalEvaluator,
                 clock=time.monotonic):
        self.journal = journal
        self.work = work
        self.evaluator = evaluator
        self.clock = clock
        self._usage_lock = RLock()
        self.text = ""
        self.status = "cancelled"
        self.reason = ""
        self.since_event = 0
        self.continuations = 0
        self.tokens = 0
        self.seconds = 0.0
        self.pending_candidate = None
        self.candidate_event_id = None
        self._tick = None
        for event in journal.events:
            data = event["data"]
            if event["kind"] != "note" or data.get("type") != "goal_state":
                continue
            if data.get("version") != 1 or data.get("status") not in self.STATUSES:
                raise ValueError("Invalid goal state event")
            if (not isinstance(data.get("text"), str) or not isinstance(data.get("since_event"), int)
                    or not 0 <= data["since_event"] <= event["seq"]
                    or not isinstance(data.get("continuations"), int) or data["continuations"] < 0
                    or not isinstance(data.get("tokens"), int) or data["tokens"] < 0
                    or not isinstance(data.get("seconds"), (int, float))
                    or not math.isfinite(data["seconds"]) or data["seconds"] < 0
                    or (data.get("pending_candidate") is not None
                        and (not isinstance(data["pending_candidate"], str)
                             or len(data["pending_candidate"]) > 8000))
                    or (data.get("candidate_event_id") is not None
                        and (type(data["candidate_event_id"]) is not int
                             or not 1 <= data["candidate_event_id"] <= event["seq"]))):
                raise ValueError("Invalid goal state event")
            if (data["since_event"] == self.since_event and self.since_event
                    and (data["continuations"] < self.continuations or data["tokens"] < self.tokens
                         or data["seconds"] < self.seconds)):
                raise ValueError("Goal budget decreased")
            self._load(data)
        if self.active and self.work.card is not None and self.work.card["goal"] != self.text:
            raise ValueError("Goal and work card disagree; recovery stopped")

    @property
    def active(self) -> bool:
        return self.status == "active"

    def _load(self, data: dict) -> None:
        self.text = data["text"]
        self.status = data["status"]
        self.reason = data.get("reason", "")
        self.since_event = data["since_event"]
        self.continuations = data["continuations"]
        self.tokens = data["tokens"]
        self.seconds = data["seconds"]
        self.pending_candidate = data.get("pending_candidate")
        self.candidate_event_id = data.get("candidate_event_id")

    def _save(self, **changes) -> None:
        state = {
            "type": "goal_state", "version": 1, "text": self.text, "status": self.status,
            "reason": self.reason, "since_event": self.since_event,
            "continuations": self.continuations, "tokens": self.tokens, "seconds": self.seconds,
            "pending_candidate": self.pending_candidate,
            "candidate_event_id": self.candidate_event_id,
        }
        state.update(changes)
        event_id = self.journal.append_event("note", state)
        self._load(self.journal.events[event_id - 1]["data"])

    def set(self, text: str) -> None:
        text = text.strip()
        if not text or len(text) > 1000:
            raise ValueError("Goal must contain 1–1000 characters")
        if self.active:
            self._save(status="cancelled", reason="Replaced by new goal", pending_candidate=None)
        self.work.replace_goal(text)
        self._save(text=text, status="active", reason="", since_event=len(self.journal.events) + 1,
                   continuations=0, tokens=0, seconds=0.0, pending_candidate=None, candidate_event_id=None)

    def off(self) -> None:
        if self.text and self.status not in {"cancelled", "achieved"}:
            self._save(status="cancelled", reason="Disabled by user", pending_candidate=None,
                       candidate_event_id=None)

    def stop_limit(self, reason: str) -> None:
        self.record_usage()
        self._save(status="limit", reason=reason, pending_candidate=None)

    def resume_user(self) -> None:
        """A fresh user turn may retry a handed-back goal without resetting budgets."""
        if self.status == "failed":
            raise ValueError("Goal was judged impossible; set a new goal")
        if self.status not in {"needs_user", "limit", "error"}:
            return
        if self.seconds >= self.MAX_SECONDS or self.tokens >= self.MAX_TOKENS:
            raise ValueError("Goal time or token budget is exhausted; set a new goal")
        if self.work.card is None or self.work.card["goal"] != self.text:
            raise ValueError("Goal and work card disagree")
        self._save(status="active", reason="User-requested continuation", pending_candidate=None,
                   candidate_event_id=None)

    def begin(self) -> None:
        if self.active and self._tick is None:
            self._tick = self.clock()

    def record_usage(self, tokens: int = 0) -> None:
        with self._usage_lock:
            now = self.clock()
            elapsed = max(0, now - self._tick) if self._tick is not None else 0.0
            self._tick = now
            self._save(tokens=self.tokens + max(0, tokens), seconds=self.seconds + elapsed)

    def pause(self) -> None:
        if self._tick is not None:
            self.record_usage()
            self._tick = None

    def decide(self, candidate: str, worker_tokens: int = 0) -> str:
        """Return achieved/continue/needs_user/limit/failed/error."""
        self.record_usage(worker_tokens)
        source = next((event["seq"] for event in reversed(self.journal.events)
                       if event["kind"] == "message" and event["seq"] >= self.since_event
                       and event["data"].get("message", {}).get("role") == "assistant"
                       and event["data"]["message"].get("content") == candidate), None)
        self._save(pending_candidate=candidate if len(candidate) <= 8000 else None,
                   candidate_event_id=source)
        ids, evidence_reason = self.work.goal_evidence(self.since_event)
        if self.work.card is None or self.work.card["goal"] != self.text:
            self._save(status="needs_user", reason="Goal and work card disagree", pending_candidate=None)
            return self.status
        if any(event["kind"] == "note" and event["seq"] >= self.since_event
               and event["data"].get("type") == "goal_tool_outcome"
               and event["data"].get("status") == "denied" for event in self.journal.events):
            self._save(status="needs_user", reason="A tool call was refused; user decision required",
                       pending_candidate=None)
            return self.status
        if self.work.card and self.work.card["blockers"]:
            self._save(status="needs_user", reason=evidence_reason, pending_candidate=None)
            return self.status
        if self.seconds >= self.MAX_SECONDS or self.tokens >= self.MAX_TOKENS:
            self._save(status="limit", reason="Goal budget exhausted", pending_candidate=None)
            return self.status
        if not ids and evidence_reason != "User explicitly confirmed work":
            return self._continue(evidence_reason)
        evidence = [{"id": item, "content": (
            "User explicitly confirmed work" if self.journal.events[item - 1]["data"].get("type") ==
            "user_work_confirmation" else
            str(self.journal.events[item - 1]["data"].get("message", {}).get("content", ""))[:1000]
            if self.journal.events[item - 1]["kind"] == "message" else
            json.dumps(self.journal.events[item - 1]["data"], ensure_ascii=False)[:1000]
        )} for item in ids]
        try:
            context = [{"id": event["seq"], "time": event["time"], "content": json.dumps({
                key: value for key, value in event["data"]["message"].items()
                if key in {"role", "content", "tool_calls", "tool_call_id"}
            }, ensure_ascii=False)[:2000]} for event in self.journal.events[self.since_event - 1:]
                if event["kind"] == "message"]
            current_user_id = next((event["seq"] for event in reversed(self.journal.events[self.since_event - 1:])
                                    if event["kind"] == "message"
                                    and event["data"]["message"].get("role") == "user"), None)
            recent_context = context[-24:]
            if current_user_id and not any(item["id"] == current_user_id for item in recent_context):
                recent_context.insert(0, next(item for item in context if item["id"] == current_user_id))
            decision, tokens = self.evaluator.evaluate(self.text, candidate, evidence, recent_context)
            self.record_usage(tokens)
        except Exception as error:  # evaluator failure is a non-success terminal result  # noqa: BLE001 - model boundary
            self._record_evaluation()
            if self.evaluator.last_usage:
                self.record_usage(self.evaluator.last_usage)
            self._save(status="error", reason=f"Goal evaluator failed: {error}", pending_candidate=None)
            return self.status
        self._record_evaluation()
        if self.seconds >= self.MAX_SECONDS or self.tokens >= self.MAX_TOKENS:
            self._save(status="limit", reason="Goal budget exhausted", pending_candidate=None)
        elif decision["impossible"]:
            self._save(status="failed", reason=decision["reason"], pending_candidate=None)
        elif decision["ok"]:
            if set(decision["evidence_event_ids"]) != set(ids):
                return self._continue("Evaluator did not cite all verified evidence")
            if self.work.card["status"] != "completed":
                self.work.complete()
                if self.work.card["status"] != "completed":
                    self._save(status="needs_user", reason="Work card needs user review", pending_candidate=None)
                    return self.status
            self._save(status="achieved", reason=decision["reason"], pending_candidate=None)
        else:
            return self._continue(decision["reason"])
        return self.status

    def _record_evaluation(self) -> None:
        response = self.evaluator.last_response
        if response is not None:
            self.journal.append_event("note", {"type": "goal_evaluation",
                "content": response.content[:8000], "truncated_log": len(response.content) > 8000,
                "finish_reason": response.finish_reason, "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens})

    def resolve_pending(self) -> str | None:
        if not self.active or self.pending_candidate is None:
            return None
        if self.candidate_event_id is None:
            self._save(status="needs_user", reason="Candidate source is unavailable after recovery",
                       pending_candidate=None, candidate_event_id=None)
            return self.status
        return self.decide(self.pending_candidate)

    def _continue(self, reason: str) -> str:
        if self.continuations >= self.MAX_CONTINUATIONS:
            self._save(status="limit", reason=reason, pending_candidate=None)
            return self.status
        self._save(continuations=self.continuations + 1, reason=reason[:1000], pending_candidate=None)
        return "continue"

    def feedback(self) -> str:
        if not self.active:
            return ""
        calls = {}
        results = []
        for event in self.journal.events[self.since_event - 1:]:
            if event["kind"] == "note" and event["data"].get("type") == "background_result":
                results.append(f"event:{event['seq']} background command={event['data'].get('command')!r}")
            if event["kind"] != "message":
                continue
            message = event["data"].get("message", {})
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") != "bash":
                    continue
                arguments = function.get("arguments", {})
                try:
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    calls[call.get("id")] = str(arguments.get("command", ""))[:120]
                except (TypeError, ValueError, AttributeError):
                    continue
            if message.get("role") == "tool" and message.get("tool_call_id") in calls:
                results.append(f"event:{event['seq']} command={calls[message['tool_call_id']]!r}")
        return (
            "An explicit Goal Stop Gate is active. Goal: " + self.text[:1000] +
            ". Keep the work card goal unchanged. If a required check failed/cancelled and repair or rerun is "
            "forbidden, record work_update(blockers=['reason'], complete=false) and report unverified in the "
            "requested format; do not hunt for substitute success evidence. "
            ". Before a final answer, record an acceptance check with work_update(criteria=[{kind:'check', "
            "description:'...', command:'exact Bash command', event_id:<tool result event ID>}]). "
            "A real successful Bash result must match the exact command and remain fresh after writes/Bash. "
            "For background commands use only persisted background_result event IDs; running tasks cannot pass. "
            "For subjective work, ask the user to use /work confirm. Do not invent event IDs. " +
            ("Last rejected candidate reason: " + self.reason[:500] + ". " if self.reason else "") +
            "Recent Bash result references (untrusted data): " + ("; ".join(results[-5:]) or "none")
        )[:2000]

    def summary(self) -> str:
        if not self.text:
            return "No goal set."
        return (f"Goal: {self.text}\nStatus: {self.status}\nChecks: {self.work.goal_evidence(self.since_event)[1]}\n"
                f"Budget: {self.continuations}/{self.MAX_CONTINUATIONS} continuations, "
                f"{self.seconds:.1f}/{self.MAX_SECONDS}s, {self.tokens}/{self.MAX_TOKENS} reported tokens\n"
                f"Last reason: {self.reason or 'none'}")
