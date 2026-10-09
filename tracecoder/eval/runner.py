"""One isolated CLI attempt followed by an independent Docker Gate."""

import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from tracecoder.eval.cases import TaskSpec, _tree_hash, materialize
from tracecoder.eval.gate import run_gate
from tracecoder.execution import DockerExecutor
from tracecoder.session_journal import _SECRET, _digest

SOURCE_ROOT = Path(__file__).resolve().parents[2]
MAX_LOG_BYTES = 4_000_000


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _redactor(environment: dict[str, str]):
    secrets = sorted({value for key, value in environment.items() if value and
                      re.search(r"(?i)(key|token|secret|password|credential)", key)}, key=len, reverse=True)

    def redact(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, "[REDACTED_SECRET]")
            text = text.replace(json.dumps(secret)[1:-1], "[REDACTED_SECRET]")
        return _SECRET.sub("[REDACTED_SECRET]", text)

    return redact


def _clean(value, redact):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_clean(item, redact) for item in value]
    if isinstance(value, dict):
        return {redact(key): _clean(item, redact) for key, item in value.items()}
    return value


def _revision(root: Path) -> str:
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True, check=True, timeout=10)
    if status.stdout:
        raise ValueError("Official evaluation requires a clean Agent Git revision")
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True, timeout=10).stdout.strip()


def _child_env(home: Path, usage: Path, model: str, provider: str, base_url: str | None,
               image: str, run_id: str, turn_path: Path | None = None,
               max_tokens: int = 100000) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("TRACECODER_", "PYTHON")) and key not in {"OPENAI_BASE_URL"}}
    for key in ("TRACECODER_API_KEY",):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "appdata"),
        "LOCALAPPDATA": str(home / "localappdata"), "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"), "PYTHONPATH": str(SOURCE_ROOT), "PYTHONSAFEPATH": "1",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "TRACECODER_EVAL_MODE": "1", "TRACECODER_EVAL_RUN_ID": run_id, "TRACECODER_EVAL_IMAGE": image,
        "TRACECODER_EVAL_USAGE_PATH": str(usage), "TRACECODER_EVAL_MAX_CALLS": "50",
        "TRACECODER_EVAL_MAX_TOKENS": str(max_tokens), "TRACECODER_MODEL": model, "TRACECODER_PROVIDER": provider,
        "TRACECODER_TEMPERATURE": "0", "TRACECODER_MAX_TOKENS": "4096", "TRACECODER_MAX_CONTEXT": "128000",
    })
    if base_url:
        env["OPENAI_BASE_URL"] = base_url
    if turn_path is not None:
        env["TRACECODER_EVAL_TURN_PATH"] = str(turn_path)
    return env


def _cleanup_workers(run_id: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        return False
    command = ["docker", "ps", "-aq", "--filter", f"label=tracecoder.eval.run={run_id}"]
    try:
        listed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        for container in listed.stdout.split():
            if not re.fullmatch(r"[0-9a-f]{12,64}", container):
                return False
            # Recheck the label before removing an object from the daemon.
            inspected = subprocess.run(["docker", "inspect", container], capture_output=True, text=True,
                                       timeout=10, check=False)
            if inspected.returncode:
                if "No such" in inspected.stderr:
                    continue
                return False
            labels = json.loads(inspected.stdout)[0]["Config"].get("Labels") or {}
            if labels.get("tracecoder.eval.run") != run_id:
                return False
            removed = subprocess.run(["docker", "rm", "-f", container], capture_output=True, text=True,
                                     timeout=10, check=False)
            if removed.returncode and "No such container" not in removed.stderr:
                return False
        verified = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        return not verified.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return False


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True, timeout=15, check=False)
        if killed.returncode and proc.poll() is None:
            raise RuntimeError("Worker process tree termination failed")
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait(timeout=15)


