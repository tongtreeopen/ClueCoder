"""Explicit, project-local memories with a small prompt index."""

import base64
import binascii
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .skills import parse_frontmatter
from .state_lock import StateLock

MEMORY_TYPES = frozenset({"user", "feedback", "project", "reference"})


def bounded_index(text: str, limit: int) -> str:
    notice = "\n[index shortened; use load_skill/list_memories/search_memory for remaining entries]"
    return text if len(text) <= limit else text[:limit - len(notice)].rpartition("\n")[0] + notice


def validate_memory_name(name: str) -> str:
    checked = " ".join(name.split()) if isinstance(name, str) else ""
    if not 1 <= len(checked) <= 80 or not all(ch.isalnum() or ch in " -_" for ch in checked):
        raise ValueError("Invalid memory name")
    return checked


class MemoryStore:
    def __init__(self, workspace: Path, scope: str = "project", user_root: Path | None = None):
        if scope not in {"project", "user"}:
            raise ValueError("Invalid memory scope")
        self.workspace = workspace.resolve()
        self.scope = scope
        self.directory = (self.workspace / ".cluecoder" / "memory" if scope == "project" else
                          (user_root or Path.home() / ".cluecoder" / "memory").absolute())
        self._lock = StateLock(lambda: self._root(create=True) / ".memory.lock")

    def _root(self, create: bool = False) -> Path:
        parent = self.directory.parent
        if parent.resolve() != parent.absolute() or self.directory.resolve() != self.directory.absolute():
            raise ValueError("Memory store escapes workspace")
        if create:
            parent.mkdir(parents=True, exist_ok=True)
            if parent.resolve() != parent.absolute():
                raise ValueError("Memory store escapes workspace")
            self.directory.mkdir(exist_ok=True)
        if self.directory.resolve() != self.directory.absolute():
            raise ValueError("Memory store escapes workspace")
        return self.directory

    def _path(self, name: str, create: bool = False) -> Path:
        checked = validate_memory_name(name)
        digest = hashlib.sha256(checked.casefold().encode("utf-8")).hexdigest()[:16]
        path = self._root(create) / f"{digest}.md"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Memory file escapes store")
        return path

    def _index_path(self, create: bool = False) -> Path:
        path = self._root(create) / "MEMORY.md"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Memory index escapes store")
        return path

    def _record(self, path: Path) -> tuple[str, str, str]:
        if path.is_symlink() or not path.resolve().is_relative_to(self._root().resolve()):
            raise ValueError("Memory file escapes store")
        content = path.read_text(encoding="utf-8")
        metadata, _ = parse_frontmatter(content)
        name = validate_memory_name(metadata.get("name"))
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Invalid memory description")
        if path.name != hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:16] + ".md":
            raise ValueError("Memory name does not match file")
        return name, description, content

    def _records(self) -> list[tuple[str, str, str]]:
        root = self._root()
        if not root.exists():
            return []
        records = []
        for path in sorted(root.glob("[0-9a-f]" * 16 + ".md")):
            try:
                records.append(self._record(path))
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                logging.getLogger(__name__).warning("Cannot load memory %s: %s", path, error)
        return records

    def _rebuild_index(self) -> None:
        index = self._index_path(create=True)
        text = self.catalog()
        self._write_bytes(index, text.encode("utf-8"))

    def records(self) -> list[dict]:
        result = []
        for name, description, content in self._records():
            metadata, body = parse_frontmatter(content)
            result.append({
                "name": name, "description": description,
                "type": metadata.get("type") if metadata.get("type") in MEMORY_TYPES else None,
                "content": body.strip(), "hash": self.hash_of(name),
                "scope": metadata.get("scope", self.scope),
                "source_session_id": metadata.get("source_session_id"),
                "source_event_ids": metadata.get("source_event_ids", []),
                "version": metadata.get("version", 1),
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
                "write_mode": metadata.get("write_mode", "legacy"),
            })
        return result

    def hash_of(self, name: str) -> str | None:
        path = self._path(name)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def version(self) -> str:
        items = sorted((record["name"], record["hash"]) for record in self.records())
        return hashlib.sha256(json.dumps(items, ensure_ascii=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _hash(data: bytes | None) -> str | None:
        return hashlib.sha256(data).hexdigest() if data is not None else None

    @staticmethod
    def _encode(data: bytes | None) -> str | None:
        return base64.b64encode(data).decode("ascii") if data is not None else None

    @staticmethod
    def _decode(data: str | None) -> bytes | None:
        return base64.b64decode(data, validate=True) if data is not None else None

    def _document(self, name: str, description: str, content: str, mem_type: str,
                  provenance: dict | None = None, previous: bytes | None = None,
                  mode: str = "explicit") -> bytes:
        checked = validate_memory_name(name)
        if mem_type not in MEMORY_TYPES:
            raise ValueError("Invalid memory type")
        if not isinstance(description, str) or not description.strip() or not isinstance(content, str) or not content.strip():
            raise ValueError("Memory description and content are required")
        if len(content.encode("utf-8")) > 16 * 1024:
            raise ValueError("Memory content exceeds 16 KiB")
        old = parse_frontmatter(previous.decode("utf-8"))[0] if previous else {}
        now = datetime.now(timezone.utc).isoformat()
        version = old.get("version", 0)
        if type(version) is not int or version < 0:
            raise ValueError("Invalid memory version")
        metadata = {"name": checked, "description": description.strip(), "type": mem_type, "scope": self.scope,
                    "created_at": old.get("created_at", now), "updated_at": now,
                    "version": version + 1, "write_mode": mode}
        if provenance is not None:
            if (not isinstance(provenance, dict) or not isinstance(provenance.get("session_id"), str)
                    or not isinstance(provenance.get("event_ids"), list)
                    or not all(type(item) is int and item > 0 for item in provenance["event_ids"])):
                raise ValueError("Invalid memory provenance")
            metadata.update({"source_session_id": provenance["session_id"],
                             "source_event_ids": provenance["event_ids"]})
        return ("---\n" + yaml.safe_dump(
            metadata,
            allow_unicode=True, sort_keys=False,
        ) + "---\n\n" + content.strip() + "\n").encode("utf-8")

    def _write_bytes(self, path: Path, data: bytes | None) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(self._root().resolve()):
            raise ValueError("Memory file escapes store")
        if data is None:
            if path.exists():
                path.unlink()
            return
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)

    def _write_operation(self, path: Path, operation: dict) -> None:
        if path.is_symlink() or not path.resolve().is_relative_to(self._root().resolve()):
            raise ValueError("Memory journal escapes store")
        self._write_bytes(path, json.dumps(operation, ensure_ascii=False).encode("utf-8"))

    def _operations(self) -> list[Path]:
        root = self._root()
        if not root.exists():
            return []
        paths = sorted(root.glob("op_*.json"))
        if any(path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in paths):
            raise ValueError("Memory journal escapes store")
        return paths

    def _load_operation(self, path: Path) -> dict:
        operation = json.loads(path.read_text(encoding="utf-8"))
        if operation.get("status") not in {"pending", "committed", "undo_pending", "undone", "aborted"}:
            raise ValueError("Invalid memory journal status")
        if not isinstance(operation.get("targets"), list) or not operation["targets"]:
            raise ValueError("Invalid memory journal targets")
        for target in operation["targets"]:
            if not isinstance(target, dict) or not isinstance(target.get("name"), str):
                raise ValueError("Invalid memory journal target")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
            validate_memory_name(target["name"])
            for side in ("before", "after"):
                encoded = target.get(side)
                if encoded is not None and not isinstance(encoded, str):
                    raise ValueError("Invalid memory journal content")
                try:
                    data = self._decode(encoded)
                except (binascii.Error, ValueError) as error:
                    raise ValueError("Invalid memory journal content") from error
                if self._hash(data) != target.get(f"{side}_hash"):
                    raise ValueError("Invalid memory journal hash")
        return operation

    def _restore(self, operation: dict, side: str, exact: bool = False) -> None:
        targets = operation["targets"]
        for target in targets:
            actual = self.hash_of(target["name"])
            allowed = {target["after_hash"]} if exact else {target["before_hash"], target["after_hash"]}
            if actual not in allowed:
                raise ValueError("memory changed: journal conflict")
        for target in targets:
            self._write_bytes(self._path(target["name"], create=True), self._decode(target[side]))
        self._rebuild_index()

    def recover_pending(self) -> int:
        if not self._root().exists():
            return 0
        with self._lock:
            recovered = 0
            for path in self._operations():
                operation = self._load_operation(path)
                if operation["status"] == "pending":
                    self._restore(operation, "before")
                    operation["status"] = "aborted"
                elif operation["status"] == "undo_pending":
                    self._restore(operation, "after")
                    operation["status"] = "committed"
                else:
                    continue
                self._write_operation(path, operation)
                recovered += 1
            return recovered

    def apply_changes(
        self, changes: dict[str, tuple[str, str, str] | None], expected: dict[str, str | None], label: str,
        source: str = "api", mode: str = "explicit", provenance: dict[str, dict] | None = None,
    ) -> str:
        if not changes or set(changes) != set(expected):
            raise ValueError("Memory changes and expected versions must match")
        names = {name: validate_memory_name(name) for name in changes}
        if len({name.casefold() for name in names.values()}) != len(changes):
            raise ValueError("Duplicate memory name")
        for name, value in changes.items():
            if value is not None:
                self._document(names[name], *value, (provenance or {}).get(name), mode=mode)
        versions = {names[name]: version for name, version in expected.items()}
        with self._lock:
            self.recover_pending()
            self._root(create=True)
            self._index_path()
            targets = []
            for original_name, value in changes.items():
                name = names[original_name]
                path = self._path(name)
                before = path.read_bytes() if path.exists() else None
                if self._hash(before) != versions[name]:
                    raise ValueError(f"memory changed: {name}")
                after = (self._document(name, *value, (provenance or {}).get(original_name), before, mode)
                         if value is not None else None)
                targets.append({
                    "name": name, "before": self._encode(before), "after": self._encode(after),
                    "before_hash": self._hash(before), "after_hash": self._hash(after),
                })
            path = self._root() / f"op_{time.time_ns():020}_{uuid.uuid4().hex}.json"
            operation = {"status": "pending", "label": label, "source": source, "mode": mode,
                         "scope": self.scope, "provenance": provenance or {}, "targets": targets}
            self._write_operation(path, operation)
            for target in targets:
                self._write_bytes(self._path(target["name"]), self._decode(target["after"]))
            self._rebuild_index()
            operation["status"] = "committed"
            self._write_operation(path, operation)
        return f"Saved {label}"

    def save_candidate(self, name: str, description: str, content: str, mem_type: str, replace: bool = False) -> str:
        expected = self.hash_of(name)
        if expected is not None and not replace:
            raise ValueError("Memory already exists; confirmation required to replace")
        self.apply_changes({name: (description, content, mem_type)}, {name: expected}, f"memory {name}")
        return f"Saved memory {name}"

    def undo_last(self, operation_path: Path | None = None) -> str:
        try:
            with self._lock:
                self.recover_pending()
                for path in reversed(self._operations()):
                    if operation_path is not None and path != operation_path:
                        continue
                    operation = self._load_operation(path)
                    if operation["status"] != "committed":
                        continue
                    for target in operation["targets"]:
                        if self.hash_of(target["name"]) != target["after_hash"]:
                            raise ValueError("memory changed: cannot undo")
                    operation["status"] = "undo_pending"
                    self._write_operation(path, operation)
                    self._restore(operation, "before", exact=True)
                    operation["status"] = "undone"
                    self._write_operation(path, operation)
                    return f"Undid {operation['label']}"
                return "No memory operation to undo"
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            return f"Error: {error}"

    def catalog(self) -> str:
        return "\n".join(f"- {name}: {' '.join(description.split())}" for name, description, _ in self._records())

    def index(self) -> str:
        path = self._index_path()
        return path.read_text(encoding="utf-8") if path.exists() else self.catalog()

    def list(self) -> str:
        return self.catalog() or "(no memories found)"

    def read(self, name: str) -> str:
        try:
            path = self._path(name)
            if not path.exists():
                return f"Error: unknown memory {name!r}"
            return self._record(path)[2]
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
            return f"Error: {error}"

    def remember(self, name: str, description: str, content: str, replace: bool = False) -> str:
        try:
            if not isinstance(replace, bool):
                raise ValueError("Memory replace must be boolean")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
            with self._lock:
                self.recover_pending()
                return self.save_candidate(name, description, content, "project", replace=replace)
        except ValueError as error:
            return f"Error: {error}"
