"""Durable, checksummed workflow events and run-scoped process locks."""

import hashlib
import json
import os
import uuid
from pathlib import Path

from .session_journal import _redact
from .state_lock import StateLock


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    if path.is_symlink():
        raise ValueError("Workflow artifact is a symlink")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class WorkflowJournal:
    def __init__(self, root: Path, run_id: str):
        if not isinstance(run_id, str) or uuid.UUID(run_id).hex != run_id:
            raise ValueError("Invalid workflow run ID")
        root = Path(root).absolute()
        if root.resolve() != root:
            raise ValueError("Workflow root contains a symlink")
        self.directory = root / run_id
        if self.directory.is_symlink() or self.directory.resolve() != self.directory:
            raise ValueError("Workflow directory escapes root")
        self.run_id = run_id
        self.events_path = self.directory / "events.jsonl"
        self.lock = StateLock(lambda: self.directory / "run.lock")
        self.events: list[dict] = []
        self.completed_steps: dict[str, dict] = {}

    @classmethod
    def create(cls, root: Path, identity: dict):
        journal = cls(root, uuid.uuid4().hex)
        journal.directory.mkdir(parents=True, exist_ok=False)
        atomic_json(journal.directory / "manifest.json", {"identity": identity, "sha256": digest(identity)})
        journal.events_path.touch(exist_ok=False)
        journal.identity = identity
        return journal

    @classmethod
    def open(cls, root: Path, run_id: str):
        journal = cls(root, run_id)
        with journal.lock:
            journal.reload(repair=True)
            journal.snapshot()
        return journal

    def reload(self, *, repair: bool = False) -> None:
        if any((self.directory / name).is_symlink() for name in ("events.jsonl", "manifest.json", "state.json", "result.json")):
            raise ValueError("Workflow artifacts cannot be symlinks")
        manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
        if digest(manifest["identity"]) != manifest["sha256"]:
            raise ValueError("Invalid workflow manifest")
        self.identity = manifest["identity"]
        raw = self.events_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            if not repair:
                raise ValueError("Incomplete workflow journal")
            offset = raw.rfind(b"\n") + 1
            (self.directory / f"bad-tail-{uuid.uuid4().hex}.bin").write_bytes(raw[offset:])
            with self.events_path.open("r+b") as stream:
                stream.truncate(offset)
                stream.flush()
                os.fsync(stream.fileno())
            raw = raw[:offset]
        events, completed, started = [], {}, set()
        for line in raw.splitlines():
            event = json.loads(line)
            body = {key: event[key] for key in ("seq", "kind", "data", "previous")}
            if (event["seq"] != len(events) + 1 or digest(body) != event["sha256"]
                    or event["previous"] != (events[-1]["sha256"] if events else manifest["sha256"])):
                raise ValueError("Invalid workflow event checksum or sequence")
            kind, data = event["kind"], event["data"]
            if kind == "step_started":
                if data["key"] in completed:
                    raise ValueError("Completed workflow step restarted")
                started.add(data["key"])
            if kind == "step_completed":
                if data["key"] not in started or data["key"] in completed or data["result"].get("status") != "completed":
                    raise ValueError("Invalid completed workflow step")
                if data.get("cacheable", True):
                    completed[data["key"]] = data["result"]
                started.remove(data["key"])
            events.append(event)
        self.events, self.completed_steps = events, completed

    def append(self, kind: str, data: dict) -> int:
        with self.lock:
            self.reload()
            if kind == "step_completed":
                starts = [event for event in self.events if event["kind"] == "step_started" and event["data"]["key"] == data["key"]]
                done = [event for event in self.events if event["kind"] == "step_completed" and event["data"]["key"] == data["key"]]
                if not starts or done or data["result"].get("status") != "completed":
                    raise ValueError("Invalid or duplicate completed workflow step")
            cleaned = _redact(data)
            if kind == "step_completed" and cleaned != data:
                cleaned["cacheable"] = False
            body = {"seq": len(self.events) + 1, "kind": kind, "data": cleaned,
                    "previous": self.events[-1]["sha256"] if self.events else digest(self.identity)}
            event = {**body, "sha256": digest(body)}
            with self.events_path.open("ab") as stream:
                stream.write(encoded(event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.reload()
            self.snapshot()
            return event["seq"]

    def snapshot(self) -> None:
        atomic_json(self.directory / "state.json", {"run_id": self.run_id, "events": len(self.events),
                                                   "status": self.events[-1]["kind"] if self.events else "created"})
