"""Durable session events, separate from the model's compacted view."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .state_lock import StateLock

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_SECRET = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{12,}|AKIA[A-Z0-9]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|https?://[^\s/@:]+:[^\s/@]+@[^\s/]+|"
    r"(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*\S{8,})"
)
_MAX_EVENT_BYTES = 65536


def _encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: dict) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _redact(value):
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED_SECRET]", value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


class SessionJournal:
    def __init__(self, root: Path, session_id: str, meta: dict, events: list[dict] | None = None):
        if not isinstance(session_id, str) or not _SAFE_ID.fullmatch(session_id) or session_id in {".", ".."}:
            raise ValueError("Invalid session id")
        self.root = root.resolve()
        self.id = session_id
        self.directory = self.root / session_id
        if self.directory.is_symlink() or not self.directory.resolve().is_relative_to(self.root):
            raise ValueError("Session path escapes root")
        self.events_path = self.directory / "events.jsonl"
        self.meta_path = self.directory / "meta.json"
        self.meta = meta
        self.events = events if events is not None else []
        self._write_lock = StateLock(self._lock_path)
        self._event_bytes = 0
        self.warnings: list[str] = []
        self.repair = True

    def _lock_path(self) -> Path:
        self._check_paths()
        return self.directory / ".journal.lock"

    @property
    def model(self) -> str:
        for event in reversed(self.events):
            if event["kind"] == "model":
                return event["data"]["model"]
        return self.meta["model"]

    @property
    def workspace(self) -> str:
        for event in reversed(self.events):
            if event["kind"] == "note" and event["data"].get("type") == "workspace_rebind":
                return event["data"]["workspace"]
        return self.meta["workspace"]

    @classmethod
    def create(cls, root: Path, workspace: Path, model: str, session_id: str | None = None) -> SessionJournal:
        if not isinstance(model, str) or not model:
            raise ValueError("Invalid model")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        session_id = session_id or f"session_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        meta = {"version": 1, "id": session_id, "workspace": str(workspace.resolve()), "model": model}
        journal = cls(root, session_id, meta)
        journal.directory.mkdir(exist_ok=False)
        temp = journal.meta_path.with_suffix(".tmp")
        with temp.open("wb") as stream:
            stream.write(_encoded(meta))
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(journal.meta_path)
        journal.events_path.touch(exist_ok=False)
        return journal

    @classmethod
    def open(cls, root: Path, session_id: str, workspace: Path | None, allow_workspace_change: bool = False,
             repair: bool = True) -> SessionJournal:
        journal = cls(root, session_id, {})
        journal._check_paths()
        meta = json.loads(journal.meta_path.read_text(encoding="utf-8"))
        if meta.get("version") != 1 or meta.get("id") != session_id or not isinstance(meta.get("model"), str):
            raise ValueError("Invalid session metadata")
        if not isinstance(meta.get("workspace"), str):
            raise ValueError("Invalid session workspace")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
        journal.meta = meta
        journal.repair = repair
        journal._read_events(repair)
        if workspace is not None and os.path.normcase(journal.workspace) != os.path.normcase(str(workspace.resolve())):
            if not allow_workspace_change:
                raise ValueError("Session belongs to a different workspace")
            journal.append_event("note", {"type": "workspace_rebind", "workspace": str(workspace.resolve())})
        if repair:
            journal._repair_pending_tools()
        return journal

    def _check_paths(self) -> None:
        if (self.directory.is_symlink() or self.events_path.is_symlink() or self.meta_path.is_symlink()
                or not self.directory.resolve().is_relative_to(self.root)
                or not self.events_path.resolve().is_relative_to(self.directory.resolve())
                or not self.meta_path.resolve().is_relative_to(self.directory.resolve())):
            raise ValueError("Session path escapes root")

    def _read_events(self, repair: bool) -> None:
        with self._write_lock:
            self._read_locked(repair)

    def _read_locked(self, repair: bool) -> None:
        raw = self.events_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        offset = 0
        for index, line in enumerate(lines):
            try:
                if not line.endswith(b"\n"):
                    raise json.JSONDecodeError("partial event", "", 0)
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if index != len(lines) - 1:
                    raise ValueError("Invalid session event before tail") from None
                if repair:
                    self._archive_bad_tail(raw[offset:])
                else:
                    self.warnings.append("Incomplete session tail")
                break
            if not isinstance(event, dict):
                raise ValueError("Invalid session event")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
            checksum = event.pop("checksum", None)
            if checksum != _digest(event):
                raise ValueError("Session event checksum mismatch")
            event["checksum"] = checksum
            if (event.get("seq") != len(self.events) + 1 or event.get("kind") not in {"message", "model", "note"}
                    or not isinstance(event.get("data"), dict)):
                raise ValueError("Invalid session event sequence or kind")
            self.events.append(event)
            offset += len(line)
        self._event_bytes = offset

    def _archive_bad_tail(self, bad: bytes) -> None:
        backup = self.directory / f"events.corrupt-{uuid.uuid4().hex}.bin"
        backup.write_bytes(bad)
        with self.events_path.open("r+b") as stream:
            stream.truncate(self.events_path.stat().st_size - len(bad))
            stream.flush()
            os.fsync(stream.fileno())
        self.warnings.append(f"Incomplete session tail preserved in {backup.name}")

    def append_event(self, kind: str, data: dict) -> int:
        if kind not in {"message", "model", "note"} or not isinstance(data, dict):
            raise ValueError("Invalid session event")
        with self._write_lock:
            self._check_paths()
            if self.events_path.stat().st_size != self._event_bytes:
                raise ValueError("Session changed by another writer; reopen before appending")
            original = _encoded(data)
            cleaned = _redact(data)
            redacted = cleaned != data
            truncated = False
            if kind == "message":
                message = cleaned.get("message")
                if not isinstance(message, dict):
                    raise ValueError("Invalid session message")
                content = message.get("content")
                if (message.get("role") == "tool" and isinstance(content, str)
                        and (len(content) > 30000 or len(_encoded(cleaned)) > _MAX_EVENT_BYTES)):
                    cleaned = self._archive_tool_result(cleaned, message)
                elif len(_encoded(cleaned)) > _MAX_EVENT_BYTES and isinstance(content, str):
                    message = dict(message)
                    message["content"] = content[:32000] + "\n[truncated in session journal]"
                    cleaned = {**cleaned, "message": message, "original_sha256": hashlib.sha256(original).hexdigest()}
                    truncated = True
                elif len(_encoded(cleaned)) > _MAX_EVENT_BYTES:
                    raise ValueError("Session event too large")
            event = {
                "seq": len(self.events) + 1, "kind": kind, "time": time.time(), "data": cleaned,
                "redacted": redacted, "truncated": truncated,
            }
            if len(_encoded(event)) > _MAX_EVENT_BYTES:
                raise ValueError("Session event too large")
            event["checksum"] = _digest(event)
            payload = _encoded(event) + b"\n"
            with self.events_path.open("ab") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self.events.append(event)
            self._event_bytes += len(payload)
            return event["seq"]

    def _archive_tool_result(self, data: dict, message: dict) -> dict:
        content = message["content"]
        relative, digest, byte_count = self._write_archive(len(self.events) + 1, content)
        archived = dict(message)
        archived["content"] = content[:2000] + f"\n[full result archived: event:{len(self.events) + 1}]"
        archived["archive_ref"] = relative
        archived["archive_sha256"] = digest
        archived["archive_bytes"] = byte_count
        return {**data, "message": archived}

    def _write_archive(self, event_id: int, content: str) -> tuple[str, str, int]:
        payload = _redact(content).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        relative = f"archives/{event_id}-{digest}.txt"
        archive_dir = self.directory / "archives"
        archive_dir.mkdir(exist_ok=True)
        archive = self.directory / relative
        if archive_dir.is_symlink() or not archive_dir.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Session archive path escapes root")
        if archive.is_symlink() or not archive.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Session archive path escapes root")
        if archive.exists():
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise ValueError("Session archive collision")
        else:
            with archive.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        return relative, digest, len(payload)

    def archive_tool_result(self, message: dict) -> None:
        if message.get("archive_ref"):
            return
        call_id = message.get("tool_call_id")
        event = next((item for item in reversed(self.events)
                      if item["kind"] == "message"
                      and item["data"].get("message", {}).get("role") == "tool"
                      and item["data"]["message"].get("tool_call_id") == call_id), None)
        if event is None:
            raise ValueError("Tool result is not present in the session journal")
        source = event["data"]["message"]
        if source.get("archive_ref"):
            metadata = source
        else:
            note = next((item for item in reversed(self.events)
                         if item["kind"] == "note" and item["data"].get("type") == "tool_result_archive"
                         and item["data"].get("source_event_id") == event["seq"]), None)
            if note is not None:
                metadata = note["data"]
            else:
                reference, digest, byte_count = self._write_archive(event["seq"], str(source.get("content") or ""))
                metadata = {"archive_ref": reference, "archive_sha256": digest, "archive_bytes": byte_count}
                self.append_event("note", {"type": "tool_result_archive", "source_event_id": event["seq"], **metadata})
        message.update(metadata)
        message["content"] = str(message.get("content") or "")[:500] + f"\n[full result archived at {metadata['archive_ref']}]"

    def read_archive(self, event_id: int, max_chars: int = 6000) -> str:
        if type(event_id) is not int or not 1 <= event_id <= len(self.events) or max_chars < 0:
            raise ValueError("Invalid archive request")
        event = self.events[event_id - 1]
        message = event.get("data", {}).get("message", {})
        metadata = message
        if not message.get("archive_ref"):
            note = next((item for item in reversed(self.events)
                         if item["kind"] == "note" and item["data"].get("type") == "tool_result_archive"
                         and item["data"].get("source_event_id") == event_id), None)
            metadata = note["data"] if note else {}
        relative = metadata.get("archive_ref")
        digest = metadata.get("archive_sha256")
        if not isinstance(relative, str) or not re.fullmatch(r"archives/\d+-[0-9a-f]{64}\.txt", relative):
            raise ValueError("Session event has no valid archive reference")
        path = self.directory / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("Session archive path escapes root")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Session archive checksum mismatch")
        text = payload.decode("utf-8")
        return text[:max_chars] + (f"\n[preview truncated; {len(text)} characters total]" if len(text) > max_chars else "")

    def append_message(self, message: dict) -> int:
        event_id = self.append_event("message", {"message": message})
        persisted = self.events[event_id - 1]["data"]["message"]
        if persisted.get("archive_ref"):
            message.clear()
            message.update(deepcopy(persisted))
        return event_id

    def set_context_budget(self, max_context_tokens: int, max_output_tokens: int, source: str) -> None:
        if (type(max_context_tokens) is not int or max_context_tokens < 1
                or type(max_output_tokens) is not int or max_output_tokens < 1):
            raise ValueError("Invalid context budget")
        with self._write_lock:
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self.meta["context_budget"] = {
                "max_context_tokens": max_context_tokens,
                "max_output_tokens": max_output_tokens,
                "context_window_source": source,
            }
            temporary = self.meta_path.with_name(f"meta.{uuid.uuid4().hex}.tmp")
            with temporary.open("wb") as stream:
                stream.write(_encoded(self.meta))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.meta_path)

    def messages(self) -> list[dict]:
        return [deepcopy(event["data"]["message"]) for event in self.events if event["kind"] == "message"]

    def _repair_pending_tools(self) -> None:
        pending = set()
        for message in self.messages():
            if message.get("tool_calls"):
                pending.update(call["id"] for call in message["tool_calls"])
            if message.get("role") == "tool":
                pending.discard(message.get("tool_call_id"))
        for call_id in sorted(pending):
            self.append_message({
                "role": "tool", "tool_call_id": call_id,
                "content": "[result not persisted; side effect unknown; do not retry automatically]",
            })
        if pending:
            self.warnings.append("Unanswered tool calls were marked uncertain; no tool was rerun")
