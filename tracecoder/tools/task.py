"""Tools for persistent project tasks."""

import json
from dataclasses import asdict
from typing import ClassVar

from ..tasks import TaskStore
from .base import Tool


class _TaskTool(Tool):
    def __init__(self, store: TaskStore):
        self.store = store


class CreateTaskTool(_TaskTool):
    name = "create_task"
    description = "Create a persistent project task and return its ID. Requires consent."
    parameters: ClassVar = {"type": "object", "properties": {
        "subject": {"type": "string"}, "description": {"type": "string"}},
        "required": ["subject"]}

    def execute(self, subject: str, description: str = "") -> str:
        return json.dumps(asdict(self.store.create(subject, description)), ensure_ascii=False)


class UpdateTaskTool(_TaskTool):
    name = "update_task"
    description = "Add dependencies to a pending project task. Requires consent."
    parameters: ClassVar = {"type": "object", "properties": {
        "task_id": {"type": "string"}, "blocked_by": {"type": "array", "items": {"type": "string"}}},
        "required": ["task_id", "blocked_by"]}

    def execute(self, task_id: str, blocked_by: list[str]) -> str:
        return json.dumps(asdict(self.store.update(task_id, blocked_by)), ensure_ascii=False)


class ListTasksTool(_TaskTool):
    name = "list_tasks"
    description = "List persistent project tasks and their statuses."
    parameters: ClassVar = {"type": "object", "properties": {}}

    def execute(self) -> str:
        return json.dumps([asdict(task) for task in self.store.list()], ensure_ascii=False)


class GetTaskTool(_TaskTool):
    name = "get_task"
    description = "Read one persistent project task by ID."
    parameters: ClassVar = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}

    def execute(self, task_id: str) -> str:
        return json.dumps(asdict(self.store.get(task_id)), ensure_ascii=False)


class ClaimTaskTool(_TaskTool):
    name = "claim_task"
    description = "Claim a pending task once all dependencies are completed. Requires consent."
    parameters: ClassVar = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}

    def execute(self, task_id: str) -> str:
        return json.dumps(asdict(self.store.claim(task_id)), ensure_ascii=False)


class CompleteTaskTool(_TaskTool):
    name = "complete_task"
    description = "Complete a task claimed by this TraceCoder instance. Requires consent."
    parameters: ClassVar = {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}

    def execute(self, task_id: str) -> str:
        return json.dumps(asdict(self.store.complete(task_id)), ensure_ascii=False)
