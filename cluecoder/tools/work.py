"""Session-only work card updates; completion is evidence-gated."""

import json
from typing import ClassVar

from ..work_state import WorkStateStore
from .base import Tool


class WorkUpdateTool(Tool):
    name = "work_update"
    description = (
        "Update the current session work card. This does not edit project files. "
        "Set complete=true only when acceptance evidence is available; otherwise the card waits for review."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"}, "step": {"type": "string"}, "next_action": {"type": "string"},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "criteria": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"}, "kind": {"type": "string", "enum": ["check", "review"]},
                "event_id": {"type": "integer"}, "command": {"type": "string"},
            }, "required": ["description", "kind"]}},
            "evidence_event_ids": {"type": "array", "items": {"type": "integer"}},
            "complete": {"type": "boolean"},
        },
    }

    def __init__(self, store: WorkStateStore):
        self.store = store

    def execute(self, goal=None, step=None, next_action=None, blockers=None, criteria=None,
                evidence_event_ids=None, complete: bool = False) -> str:
        self.store.update(goal=goal, step=step, next_action=next_action, blockers=blockers,
                          criteria=criteria, evidence_event_ids=evidence_event_ids)
        if complete:
            self.store.complete()
        return self.store.summary() or "Work card completed and archived"


class ReadWorkStateTool(Tool):
    name = "read_work_state"
    description = "Read the current session work card and its acceptance evidence references."
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, store: WorkStateStore):
        self.store = store

    def execute(self) -> str:
        return json.dumps(self.store.card, ensure_ascii=False)[:8000] if self.store.card else "(no work card)"
