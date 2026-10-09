"""Bounded Docker execution for strict CLI Bash calls."""

import base64
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

DEFAULT_IMAGE = (
    "ghcr.io/astral-sh/uv@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e"
)
MAX_OUTPUT_BYTES = 1_000_000


class DockerExecutor:
    def __init__(self, workspace: Path, image: str = DEFAULT_IMAGE, run_id: str | None = None,
                 workspace_target: str = "/workspace"):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir() or "," in str(self.workspace):
            raise ValueError("Docker workspace must be an existing directory without commas")
        self.image = image
        self.run_id = run_id or (os.getenv("TRACECODER_EVAL_RUN_ID") if os.getenv("TRACECODER_EVAL_MODE") == "1" else None)
        if self.run_id is not None and not re.fullmatch(r"[0-9a-f]{32}", self.run_id):
            raise ValueError("Invalid evaluation run ID")
        if workspace_target not in ("/workspace", "/testbed") or workspace_target == "/testbed" and not self.run_id:
            raise ValueError("Invalid Docker workspace target")
        if workspace_target == "/testbed" and not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("Verified worker requires a digest-pinned image")
        self.workspace_target = workspace_target

    @classmethod
    def available(cls, image: str = DEFAULT_IMAGE) -> bool:
        if not shutil.which("docker"):
            return False
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    @classmethod
    def try_create(cls, workspace: Path) -> "DockerExecutor | None":
        evaluating = os.getenv("TRACECODER_EVAL_MODE") == "1"
        image = os.environ.get("TRACECODER_EVAL_IMAGE", "") if evaluating else DEFAULT_IMAGE
        if evaluating and not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
            raise ValueError("Evaluation requires a digest-pinned worker image")
        if not cls.available(image):
            if evaluating:
                raise RuntimeError("Evaluation worker Docker image unavailable")
            return None
        if evaluating:
            return cls(workspace, image, workspace_target=os.getenv("TRACECODER_EVAL_WORKSPACE_TARGET", "/workspace"))
        try:
            return cls(workspace, image)
        except ValueError:
            return None

    def start(self, command: str, timeout: int = 120, *, background: bool = False) -> "DockerRun":
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be non-empty text")
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError("timeout must be between 1 and 300 seconds")
        name = f"tracecoder-sandbox-{uuid.uuid4().hex}"
        if self.run_id:
            command = "umask 000; " + command
        args = [
            "docker", "create", f"--name={name}", "--pull=never", "--network=none",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=512m", "--cpus=1", "--read-only", "--init",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", "--user=65534:65534",
            f"--mount=type=bind,source={self.workspace},target={self.workspace_target}",
            f"--workdir={self.workspace_target}", "--env=HOME=/tmp",
            "--entrypoint=/bin/sh", self.image, "-c", command,
        ]
        if self.workspace_target == "/testbed":
            args[args.index("--user=65534:65534")] = "--user=0:0"
            args[args.index("--entrypoint=/bin/sh")] = "--entrypoint=/bin/bash"
            args.insert(args.index(self.image), "--env=BASH_ENV=/root/.bashrc")
        if self.run_id:
            args.insert(2, f"--label=tracecoder.eval.run={self.run_id}")
            args.insert(2, "--env=PYTHONDONTWRITEBYTECODE=1")
        if background:
            args[args.index("--entrypoint=/bin/bash" if self.workspace_target == "/testbed"
                            else "--entrypoint=/bin/sh")] = "--entrypoint=python"
            args[-2:] = ["-u", "-c", _WATCHDOG, str(timeout),
                         "/bin/bash" if self.workspace_target == "/testbed" else "/bin/sh",
                         base64.b64encode(command.encode()).decode()]
        try:
            created = subprocess.run(args, capture_output=True, timeout=10, check=False,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if created.returncode:
                raise OSError(created.stderr.decode("utf-8", errors="replace") + f"\n[exit code: {created.returncode}]")
            return DockerRun(self, name, timeout, watchdog=background)
        except BaseException:
            self._cleanup(name)
            raise

    def run(self, command: str, timeout: int = 120) -> str:
        try:
            result = self.start(command, timeout).wait()
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return f"Error: Docker execution failed: {error}"
        return format_result(result, timeout)

    @staticmethod
    def _cleanup(name: str) -> bool:
        try:
            result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=10,
                                    check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 or "No such container" in result.stderr


# The container owns this deadline even if the CLI is killed.
_WATCHDOG = """
import base64, os, signal, subprocess, sys
p = subprocess.Popen([sys.argv[2], '-c', base64.b64decode(sys.argv[3]).decode()], start_new_session=True)
try:
    code = p.wait(timeout=int(sys.argv[1]))
except subprocess.TimeoutExpired:
    code = 124
finally:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()
sys.exit(code if code >= 0 else 128 - code)
"""


def format_result(result: dict, timeout: int = 120) -> str:
    status = result["status"]
    if status == "cleanup_failed":
        return "Error: Docker container cleanup could not be verified"
    if status == "output_limit":
        return "Error: output limit exceeded; container terminated"
    if status == "timed_out":
        return f"Error: timed out after {timeout}s"
    if status == "cancelled":
        return "Error: command cancelled"
    output = result["output"]
    if result["exit_code"]:
        output += f"\n[exit code: {result['exit_code']}]"
    return output.strip() or "(no output)"


class DockerRun:
    def __init__(self, executor: DockerExecutor, name: str, timeout: int, *, watchdog: bool = False):
        self.executor, self.name, self.timeout = executor, name, timeout
        self.watchdog = watchdog
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._output = bytearray()
        self._result = {"status": "running", "exit_code": None, "cleanup_verified": False}
        self._proc = subprocess.Popen(
            ["docker", "start", "--attach", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            output = self._output.decode("utf-8", errors="replace")
            if len(output) > 15000:
                output = output[:6000] + f"\n... truncated ({len(output)} chars total) ...\n" + output[-3000:]
            return {**self._result, "output": output}

    def _watch(self) -> None:
        overflow = threading.Event()
        read_error = threading.Event()

        def collect():
            try:
                while data := self._proc.stdout.read1(65536):
                    with self._lock:
                        remaining = MAX_OUTPUT_BYTES - len(self._output)
                        self._output.extend(data[:remaining])
                        if len(data) > remaining:
                            overflow.set()
                            break
            except OSError:
                read_error.set()

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout
        status = "failed"
        cleaned = False
        try:
            while self._proc.poll() is None:
                if self._cancel.is_set() or overflow.is_set() or time.monotonic() >= deadline:
                    status = ("output_limit" if overflow.is_set() else
                              "cancelled" if self._cancel.is_set() else "timed_out")
                    cleaned = self.executor._cleanup(self.name)
                    if self._proc.poll() is None:
                        self._proc.kill()
                    break
                self._cancel.wait(0.05)
            self._proc.wait()
            reader.join(timeout=2)
            if status == "failed":
                status = "completed" if self._proc.returncode == 0 and not read_error.is_set() else "failed"
            if overflow.is_set():
                status = "output_limit"
            if self.watchdog and self._proc.returncode == 124:
                status = "timed_out"
        finally:
            cleaned = self.executor._cleanup(self.name)
            with self._lock:
                self._result = {"status": status if cleaned else "cleanup_failed",
                                "exit_code": self._proc.returncode, "cleanup_verified": cleaned}
            if not reader.is_alive():
                self._proc.stdout.close()
            self._done.set()

    def wait(self, timeout: float | None = None) -> dict:
        try:
            if timeout is not None:
                self._done.wait(timeout)
            else:
                while not self._done.is_set():
                    time.sleep(0.05)
        except BaseException:
            self._cancel.set()
            self._done.wait()
            raise
        return self.snapshot()

    def cancel(self) -> dict:
        self._cancel.set()
        self._done.wait()
        return self.snapshot()
