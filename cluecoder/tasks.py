"""Project-local persistent tasks."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .state_lock import StateLock

TASK_ID = re.compile(r"task_[0-9a-f]{8}\Z")


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str = "pending"
    owner: str | None = None
    blocked_by: list[str] = field(default_factory=list)


class TaskStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.directory = self.workspace / ".cluecoder" / "tasks"
        # ponytail: serialize the small project store; split locks only if contention matters.
        self._lock = StateLock(lambda: self._root(create=True) / ".store.lock")

    def _root(self, create: bool = False) -> Path:
        parent = self.workspace / ".cluecoder"
        if parent.resolve() != parent.absolute() or self.directory.resolve() != self.directory.absolute():
            raise ValueError("Task directory escapes workspace")
        if create:
            parent.mkdir(exist_ok=True)
            if parent.resolve() != parent.absolute():
                raise ValueError("Task directory escapes workspace")
            self.directory.mkdir(exist_ok=True)
        if self.directory.resolve() != self.directory.absolute():
            raise ValueError("Task directory escapes workspace")
        return self.directory

    def _path(self, task_id: str, create: bool = False) -> Path:
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise ValueError("Invalid task ID")
        path = self._root(create) / f"{task_id}.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Task file escapes directory")
        return path

    def create(self, subject: str, description: str = "") -> Task:
        if not isinstance(subject, str) or not subject.strip() or not isinstance(description, str):
            raise ValueError("Task subject is required and description must be text")
        with self._lock:
            self._root(create=True)
            for _ in range(10):
                task = Task(id=f"task_{secrets.token_hex(4)}", subject=subject.strip(), description=description)
                path = self._path(task.id)
                try:
                    with path.open("x", encoding="utf-8") as stream:
                        json.dump(asdict(task), stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    return task
                except FileExistsError:
                    continue
            raise RuntimeError("Cannot allocate unique task ID")

    def get(self, task_id: str) -> Task:
        if not self._path(task_id).exists():
            raise FileNotFoundError(f"Unknown task {task_id}")
        with self._lock:
            data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("id") != task_id:
                raise ValueError("Task ID does not match file")
            if set(data) != {"id", "subject", "description", "status", "owner", "blocked_by"}:
                raise ValueError("Invalid task fields")
            if not isinstance(data["subject"], str) or not data["subject"].strip():
                raise ValueError("Invalid task subject")
            if (not isinstance(data["description"], str) or not isinstance(data["status"], str)
                    or data["status"] not in {"pending", "in_progress", "completed"}):
                raise ValueError("Invalid task description or status")
            if data["owner"] is not None and not isinstance(data["owner"], str):
                raise ValueError("Invalid task owner")
            if not isinstance(data["blocked_by"], list) or not all(
                isinstance(dep, str) and TASK_ID.fullmatch(dep) for dep in data["blocked_by"]
            ):
                raise ValueError("Invalid task dependencies")
            return Task(**data)

    def list(self, *, strict: bool = False) -> list[Task]:
        if not self._root().exists():
            return []
        with self._lock:
            root = self._root()
            if not root.exists():
                return []
            tasks = []
            for path in sorted(root.glob("task_*.json")):
                if TASK_ID.fullmatch(path.stem):
                    try:
                        tasks.append(self.get(path.stem))
                    except (OSError, UnicodeError, ValueError) as error:
                        if strict:
                            raise ValueError(f"Cannot load task {path.stem}") from error
                        logging.getLogger(__name__).warning("Cannot load task %s: %s", path.stem, type(error).__name__)
            return tasks

    def _replace(self, task: Task) -> None:
        path = self._path(task.id)
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(asdict(task), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)

    def update(self, task_id: str, blocked_by: list[str]) -> Task:
        if not isinstance(blocked_by, list) or not all(
            isinstance(dep, str) and TASK_ID.fullmatch(dep) for dep in blocked_by
        ):
            raise ValueError("Invalid task dependency list")
        with self._lock:
            task = self.get(task_id)
            if task.status != "pending":
                raise ValueError("Only pending tasks can be updated")
            proposed = list(dict.fromkeys([*task.blocked_by, *blocked_by]))
            for dep in proposed:
                if dep == task_id:
                    raise ValueError("Task dependency cycle")
                try:
                    self.get(dep)
                except (FileNotFoundError, ValueError) as error:
                    raise ValueError(f"Unknown dependency {dep}") from error
            graph = {item.id: item.blocked_by for item in self.list(strict=True)}
            graph[task_id] = proposed
            active: set[str] = set()
            visited: set[str] = set()

            def visit(node: str) -> None:
                if node in active:
                    raise ValueError("Task dependency cycle")
                if node in visited:
                    return
                if node not in graph:
                    raise ValueError(f"Unknown dependency {node}")
                active.add(node)
                for dep in graph[node]:
                    visit(dep)
                active.remove(node)
                visited.add(node)

            for node in graph:
                visit(node)
            task.blocked_by = proposed
            self._replace(task)
            return task

    def claim(self, task_id: str, owner: str = "cluecoder") -> Task:
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("Invalid task owner")
        with self._lock:
            task = self.get(task_id)
            if task.status != "pending":
                raise ValueError(f"Task {task_id} is {task.status}")
            if any(self.get(dep).status != "completed" for dep in task.blocked_by):
                raise ValueError(f"Task {task_id} is blocked")
            task.status, task.owner = "in_progress", owner
            self._replace(task)
            return task

    def complete(self, task_id: str, owner: str = "cluecoder") -> Task:
        with self._lock:
            task = self.get(task_id)
            if task.status != "in_progress":
                raise ValueError(f"Task {task_id} is {task.status}, not in_progress")
            if task.owner != owner:
                raise ValueError(f"Task {task_id} has a different owner")
            task.status = "completed"
            self._replace(task)
            return task
