"""Independent evaluation Gate checks with the pinned local Docker image."""

import shutil
import subprocess
from pathlib import Path

import pytest

from cluecoder.eval.cases import TaskSpec, load_suite, materialize
from cluecoder.eval.gate import _docker_args, run_gate
from cluecoder.execution import DEFAULT_IMAGE


@pytest.fixture
def docker_ready():
    if not shutil.which("docker") or subprocess.run(
        ["docker", "image", "inspect", DEFAULT_IMAGE], capture_output=True, timeout=5, check=False,
    ).returncode:
        pytest.skip("Pinned local Docker image or daemon is unavailable")


def _task(tmp_path: Path, command: list[str]) -> TaskSpec:
    workspace = tmp_path / "workspace"
    gate = tmp_path / "hidden-tests"
    workspace.mkdir()
    gate.mkdir()
    return TaskSpec("repair", "Fix it", workspace, "0" * 64, gate, command, ["solution.py"])


def test_gate_uses_read_only_container_argv(tmp_path: Path):
    task = _task(tmp_path, ["python", "-c", "print('ok')"])
    args = _docker_args(task, task.snapshot_dir, DEFAULT_IMAGE, "cluecoder-gate-test")
    assert args[:2] == ["docker", "run"]
    assert "--network=none" in args
    assert "--user=65534:65534" in args
    assert "--read-only" in args
    assert "--memory=512m" in args
    assert "--cpus=1" in args
    assert "--pids-limit=64" in args
    assert "--pull=never" in args
    assert f"--mount=type=bind,source={task.snapshot_dir},target=/workspace,readonly" in args
    assert f"--mount=type=bind,source={task.gate_dir},target=/gate,readonly" in args
    assert args[-len(task.gate_command):] == task.gate_command


def test_gate_never_uses_host_shell(tmp_path: Path, docker_ready):
    marker = tmp_path / "host-shell-marker"
    text = f"literal; $(touch {marker})"
    task = _task(tmp_path, ["python", "-c", "import sys; print(sys.argv[1])", text])
    result = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE)
    assert result.status == "pass", result.output
    assert result.exit_code == 0
    assert result.image_id == DEFAULT_IMAGE.rsplit("@", 1)[1]
    assert result.output.strip() == text
    assert not marker.exists()


def test_gate_reports_test_failure_and_blocks_writes(tmp_path: Path, docker_ready):
    script = (
        "from pathlib import Path; "
        "assert Path('/workspace/solution.py').read_text() == 'broken'; "
        "assert Path('/gate/hidden.txt').read_text() == 'secret'"
    )
    task = _task(tmp_path, ["python", "-c", script])
    (task.snapshot_dir / "solution.py").write_text("broken", encoding="utf-8")
    (task.gate_dir / "hidden.txt").write_text("secret", encoding="utf-8")
    assert run_gate(task, task.snapshot_dir, DEFAULT_IMAGE).status == "pass"

    for target in ("/workspace/solution.py", "/gate/hidden.txt"):
        task.gate_command[:] = ["python", "-c", f"from pathlib import Path; Path({target!r}).write_text('changed')"]
        result = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE)
        assert result.status == "test_fail", result.output
        assert result.exit_code == 1
        assert "Read-only file system" in result.output
    assert (task.snapshot_dir / "solution.py").read_text(encoding="utf-8") == "broken"
    assert (task.gate_dir / "hidden.txt").read_text(encoding="utf-8") == "secret"


def test_gate_container_is_nonroot_and_networkless(tmp_path: Path, docker_ready):
    script = (
        "import os, socket\n"
        "assert os.getuid() == 65534\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=1)\n"
        "except OSError:\n"
        "    print('network-blocked')\n"
        "else:\n"
        "    raise AssertionError('network unexpectedly available')\n"
    )
    task = _task(tmp_path, ["python", "-c", script])
    result = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE)
    assert result.status == "pass", result.output
    assert "network-blocked" in result.output


def test_gate_timeout_and_output_bound_leave_no_container(tmp_path: Path, docker_ready):
    task = _task(tmp_path, ["python", "-c", "import time; print('timeout-diagnostic', flush=True); time.sleep(10)"])
    timed_out = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE, timeout=3)
    assert timed_out.status == "gate_error"
    assert "timed out" in timed_out.output
    assert "timeout-diagnostic" in timed_out.output
    task.gate_command[:] = ["python", "-c", "print('overflow-diagnostic', flush=True); print('x' * 2000000)"]
    result = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE)
    assert result.status == "gate_error"
    assert "output limit" in result.output
    assert "overflow-diagnostic" in result.output
    assert not subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=cluecoder-gate-", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_gate_missing_image_is_setup_error(tmp_path: Path, docker_ready):
    task = _task(tmp_path, ["python", "-c", "print('never')"])
    missing = "ghcr.io/astral-sh/uv@sha256:" + "0" * 64
    assert run_gate(task, task.snapshot_dir, missing).status == "setup_error"
    assert not subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=cluecoder-gate-", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_gate_missing_fixed_command_is_gate_error(tmp_path: Path, docker_ready):
    task = _task(tmp_path, ["definitely-not-an-installed-gate-command"])
    result = run_gate(task, task.snapshot_dir, DEFAULT_IMAGE)
    assert result.status == "gate_error"
    assert result.exit_code == 127


def test_gate_rejects_premature_success_even_with_forged_unittest_summary(tmp_path: Path, docker_ready):
    suite = load_suite(Path(__file__).resolve().parents[1] / "benchmarks/cluecoder-baseline-v1/manifest.json")
    task = next(task for task in suite.tasks if task.id == "ttl_cache")
    workspace = tmp_path / "candidate"
    materialize(task, workspace)
    (workspace / "solution.py").write_text(
        "import os\n"
        "os.write(1, b'Ran 2 tests in 0.001s\\n\\nOK\\n')\n"
        "os.write(1, b'{\"control\": {\"complete\": true, \"expected\": 2}}\\n')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    result = run_gate(task, workspace, DEFAULT_IMAGE)
    assert result.status == "test_fail", result.output
    assert "Ran 2 tests in 0.001s" in result.output
    assert '"complete": true' in result.output


def test_gate_rejects_candidate_forged_report_fd(tmp_path: Path, docker_ready):
    suite = load_suite(Path(__file__).resolve().parents[1] / "benchmarks/cluecoder-baseline-v1/manifest.json")
    task = next(task for task in suite.tasks if task.id == "ttl_cache")
    workspace = tmp_path / "candidate"
    materialize(task, workspace)
    (workspace / "solution.py").write_text(
        "import os, sys\n"
        "os.write(int(sys.argv[1]), b'{\"discovered\": 2, \"tests_run\": 2, \"skipped\": 0, \"success\": true}')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    assert run_gate(task, workspace, DEFAULT_IMAGE).status == "test_fail"


def test_gate_unavailable_docker_and_unpinned_image_fail_closed(tmp_path: Path, monkeypatch):
    task = _task(tmp_path, ["python", "-c", "print('never')"])
    assert run_gate(task, task.snapshot_dir, "python:latest").status == "setup_error"
    monkeypatch.setenv("PATH", "")
    assert run_gate(task, task.snapshot_dir, DEFAULT_IMAGE).status == "setup_error"
