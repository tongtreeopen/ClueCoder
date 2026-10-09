"""Shared session checklist with optional work-card persistence callbacks."""

from collections.abc import Callable
from typing import ClassVar

from .base import Tool

_VALID_STATUS = ("pending", "in_progress", "done")


class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "Manage the session task checklist. Pass the complete list every time; it fully "
        "replaces the old one. Use it to plan multi-step work: write the list up front, "
        "keep one task in_progress while you work on it, and mark it done the moment it "
        "finishes. The current list is re-shown in the system context every round."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "The full checklist, in order. Replaces the current list; pass [] to clear it.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "What needs to be done, one line",
                        },
                        "status": {
                            "type": "string",
                            "enum": list(_VALID_STATUS),
                            "description": "pending, in_progress, or done",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["tasks"],
    }

    def __init__(self, on_change: Callable[[list[dict]], object] | None = None):
        self._tasks: list[dict] = []
        self.on_change = on_change

    @staticmethod
    def _checked(tasks: list) -> list[dict]:
        if not isinstance(tasks, list):
            raise ValueError("tasks must be a list of {content, status} objects")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
        checked = []
        for i, t in enumerate(tasks, 1):
            content = t.get("content") if isinstance(t, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"task {i} needs a non-empty 'content' string")
            status = t.get("status")
            if status not in _VALID_STATUS:
                raise ValueError(f"task {i} has invalid status {status!r} (use pending, in_progress, or done)")
            checked.append({"content": content.strip(), "status": status})
        return checked

    def execute(self, tasks: list) -> str:
        try:
            checked = self._checked(tasks)
        except ValueError as error:
            return f"Error: {error}"
        # validate everything before replacing, so a bad call leaves the old list intact
        if self.on_change is not None:
            self.on_change(checked)
        self._tasks = checked
        if not checked:
            return "Task list cleared."
        return "Task list updated:\n" + self.render()

    def restore(self, tasks: list) -> None:
        self._tasks = self._checked(tasks)

    def render(self) -> str:
        """The checklist as text; Agent injects this into the system context."""
        return "\n".join(f"{i}. [{t['status']}] {t['content']}" for i, t in enumerate(self._tasks, 1))
