"""Separate read-only Docker validation for evaluation tasks.

Candidate code shares the Gate container and can read /gate. The worker cannot
access that directory; validation is not an adversarial hidden-test boundary.
"""

import ast
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from tracecoder.eval.cases import TaskSpec

MAX_OUTPUT_BYTES = 1_000_000

_UNITTEST_CHILD = """
import ctypes, json, os, subprocess, sys, types, unittest
fd, start, pattern = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.set_inheritable(fd, False)
if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0):
    raise RuntimeError("Cannot protect trusted test process")

def invoke(source):
    program = "import json\\n" + source + "\\nprint(json.dumps(result))\\n"
    candidate = subprocess.run([sys.executable, "-c", program], cwd="/workspace", stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10,
                               close_fds=True, check=False)
    if candidate.returncode or len(candidate.stdout) > 65536:
        raise AssertionError(f"Candidate process failed: {candidate.returncode}; {candidate.stderr[:2000]}")
    try:
        return json.loads(candidate.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"Invalid candidate response: {candidate.stdout[:2000]!r}") from error

gate_api = types.ModuleType("gate_api")
gate_api.invoke = invoke
sys.modules["gate_api"] = gate_api
discover = unittest.defaultTestLoader.discover
run = unittest.TextTestRunner().run
was_successful = unittest.TestResult.wasSuccessful
suite = discover(start, pattern=pattern)
discovered = suite.countTestCases()
result = run(suite)
success = was_successful(result)
os.write(fd, json.dumps({"discovered": discovered, "tests_run": result.testsRun,
                         "skipped": len(result.skipped), "success": success}).encode())
raise SystemExit(0 if success else 1)
"""

_UNITTEST_SUPERVISOR = """
import base64, ctypes, json, os, subprocess, sys
expected, child_code, start, pattern = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
command = sys.argv[5:]
if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0):
    raise RuntimeError("Cannot protect Gate supervisor")
read_fd, write_fd = os.pipe()
child = subprocess.Popen([command[0], "-I", "-c", child_code, str(write_fd), start, pattern],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, pass_fds=(write_fd,))
os.close(write_fd)
while chunk := os.read(child.stdout.fileno(), 2048):
    os.write(1, json.dumps({"log": base64.b64encode(chunk).decode()}).encode() + b"\\n")
code = child.wait()
record = os.read(read_fd, 4096)
os.close(read_fd)
try:
    report = json.loads(record)
except (ValueError, UnicodeDecodeError):
    report = {}
complete = (report.get("discovered") == expected and report.get("tests_run") == expected
            and report.get("skipped") == 0 and code in (0, 1))
os.write(1, json.dumps({"control": {"complete": complete, "expected": expected,
                                    "tests_run": report.get("tests_run"), "success": report.get("success"),
                                    "child_exit": code}}).encode() + b"\\n")
raise SystemExit(0 if complete and report.get("success") and code == 0 else 1 if complete else 3)
"""


def _unittest_evidence(task: TaskSpec) -> tuple[int, str] | None:
    command = task.gate_command
    if command[:6] != ["python", "-m", "unittest", "discover", "-s", "/gate"]:
        return None
    if len(command) == 6:
        pattern = "test*.py"
    elif len(command) == 8 and command[6] == "-p":
        pattern = command[7]
    else:
        raise ValueError("Unsupported unittest discovery command")
    files = sorted(task.gate_dir.glob(pattern))
    count = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and any(
                isinstance(base, ast.Attribute) and base.attr == "TestCase" for base in cls.bases
            ):
                count += sum(isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                             and method.name.startswith("test_") for method in cls.body)
    if count < 1:
        raise ValueError("Gate has no countable unittest cases")
    return count, pattern


def _supervised_output(data: bytes) -> tuple[str, dict | None]:
    logs = []
    control = None
    for line in data.splitlines():
        try:
            item = json.loads(line)
            if set(item) == {"log"}:
                logs.append(base64.b64decode(item["log"], validate=True))
            elif set(item) == {"control"} and control is None:
                control = item["control"]
            else:
                return b"".join(logs).decode("utf-8", errors="replace"), None
        except (ValueError, TypeError, KeyError, binascii.Error):
            return b"".join(logs).decode("utf-8", errors="replace"), None
    return b"".join(logs).decode("utf-8", errors="replace"), control


@dataclass(frozen=True)
class GateResult:
    status: str
    exit_code: int | None
    output: str
    duration_seconds: float
    image_id: str | None


