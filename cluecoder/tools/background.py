import json
from typing import ClassVar

from ..background import BackgroundManager
from .base import Tool


class BackgroundStatusTool(Tool):
    name = "background_status"
    description = ("Wait for a background command (10s by default, at most 30s), then read its status. "
                   "Accept only a fresh persisted result_event_id, never a start receipt or this text.")
    parameters: ClassVar = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
                "description": "Wait up to this many seconds; return early on completion. 0 reads immediately; default 10.",
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, manager: BackgroundManager):
        self.manager = manager

    def execute(self, task_id: str, wait_seconds: int = 10) -> str:
        return json.dumps(self.manager.status(task_id, wait_seconds), ensure_ascii=False)


class BackgroundCancelTool(BackgroundStatusTool):
    name = "background_cancel"
    description = "Cancel a session background command and verify container cleanup."
    parameters: ClassVar = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}

    def execute(self, task_id: str) -> str:
        return json.dumps(self.manager.cancel(task_id), ensure_ascii=False)
