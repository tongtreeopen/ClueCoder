"""Recoverable session work card with evidence-based completion."""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy

from .session_journal import SessionJournal

# Work card goals are summaries. _text rejects longer text by design, so a caller
# seeding a card from text it does not control must slice to this first.
GOAL_LIMIT = 1000


def _text(value: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid work card text")
    return value.strip()


class WorkStateStore:
    def __init__(self, journal: SessionJournal):
        self.journal = journal
        self.background_manager = None
        self.snapshot_path = journal.directory / "work_state.json"
        if self.snapshot_path.is_symlink() or not self.snapshot_path.resolve().is_relative_to(journal.directory.resolve()):
            raise ValueError("Work state path escapes session")
        self.card: dict | None = None
        for event in journal.events:
            if event["kind"] == "note" and event["data"].get("type") == "work_state":
                candidate = event["data"].get("card")
                if not isinstance(candidate, dict) or candidate.get("revision") != ((self.card or {}).get("revision", 0) + 1):
                    raise ValueError("Invalid work state revision")
                self.card = deepcopy(candidate)
        if self.card is not None:
            try:
                saved = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None
            if saved != self.card and journal.repair:
                self._snapshot(self.card)

    def _snapshot(self, card: dict) -> None:
        if self.snapshot_path.is_symlink() or not self.snapshot_path.resolve().is_relative_to(self.journal.directory.resolve()):
            raise ValueError("Work state path escapes session")
        temp = self.snapshot_path.with_name(f"work_state.{uuid.uuid4().hex}.tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(card, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.snapshot_path)

    def _save(self, card: dict) -> dict:
        card["revision"] = (self.card or {}).get("revision", 0) + 1
        self.journal.append_event("note", {"type": "work_state", "card": card})
        self.card = deepcopy(self.journal.events[-1]["data"]["card"])
        self._snapshot(self.card)
        return deepcopy(self.card)

    def ensure(self, goal: str) -> dict:
        if self.card is not None:
            return deepcopy(self.card)
        goal = _text(goal, GOAL_LIMIT)
        if not goal:
            raise ValueError("Work goal is required")
        return self._save(
            {
                "goal": goal,
                "criteria": [],
                "step": "",
                "todos": [],
                "evidence_event_ids": [],
                "blockers": [],
                "next_action": "",
                "status": "active",
                "archived": False,
            }
        )

    def update(self, *, goal=None, criteria=None, step=None, next_action=None, blockers=None, evidence_event_ids=None) -> dict:
        if self.card is None:
            raise ValueError("Work card does not exist")
        if self.card["status"] == "completed":
            raise ValueError("Completed work must be reopened first")
        card = deepcopy(self.card)
        if goal is not None:
            card["goal"] = _text(goal, GOAL_LIMIT)
            if not card["goal"]:
                raise ValueError("Work goal is required")
        if step is not None:
            card["step"] = _text(step, 2000)
        if next_action is not None:
            card["next_action"] = _text(next_action, 2000)
        if blockers is not None:
            if not isinstance(blockers, list) or len(blockers) > 20:
                raise ValueError("Invalid blockers")
            card["blockers"] = [_text(item, 500) for item in blockers]
        if evidence_event_ids is not None:
            if (
                not isinstance(evidence_event_ids, list)
                or len(evidence_event_ids) > 50
                or any(type(item) is not int or not 1 <= item <= len(self.journal.events) for item in evidence_event_ids)
            ):
                raise ValueError("Invalid evidence references")
            card["evidence_event_ids"] = list(dict.fromkeys(evidence_event_ids))
        if criteria is not None:
            if not isinstance(criteria, list) or len(criteria) > 20:
                raise ValueError("Invalid work criteria")
            checked = []
            for item in criteria:
                if not isinstance(item, dict) or item.get("kind") not in {"check", "review"}:
                    raise ValueError("Invalid work criterion")
                description = _text(item.get("description"), 500)
                if not description:
                    raise ValueError("Work criterion description is required")
                event_id = item.get("event_id")
                if event_id is not None and (type(event_id) is not int or not 1 <= event_id <= len(self.journal.events)):
                    raise ValueError("Invalid criterion evidence")
                command = item.get("command")
                if command is not None:
                    command = _text(command, 1000)
                checked.append({"description": description, "kind": item["kind"], "event_id": event_id, "command": command})
            card["criteria"] = checked
        card["status"] = "blocked" if card["blockers"] else "active"
        return self._save(card)

    def set_todos(self, todos: list[dict]) -> dict:
        if self.card is None or self.card["status"] == "completed":
            raise ValueError("No active work card")
        if len(todos) > 50:
            raise ValueError("Too many Todo items")
        card = deepcopy(self.card)
        card["todos"] = deepcopy(todos)
        return self._save(card)

    def replace_goal(self, goal: str) -> dict:
        goal = _text(goal, GOAL_LIMIT)
        if not goal:
            raise ValueError("Work goal is required")
        return self._save(
            {
                "goal": goal,
                "criteria": [],
                "step": "",
                "todos": [],
                "evidence_event_ids": [],
                "blockers": [],
                "next_action": "",
                "status": "active",
                "archived": False,
            }
        )

    def _check_succeeded(self, event_id: int | None, command: str | None) -> bool:
        if not command or event_id is None or not 1 <= event_id <= len(self.journal.events):
            return False
        event = self.journal.events[event_id - 1]
        if event["kind"] == "note" and event["data"].get("type") == "background_result":
            return self._background_check(event, command)
        if event["kind"] != "message":
            return False
        message = event["data"].get("message", {})
        content = message.get("content")
        if (
            message.get("role") != "tool"
            or not isinstance(content, str)
            or content.startswith(
                (
                    "Error:",
                    "Error executing ",
                    "Error running ",
                    "Permission denied:",
                    "⚠ Blocked:",
                    "[interrupted]",
                    "[result not persisted",
                )
            )
            or "[exit code:" in content
            or event.get("truncated")
        ):
            return False
        call_id = message.get("tool_call_id")
        for previous in reversed(self.journal.events[: event_id - 1]):
            if previous["kind"] != "message":
                continue
            for call in previous["data"].get("message", {}).get("tool_calls", []):
                if call.get("id") == call_id:
                    function = call.get("function", {})
                    if function.get("name") != "bash":
                        return False
                    try:
                        arguments = function.get("arguments", {})
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        return (
                            isinstance(arguments, dict)
                            and arguments.get("command") == command
                            and arguments.get("run_in_background") is not True
                            and not self._background_overlap(previous["seq"], event_id)
                        )
                    except (TypeError, ValueError):
                        return False
        return False

    def _background_overlap(self, check_start: int, check_end: int) -> bool:
        ends = {
            event["data"].get("task_id"): event["seq"]
            for event in self.journal.events
            if event["kind"] == "note" and event["data"].get("type") == "background_result"
        }
        return any(
            event["seq"] <= check_end and ends.get(event["data"].get("task_id"), float("inf")) >= check_start
            for event in self.journal.events
            if event["kind"] == "note" and event["data"].get("type") == "background_start"
        )

    def _background_check(self, event: dict, command: str) -> bool:
        result = event["data"]
        if (
            event.get("truncated")
            or result.get("status") != "completed"
            or result.get("exit_code") != 0
            or result.get("cleanup_verified") is not True
            or result.get("interfered") is not False
            or result.get("command") != command
        ):
            return False
        starts = [
            item
            for item in self.journal.events[: event["seq"] - 1]
            if item["kind"] == "note"
            and item["data"].get("type") == "background_start"
            and item["data"].get("task_id") == result.get("task_id")
        ]
        if len(starts) != 1 or any(starts[0]["data"].get(key) != result.get(key) for key in ("command", "tool_call_id")):
            return False
        call_id = result.get("tool_call_id")
        for item in reversed(self.journal.events[: starts[0]["seq"] - 1]):
            message = item["data"].get("message", {})
            for call in message.get("tool_calls", []):
                if call.get("id") == call_id and call.get("function", {}).get("name") == "bash":
                    args = call["function"].get("arguments", {})
                    try:
                        args = json.loads(args) if isinstance(args, str) else args
                        if (
                            not isinstance(args, dict)
                            or args.get("command") != command
                            or args.get("run_in_background") is not True
                        ):
                            return False
                    except (ValueError, TypeError):
                        return False
                    return any(
                        receipt["data"].get("message", {}).get("tool_call_id") == call_id
                        and receipt["data"].get("message", {}).get("role") == "tool"
                        and self._background_receipt(receipt, result.get("task_id"))
                        for receipt in self.journal.events[item["seq"] : starts[0]["seq"] - 1]
                    )
        return False

    def _check_invocation(self, event_id: int) -> tuple[int, str] | None:
        result = self.journal.events[event_id - 1]["data"]
        call_id = result.get("tool_call_id") or result.get("message", {}).get("tool_call_id")
        cutoff = event_id - 1
        if result.get("type") == "background_result":
            cutoff = next(
                event["seq"] - 1
                for event in self.journal.events[:cutoff]
                if event["kind"] == "note"
                and event["data"].get("type") == "background_start"
                and event["data"].get("task_id") == result.get("task_id")
            )
        for event in reversed(self.journal.events[:cutoff]):
            if any(call.get("id") == call_id for call in event["data"].get("message", {}).get("tool_calls", [])):
                return event["seq"], call_id
        return None

    @staticmethod
    def _background_receipt(event: dict, task_id: str) -> bool:
        try:
            receipt = json.loads(event["data"]["message"]["content"])
            return isinstance(receipt, dict) and receipt.get("task_id") == task_id and receipt.get("status") == "started"
        except (ValueError, TypeError, KeyError):
            return False

    def background_problem(self) -> str:
        if self.background_manager is not None:
            for task_id in list(self.background_manager.tasks):
                result = self.background_manager.status(task_id)
                if result["status"] == "running" or not result["cleanup_verified"]:
                    return "Background command is running or cleanup is unverified"
        pending = set()
        for event in self.journal.events:
            data = event["data"]
            if event["kind"] != "note":
                continue
            if data.get("type") == "background_start":
                pending.add(data.get("task_id"))
            elif data.get("type") == "background_result":
                if data.get("status") == "running" or data.get("cleanup_verified") is not True:
                    return "Background cleanup is unverified"
                pending.discard(data.get("task_id"))
        return "Background command has no persisted terminal result; use a new session and rerun checks" if pending else ""

    def goal_evidence(self, since_event: int) -> tuple[list[int], str]:
        """Read-only, conservative evidence gate for an explicitly set goal."""
        if self.card is None:
            return [], "No work card"
        if problem := self.background_problem():
            return [], problem
        if self.card["blockers"]:
            return [], "Work is blocked: " + ", ".join(self.card["blockers"])
        confirmed = [
            event["seq"]
            for event in self.journal.events
            if event["kind"] == "note"
            and event["data"].get("type") == "user_work_confirmation"
            and event["seq"] >= since_event
            and event["data"].get("revision") <= self.card["revision"]
        ]
        if confirmed:
            ids = [confirmed[-1]]
            accepted_calls = set()
        else:
            if not self.card["criteria"]:
                return [], "No acceptance check recorded; add a check or use /work confirm"
            ids = []
            for item in self.card["criteria"]:
                event_id = item.get("event_id")
                if (
                    item["kind"] != "check"
                    or event_id is None
                    or event_id < since_event
                    or not self._check_succeeded(event_id, item.get("command"))
                ):
                    return [], "Acceptance check is missing, failed, or needs user review"
                ids.append(event_id)
            accepted_calls = {self._check_invocation(event_id) for event_id in ids}
        for event in self.journal.events[min(ids) :]:
            if event["kind"] == "note" and event["data"].get("type") == "background_start":
                return [], "Acceptance check is stale after a background action"
            if event["kind"] != "message":
                continue
            message = event["data"].get("message", {})
            if message.get("role") != "assistant":
                continue
            if any(
                call.get("function", {}).get("name") in {"write_file", "edit_file", "background_cancel", "agent"}
                or (call.get("function", {}).get("name") == "bash" and (event["seq"], call.get("id")) not in accepted_calls)
                for call in message.get("tool_calls", [])
            ):
                return [], "Acceptance check is stale after a file or Bash action"
        return ids, "User explicitly confirmed work" if confirmed else "Acceptance checks passed"

    def complete(self) -> dict:
        if self.card is None:
            raise ValueError("Work card does not exist")
        if self.card["status"] == "completed":
            return deepcopy(self.card)
        card = deepcopy(self.card)
        if card["blockers"]:
            card["status"] = "blocked"
        elif card["criteria"] and self.goal_evidence(1)[0]:
            card["status"] = "completed"
        else:
            card["status"] = "awaiting_review"
        card["archived"] = card["status"] == "completed"
        return self._save(card)

    def confirm(self) -> dict:
        if problem := self.background_problem():
            raise ValueError(problem)
        if self.card is None:
            raise ValueError("Work card does not exist")
        if self.card["status"] == "completed":
            return deepcopy(self.card)
        card = deepcopy(self.card)
        card["status"], card["archived"] = "completed", True
        return self._save(card)

    def reopen(self) -> dict:
        if self.card is None or self.card["status"] != "completed":
            raise ValueError("Work card is not completed")
        card = deepcopy(self.card)
        card["status"], card["archived"] = "active", False
        return self._save(card)

    def summary(self) -> str:
        if self.card is None or self.card["archived"]:
            return ""
        card = self.card
        return (
            f"Goal: {card['goal']}\nStatus: {card['status']}\nStep: {card['step']}\n"
            f"Next: {card['next_action']}\nBlockers: {', '.join(card['blockers']) or 'none'}\n"
            f"Acceptance: {len(card['criteria'])} criteria"
        )[:4000]