def _docker_args(task: TaskSpec, workspace: Path, image: str, name: str,
                 evidence: tuple[int, str] | None = None) -> list[str]:
    command = task.gate_command if evidence is None else [
        "python", "-I", "-c", _UNITTEST_SUPERVISOR, str(evidence[0]), _UNITTEST_CHILD,
        "/gate", evidence[1], *task.gate_command,
    ]
    return [
        "docker", "run", "--rm", f"--name={name}", "--pull=never", "--network=none",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64",
        "--memory=512m", "--cpus=1", "--read-only", "--init",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", "--user=65534:65534",
        f"--mount=type=bind,source={workspace},target=/workspace,readonly",
        f"--mount=type=bind,source={task.gate_dir},target=/gate,readonly",
        "--workdir=/workspace", "--env=HOME=/tmp", "--env=PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint=", image, *command,
    ]


def _cleanup(name: str) -> bool:
    try:
        removed = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=10, check=False)
        inspected = subprocess.run(["docker", "container", "inspect", name], capture_output=True, text=True,
                                   timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (removed.returncode == 0 or "No such container" in removed.stderr) and inspected.returncode != 0 and (
        "No such object" in inspected.stderr or "No such container" in inspected.stderr
    )


def run_gate(task: TaskSpec, workspace: Path, image: str, timeout: int = 120) -> GateResult:
    started = time.monotonic()

    def result(status: str, output: str, exit_code: int | None = None, image_id: str | None = None) -> GateResult:
        return GateResult(status, exit_code, output, time.monotonic() - started, image_id)

    workspace = Path(workspace)
    gate = task.gate_dir
    if (
        not isinstance(image, str) or not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image)
        or not isinstance(timeout, int) or not 1 <= timeout <= 120
        or not workspace.is_absolute() or not gate.is_absolute()
        or not workspace.is_dir() or not gate.is_dir()
        or "," in str(workspace) or "," in str(gate)
        or workspace.resolve().is_relative_to(gate.resolve()) or gate.resolve().is_relative_to(workspace.resolve())
        or not isinstance(task.gate_command, list) or not task.gate_command
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in task.gate_command)
    ):
        return result("setup_error", "Invalid Gate image, paths, command, or timeout")
    if not shutil.which("docker"):
        return result("setup_error", "Docker executable is unavailable")
    try:
        evidence = _unittest_evidence(task)
    except (OSError, SyntaxError, ValueError) as error:
        return result("setup_error", f"Gate test inventory failed: {error}")
    try:
        inspected = subprocess.run(["docker", "image", "inspect", image, "--format={{.Id}}"],
                                   capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return result("setup_error", f"Docker image preflight failed: {error}")
    if inspected.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", inspected.stdout.strip()):
        return result("setup_error", f"Pinned local image unavailable: {inspected.stderr.strip()}")
    image_id = inspected.stdout.strip()
    name = f"tracecoder-gate-{uuid.uuid4().hex}"
    try:
        proc = subprocess.Popen(
            _docker_args(task, workspace, image, name, evidence), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as error:
        return result("setup_error", f"Docker Gate could not start: {error}", image_id=image_id)

    chunks: list[bytes] = []
    overflow = threading.Event()

    def collect() -> None:
        size = 0
        while data := proc.stdout.read(65536):
            size += len(data)
            if size > MAX_OUTPUT_BYTES:
                overflow.set()
                break
            chunks.append(data)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while proc.poll() is None and not overflow.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        timed_out = proc.poll() is None and not overflow.is_set()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        reader.join(timeout=2)
        clean = _cleanup(name)
    output, control = _supervised_output(b"".join(chunks)) if evidence else (
        b"".join(chunks).decode("utf-8", errors="replace"), None
    )

    def diagnostic(reason: str) -> str:
        return f"{reason}\n{output}" if output else reason

    if not clean or reader.is_alive():
        return result("gate_error", diagnostic("Docker container cleanup could not be verified"), proc.returncode, image_id)
    if overflow.is_set():
        return result("gate_error", diagnostic("Gate output limit exceeded"), proc.returncode, image_id)
    if timed_out:
        return result("gate_error", diagnostic(f"Gate timed out after {timeout}s"), proc.returncode, image_id)
    if evidence and (not isinstance(control, dict) or not control.get("complete")
                     or control.get("expected") != evidence[0]):
        return result("gate_error", diagnostic("Gate completion evidence missing or incomplete"), proc.returncode, image_id)
    if proc.returncode == 125:
        return result("setup_error", output, proc.returncode, image_id)
    if proc.returncode in (126, 127):
        return result("gate_error", output, proc.returncode, image_id)
    return result("pass" if proc.returncode == 0 else "test_fail", output, proc.returncode, image_id)
