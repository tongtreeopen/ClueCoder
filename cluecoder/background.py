"""Session-owned Docker commands; workers never write the session journal."""

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

from .execution import DockerExecutor, DockerRun


@dataclass
class BackgroundTask:
    command: str
    run: DockerRun
    tool_call_id: str | None
    interfered: bool = False
    collected: bool = False
    recorded: bool = False
    result_event_id: int | None = None


class BackgroundManager:
    def __init__(self, executor: DockerExecutor):
        self.executor = executor
        # ponytail: retain terminal results until reset; archive if long sessions exhaust memory.
        self.tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.RLock()
        self._local = threading.local()

    @contextmanager
    def call_scope(self, call_id: str):
        previous = getattr(self._local, "call_id", None)
        self._local.call_id = call_id
        try:
            yield
        finally:
            self._local.call_id = previous

    def start(self, command: str, timeout: int = 120) -> str:
        with self._lock:
            if sum(task.run.snapshot()["status"] == "running" for task in self.tasks.values()) >= 4:
                raise ValueError("At most 4 background commands may run")
            self.interfere()
            task_id = uuid.uuid4().hex
            self.tasks[task_id] = BackgroundTask(command, self.executor.start(command, timeout, background=True),
                                                 getattr(self._local, "call_id", None))
            return task_id

    def _task(self, task_id: str) -> BackgroundTask:
        if not isinstance(task_id, str) or task_id not in self.tasks:
            raise ValueError("Unknown background task ID in this session")
        return self.tasks[task_id]

    def status(self, task_id: str, wait_seconds: int = 0) -> dict:
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 30:
            raise ValueError("wait_seconds must be an integer between 0 and 30")
        with self._lock:
            task = self._task(task_id)
        if wait_seconds:
            task.run.wait(wait_seconds)
        with self._lock:
            return {"task_id": task_id, "command": task.command, "tool_call_id": task.tool_call_id,
                    "interfered": task.interfered, "result_event_id": task.result_event_id, **task.run.snapshot()}

    def cancel(self, task_id: str) -> dict:
        with self._lock:
            task = self._task(task_id)
            self.interfere()
        task.run.cancel()
        return self.status(task_id)

    def interfere(self, exclude_call_id: str | None = None) -> None:
        with self._lock:
            for task in self.tasks.values():
                if not task.collected and (exclude_call_id is None or task.tool_call_id != exclude_call_id):
                    task.interfered = True

    def starts(self) -> list[dict]:
        with self._lock:
            return [{"task_id": task_id, "command": task.command, "tool_call_id": task.tool_call_id}
                    for task_id, task in self.tasks.items() if not task.recorded]

    def mark_recorded(self, task_id: str) -> None:
        with self._lock:
            self._task(task_id).recorded = True

    def drain(self) -> list[dict]:
        with self._lock:
            ready = [self.status(task_id) for task_id, task in self.tasks.items()
                     if not task.collected and task.run.snapshot()["status"] != "running"]
            for result in ready:
                self.tasks[result["task_id"]].collected = True
            return ready

    def restore_result(self, task_id: str) -> None:
        with self._lock:
            self._task(task_id).collected = False

    def record_result(self, task_id: str, event_id: int) -> None:
        with self._lock:
            self._task(task_id).result_event_id = event_id

    def close(self) -> list[dict]:
        with self._lock:
            tasks = list(self.tasks.items())
        for _, task in tasks:
            if task.run.snapshot()["status"] == "running":
                task.run.cancel()
        return [self.status(task_id) for task_id, _ in tasks]

    def clear(self) -> None:
        with self._lock:
            if any(task.run.snapshot()["status"] == "running" or not task.run.snapshot()["cleanup_verified"]
                   for task in self.tasks.values()):
                raise ValueError("Background cleanup is not verified")
            self.tasks.clear()

    def context(self) -> str:
        with self._lock:
            return str([self.status(task_id) for task_id in list(self.tasks)[-4:]])[:16000]