def _run_process(command: list[str], cwd: Path, env: dict[str, str], timeout: float = 900) -> dict:
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            shell=False, start_new_session=os.name != "nt",
                            umask=0 if os.name != "nt" else -1,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    logs: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    overflow = threading.Event()

    def collect(name, stream):
        size = 0
        while data := stream.read(65536):
            size += len(data)
            if size > MAX_LOG_BYTES:
                overflow.set()
                break
            logs[name].append(data)

    readers = [threading.Thread(target=collect, args=(name, getattr(proc, name)), daemon=True) for name in logs]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None and not overflow.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        timed_out = proc.poll() is None and not overflow.is_set()
    finally:
        if proc.poll() is None or os.name != "nt":
            _kill_tree(proc)
        for reader in readers:
            reader.join(timeout=3)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("Worker descendants retained output handles")
    return {"exit_code": proc.returncode, "timed_out": timed_out, "output_limit": overflow.is_set(),
            **{name: b"".join(chunks).decode("utf-8", errors="replace") for name, chunks in logs.items()}}


def _files(root: Path) -> dict[str, bytes]:
    _tree_hash(root)  # Reject symlinks and special files before reading candidates.
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _prepare_workspace(workspace: Path) -> None:
    # Only the disposable copy is shared; its temporary parent stays private (0700 on POSIX).
    for path in (workspace, *workspace.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in evaluation workspace")
        path.chmod((path.stat().st_mode & 0o777) | (0o777 if path.is_dir() else 0o666))


def _archive_events(sessions: list[Path], redact) -> tuple[str, list[str]]:
    events, warnings = [], []
    for session in sessions:
        for index, line in enumerate(session.read_bytes().splitlines(keepends=True), 1):
            if not line.endswith(b"\n"):
                warnings.append(f"{session.parent.name}: incomplete final journal record omitted")
                break
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Invalid journal record")  # noqa: TRY004 - invalid persisted/tool data uses ValueError consistently
                checksum = event.pop("checksum", None)
                if checksum != _digest(event):
                    raise ValueError("Journal checksum mismatch")
                if (type(event.get("seq")) is not int or event["seq"] != index or
                        event.get("kind") not in {"message", "model", "note"}):
                    raise ValueError("Invalid journal sequence or kind")
            except (ValueError, UnicodeDecodeError, AttributeError):
                warnings.append(f"{session.parent.name}: invalid journal record at line {index}; remaining records omitted")
                break
            cleaned = _clean(event, redact)
            cleaned["redacted"] = cleaned != event or cleaned.get("redacted", False)
            cleaned["checksum"] = _digest(cleaned)
            events.append(json.dumps(cleaned, ensure_ascii=False))
    return "\n".join(events) + ("\n" if events else ""), warnings


def _patch(before: dict[str, bytes], after: dict[str, bytes]) -> tuple[list[str], str]:
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    chunks = []
    for name in changed:
        try:
            old = before.get(name, b"").decode("utf-8").splitlines(keepends=True)
            new = after.get(name, b"").decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            chunks.append(f"Binary files a/{name} and b/{name} differ\n")
            continue
        for line in difflib.unified_diff(old, new, fromfile=f"a/{name}" if name in before else "/dev/null",
                                        tofile=f"b/{name}" if name in after else "/dev/null"):
            chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return changed, "".join(chunks)


def _model_calls_trace_complete(trace: object, usage: dict) -> bool:
    if not isinstance(trace, dict) or type(trace.get("version")) is not int or trace["version"] != 1:
        return False
    calls, limit, rejected = trace.get("calls"), trace.get("limit"), trace.get("rejected_attempts")
    if (type(calls) is not list or type(usage.get("calls")) is not int or len(calls) != usage["calls"]
            or type(usage.get("tokens")) is not int or type(usage.get("usage_unknown")) is not bool
            or limit not in (None, "calls", "tokens") or limit != usage.get("limit")
            or type(rejected) is not int or rejected < 0 or bool(rejected) != bool(limit)):
        return False
    prompt_total = completion_total = 0
    unknown = False
    for index, call in enumerate(calls, 1):
        if (type(call) is not dict or type(call.get("call_index")) is not int or call["call_index"] != index
                or not isinstance(call.get("actor"), str)
                or (call["actor"] != "parent" and not re.fullmatch(r"child:[0-9a-f]{32}", call["actor"]))):
            return False
        outcome = call.get("outcome")
        prompt, completion = call.get("prompt_tokens"), call.get("completion_tokens")
        if outcome == "provider_error":
            if (prompt is not None or completion is not None or call.get("usage_status") != "missing"
                    or not isinstance(call.get("exception_type"), str) or not call["exception_type"]):
                return False
            unknown = True
        elif outcome == "completed":
            if any(value is not None and (type(value) is not int or value < 0) for value in (prompt, completion)):
                return False
            status = ("complete" if prompt is not None and completion is not None and prompt + completion > 0
                      else "zero" if prompt is not None and completion is not None
                      else "partial" if prompt is not None or completion is not None else "missing")
            if call.get("usage_status") != status:
                return False
            prompt_total += prompt or 0
            completion_total += completion or 0
            unknown = unknown or status != "complete"
        else:
            return False
        if (type(call.get("cumulative_prompt_tokens")) is not int
                or call["cumulative_prompt_tokens"] != prompt_total
                or type(call.get("cumulative_completion_tokens")) is not int
                or call["cumulative_completion_tokens"] != completion_total
                or type(call.get("cumulative_tokens")) is not int
                or call["cumulative_tokens"] != prompt_total + completion_total
                or call.get("cumulative_usage_unknown") is not unknown):
            return False
    return prompt_total + completion_total == usage["tokens"] and unknown is usage["usage_unknown"]


def run_case(task: TaskSpec, output_root: Path, model: str, provider: str,
             base_url: str | None, image: str, max_tokens: int = 100000) -> dict:
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    directory = Path(output_root).resolve() / f"{task.id}-{run_id}"
    directory.mkdir(parents=True, exist_ok=False)
    redact = _redactor(dict(os.environ))

    def write(name: str, text: str) -> None:
        if name.endswith(".json"):
            text = _json(_clean(json.loads(text), redact))
        elif name != "events.jsonl":
            text = redact(text)
        (directory / name).write_bytes(text.encode("utf-8"))

    definition = {"id": task.id, "prompt": task.prompt, "snapshot_sha256": task.snapshot_sha256,
                  "gate_command": task.gate_command, "allowed_paths": task.allowed_paths}
    command = [sys.executable, "-m", "tracecoder", "--yes", "-p", task.prompt, "--model", model]
    metadata = {"version": 1, "run_id": run_id, "task_id": task.id, "model": model, "provider": provider,
                "base_url": base_url, "temperature": 0, "max_output_tokens": 4096, "max_context_tokens": 128000,
                "budgets": {"seconds": 900, "calls": 50, "tokens": max_tokens, "gate_seconds": 120},
                "image": image, "image_id": None, "agent_revision": None, "worker_command": command,
                "snapshot_sha256": task.snapshot_sha256,
                "task_sha256": hashlib.sha256(_json(definition).encode()).hexdigest()}
    result = {"task_id": task.id, "run_id": run_id, "run_dir": str(directory), "status": "setup_error",
              "gate_status": None, "gate_exit_code": None, "exit_code": None, "changed_paths": [],
              "forbidden_paths": [], "session_ids": [], "docker_valid": False, "reproducible": False}
    usage = {"calls": 0, "tokens": 0, "usage_unknown": True, "limit": None}
    for name in ("events.jsonl", "stdout.txt", "stderr.txt", "patch.diff", "gate.txt"):
        write(name, "")
    with tempfile.TemporaryDirectory(prefix="tracecoder-eval-") as temporary:
        root = Path(temporary).resolve()
        workspace, home = root / "workspace", root / "home"
        home.mkdir()
        usage_path = root / "usage.json"
        calls_path = root / "model_calls.json"
        turn_path = root / "turn.json"
        try:
            if provider not in {"openai", "litellm"} or not model:
                raise ValueError("Evaluation requires an explicit model and supported provider")
            metadata["agent_revision"] = _revision(SOURCE_ROOT)
            materialize(task, workspace)
            before = _files(workspace)
            _prepare_workspace(workspace)
            metadata["gate_sha256"] = _tree_hash(task.gate_dir)
            if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
                raise ValueError("Evaluation requires a digest-pinned image")
            inspected = subprocess.run(["docker", "image", "inspect", image, "--format={{.Id}}"],
                                       capture_output=True, text=True, check=True, timeout=10)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", inspected.stdout.strip()):
                raise ValueError("Invalid Docker image ID")
            metadata["image_id"] = inspected.stdout.strip()
            ready = DockerExecutor(workspace, image, run_id).run(
                "test -w /workspace && test ! -e /gate && printf tracecoder-eval-ready", timeout=15)
            if ready != "tracecoder-eval-ready" or not _cleanup_workers(run_id):
                raise RuntimeError(f"Worker Docker preflight failed: {ready}")
            result["docker_valid"] = True
            result["reproducible"] = True
            write("run.json", _json(metadata))
            result["status"] = "agent_error"
            worker_started = time.monotonic()
            try:
                environment = _child_env(home, usage_path, model, provider, base_url, image, run_id, turn_path, max_tokens)
                environment["TRACECODER_EVAL_ALLOWED_PATHS"] = json.dumps(task.allowed_paths)
                outcome = _run_process(command, workspace, environment)
            finally:
                clean = _cleanup_workers(run_id)
                result["agent_duration_seconds"] = time.monotonic() - worker_started
            write("stdout.txt", outcome["stdout"])
            write("stderr.txt", outcome["stderr"])
            result["exit_code"] = outcome["exit_code"]
            result.update(timed_out=outcome["timed_out"], output_limit=outcome["output_limit"])
            if outcome["timed_out"] or outcome["output_limit"]:
                result["status"] = "agent_limit"
            after = _files(workspace)
            result["candidate_sha256"] = _tree_hash(workspace)
            result["changed_paths"], patch = _patch(before, after)
            result["forbidden_paths"] = sorted(set(result["changed_paths"]) - set(task.allowed_paths))
            write("patch.diff", patch)
            if usage_path.exists():
                usage = json.loads(usage_path.read_text(encoding="utf-8"))
            if usage.get("limit"):
                result["status"] = "agent_limit"
            turn = json.loads(turn_path.read_text(encoding="utf-8")) if turn_path.exists() else {}
            result["turn_completed"] = turn.get("completed") is True
            result["turn_reason"] = turn.get("reason", "missing")
            sessions = list((home / ".tracecoder" / "sessions").glob("*/events.jsonl"))
            result["session_ids"] = [path.parent.name for path in sessions]
            events, result["journal_warnings"] = _archive_events(sessions, redact)
            write("events.jsonl", events)
            if not clean:
                raise RuntimeError("Worker container cleanup could not be verified")
            if result["forbidden_paths"]:
                raise ValueError("Candidate changed paths outside the task allowlist")
            if _tree_hash(task.gate_dir) != metadata["gate_sha256"]:
                raise ValueError("Gate changed during worker execution")
            gate = run_gate(task, workspace, image)
            write("gate.txt", gate.output)
            result.update(gate_status=gate.status, gate_exit_code=gate.exit_code,
                          gate_duration_seconds=gate.duration_seconds)
            if outcome["timed_out"] or outcome["output_limit"] or usage.get("limit") or turn.get("reason") == "round_limit":
                result["status"] = "agent_limit"
            elif (outcome["exit_code"] or turn.get("reason") != "completed" or
                  len(sessions) != 1 or result["journal_warnings"]):
                result["status"] = "agent_error"
            else:
                result["status"] = gate.status
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result["error"] = str(error)
        finally:
            if not (directory / "run.json").exists():
                write("run.json", _json(metadata))
            trace = {"version": 1, "calls": [], "limit": None, "rejected_attempts": 0}
            result["model_calls_trace_complete"] = False
            if calls_path.exists():
                try:
                    trace = json.loads(calls_path.read_text(encoding="utf-8"))
                    result["model_calls_trace_complete"] = _model_calls_trace_complete(trace, usage)
                except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                    trace = {"version": 1, "calls": [], "limit": None, "rejected_attempts": 0}
                    result["model_calls_trace_error"] = type(error).__name__
            write("model_calls.json", _json(trace))
    result.update(usage)
    result.update(agent_revision=metadata["agent_revision"], image_id=metadata["image_id"],
                  duration_seconds=time.monotonic() - started)
    write("usage.json", _json(usage))
    result["artifact_hashes"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(directory.iterdir())}
    write("result.json", _json(result))
    return json.loads((directory / "result.json").read_text(encoding="utf-8"))
