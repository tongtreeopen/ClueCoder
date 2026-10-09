"""Tools for trusted workflow execution and read-only run status."""

import json
from pathlib import Path
from typing import ClassVar

from .base import Tool
from ..workflow import WorkflowRuntime


class WorkflowTool(Tool):
    name = "workflow"
    description = "Run a trusted read-only review-changes workflow or resume its run_id. Cached results are not test evidence."
    parameters: ClassVar[dict] = {"type": "object", "properties": {
        "name": {"type": "string", "enum": ["review-changes"]}, "args": {"type": "object", "additionalProperties": False},
        "resume_from_run_id": {"type": "string"}}, "required": ["name"], "additionalProperties": False}

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._parent_agent = None

    def execute(self, name: str, args: dict | None = None, resume_from_run_id: str | None = None) -> str:
        parent = self._parent_agent
        if parent is None:
            return "Error: workflow tool has no parent Agent"
        if parent.plan_mode:
            return "Error: Plan Mode prohibits workflow execution or resume"
        try:
            goal = parent.goal_controller
            report = WorkflowRuntime(self.workspace, parent.llm,
                                     goal=goal if goal and goal.status in {"active", "limit"} else None,
                                     on_usage=parent.on_usage).run(name, args, resume_from_run_id)
            work = getattr(parent, "work_state", None)
            if work is not None:
                work.journal.append_event("note", {"type": "workflow_run", "run_id": report["run_id"],
                                                   "status": report["status"], "artifacts": report["artifacts"]})
            return json.dumps(report, ensure_ascii=False)
        except (ValueError, OSError) as error:
            return json.dumps({"status": "error", "blockers": [str(error)]}, ensure_ascii=False)


class WorkflowStatusTool(Tool):
    name = "workflow_status"
    description = "Read workflow run state and source references without model calls."
    parameters: ClassVar[dict] = {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._parent_agent = None

    def execute(self, run_id: str) -> str:
        try:
            return json.dumps(WorkflowRuntime(self.workspace, self._parent_agent.llm).status(run_id), ensure_ascii=False)
        except (ValueError, OSError) as error:
            return json.dumps({"status": "error", "blockers": [str(error)]}, ensure_ascii=False)
