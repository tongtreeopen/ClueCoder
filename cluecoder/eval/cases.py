"""Validated evaluation manifests and fresh task snapshots."""

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class TaskSpec:
    id: str
    prompt: str
    snapshot_dir: Path
    snapshot_sha256: str
    gate_dir: Path
    gate_command: list[str]
    allowed_paths: list[str]


@dataclass(frozen=True)
class SuiteSpec:
    id: str
    tasks: list[TaskSpec]


def _relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Invalid relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..", "") for part in value.split("/")) or ":" in value:
        raise ValueError(f"Invalid relative path: {value!r}")
    return path


def _directory(root: Path, value: str) -> Path:
    path = root.joinpath(*_relative(value).parts)
    if not path.is_dir() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Directory outside suite or missing: {value!r}")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"Symlink directory: {value!r}")
        current = current.parent
    return path


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"Symlink in evaluation tree: {path}")
        if path.is_file():
            name = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
        elif not path.is_dir():
            raise ValueError(f"Unsupported file in evaluation tree: {path}")
    return digest.hexdigest()


def load_suite(path: Path) -> SuiteSpec:
    if path.is_symlink():
        raise ValueError("Suite manifest cannot be a symlink")
    root = path.parent.resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"id", "tasks"}:
        raise ValueError("Invalid suite fields")
    if not isinstance(data["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", data["id"]):
        raise ValueError("Invalid suite ID")
    if not isinstance(data["tasks"], list) or not data["tasks"]:
        raise ValueError("Suite must contain tasks")
    tasks = []
    seen = set()
    fields = {"id", "prompt", "snapshot_dir", "snapshot_sha256", "gate_dir", "gate_command", "allowed_paths"}
    for item in data["tasks"]:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("Invalid task fields")
        task_id = item["id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task_id) or task_id in seen:
            raise ValueError(f"Invalid or duplicate task ID: {task_id!r}")
        seen.add(task_id)
        if not isinstance(item["prompt"], str) or not item["prompt"].strip():
            raise ValueError(f"Invalid prompt for {task_id}")
        expected = item["snapshot_sha256"]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Invalid snapshot hash for {task_id}")
        command = item["gate_command"]
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError(f"Invalid Gate command for {task_id}")
        allowed = item["allowed_paths"]
        if not isinstance(allowed, list) or not allowed or any(not isinstance(value, str) for value in allowed):
            raise ValueError(f"Invalid allowed paths for {task_id}")
        for value in allowed:
            _relative(value)
        if len(allowed) != len(set(allowed)):
            raise ValueError(f"Duplicate allowed path for {task_id}")
        snapshot = _directory(root, item["snapshot_dir"])
        gate = _directory(root, item["gate_dir"])
        if gate.is_relative_to(snapshot) or snapshot.is_relative_to(gate):
            raise ValueError(f"Gate overlaps visible snapshot for {task_id}")
        if _tree_hash(snapshot) != expected:
            raise ValueError(f"Snapshot hash mismatch for {task_id}")
        _tree_hash(gate)
        tasks.append(TaskSpec(task_id, item["prompt"], snapshot, expected, gate, command, allowed))
    return SuiteSpec(data["id"], tasks)


def materialize(task: TaskSpec, destination: Path) -> str:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not task.snapshot_dir.is_dir() or task.snapshot_dir.resolve() != task.snapshot_dir:
        raise ValueError(f"Snapshot path changed for {task.id}")
    if destination.resolve().is_relative_to(task.snapshot_dir.resolve()) or destination.resolve().is_relative_to(
        task.gate_dir.resolve()
    ):
        raise ValueError("Destination overlaps evaluation inputs")
    if _tree_hash(task.snapshot_dir) != task.snapshot_sha256:
        raise ValueError(f"Snapshot hash mismatch for {task.id}")
    shutil.copytree(task.snapshot_dir, destination, symlinks=True)
    copied_hash = _tree_hash(destination)
    if copied_hash != task.snapshot_sha256:
        raise ValueError(f"Copied snapshot hash mismatch for {task.id}")
    return copied_hash
