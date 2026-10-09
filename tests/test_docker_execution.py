"""Real Docker isolation checks; skip only when the pinned local image is unavailable."""

import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from cluecoder import cli as cli_module
from cluecoder import execution as execution_module
from cluecoder import session as session_module
from cluecoder.demo import run_demo
from cluecoder.eval.runner import _prepare_workspace
from cluecoder.execution import DockerExecutor, _sandbox_user
from cluecoder.tools.bash import DockerBashTool


@pytest.fixture
def executor(tmp_path):
    if not DockerExecutor.available():
        pytest.skip("Pinned Docker image or Linux engine is unavailable")
    _prepare_workspace(tmp_path)
    return DockerExecutor(tmp_path, run_id=uuid.uuid4().hex)


def test_docker_runs_python_and_writes_only_mounted_workspace(executor, tmp_path):
    result = executor.run("python -c 'from pathlib import Path; Path(\"result.txt\").write_text(\"sandbox-ok\")'")
    assert "sandbox-ok" == (tmp_path / "result.txt").read_text(encoding="utf-8")
    assert "exit code" not in result


def test_docker_has_no_network_or_root_user(executor):
    assert executor.run("id -u") == "65534"
    assert executor.run("pwd") == "/workspace"
    result = executor.run("python -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), timeout=1)'")
    assert "exit code" in result


def test_verified_docker_uses_testbed_without_changing_ordinary_mode(docker_ready, tmp_path):
    assert DockerExecutor.available()
    with pytest.raises(ValueError, match="digest"):
        DockerExecutor(tmp_path, image="mutable:latest", run_id="a" * 32, workspace_target="/testbed")
    verified = DockerExecutor(tmp_path, run_id="a" * 32, workspace_target="/testbed")
    assert "/testbed" in DockerBashTool(verified).description
    assert "/workspace" not in DockerBashTool(verified).description
    assert verified.run("pwd; id -u; echo $BASH_ENV") == "/testbed\n0\n/root/.bashrc"
    assert "exit code" in verified.run("python -c 'import socket; socket.create_connection((\"1.1.1.1\", 53), timeout=1)'")
    assert DockerExecutor(tmp_path).run("id -u; pwd") == _sandbox_user().split(":")[0] + "\n/workspace"
    assert "/workspace" in DockerBashTool(DockerExecutor(tmp_path)).description


def test_eval_docker_configuration_errors_fail_closed(docker_ready, tmp_path, monkeypatch):
    assert DockerExecutor.available()
    monkeypatch.setenv("CLUECODER_EVAL_MODE", "1")
    monkeypatch.setenv("CLUECODER_EVAL_IMAGE", execution_module.DEFAULT_IMAGE)
    monkeypatch.setenv("CLUECODER_EVAL_RUN_ID", "c" * 32)
    monkeypatch.setenv("CLUECODER_EVAL_WORKSPACE_TARGET", "/wrong")
    with pytest.raises(ValueError, match="target"):
        DockerExecutor.try_create(tmp_path)


def test_cli_uses_local_pinned_docker_image_when_available(tmp_path, monkeypatch):
    if not DockerExecutor.available():
        pytest.skip("Pinned Docker image or Linux engine is unavailable")
    seen = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", ["cluecoder", "-p", "hello", "--yes"])
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli_module, "_run_once", lambda agent, prompt, memory, journal: seen.append(agent))
    cli_module.main()
    assert len(seen) == 1
    bash = next(tool for tool in seen[0].tools if tool.name == "bash")
    assert isinstance(bash, DockerBashTool)
    assert "sandbox-ready" in bash.execute("echo sandbox-ready")


def test_docker_can_mount_actual_repository_drive():
    if not DockerExecutor.available():
        pytest.skip("Pinned Docker image or Linux engine is unavailable")
    repository = Path(__file__).resolve().parent.parent
    assert DockerExecutor(repository).run("test -f pyproject.toml && echo repository-mounted") == "repository-mounted"


def test_strict_demo_runs_test_inside_container(tmp_path, capsys):
    if not DockerExecutor.available():
        pytest.skip("Pinned Docker image or Linux engine is unavailable")
    assert run_demo(strict=True, workspace=tmp_path) == 0
    assert "sandbox test passed" in capsys.readouterr().out


def test_docker_cannot_modify_host_file_outside_workspace(executor, tmp_path):
    outside = tmp_path.parent / "host-secret.txt"
    outside.write_text("original", encoding="utf-8")
    result = executor.run("printf changed > /workspace/../host-secret.txt")
    assert "exit code" in result
    assert "original" not in executor.run("cat /workspace/../host-secret.txt")
    assert outside.read_text(encoding="utf-8") == "original"


def test_docker_mount_does_not_follow_host_link_outside_workspace(executor, tmp_path):
    outside = tmp_path.parent / "linked-host-folder"
    outside.mkdir(exist_ok=True)
    secret = outside / "secret.txt"
    secret.write_text("HOST_ONLY_SECRET", encoding="utf-8")
    try:
        (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host cannot create directory symlinks")
    assert "HOST_ONLY_SECRET" not in executor.run("cat /workspace/linked/secret.txt")
    executor.run("printf changed > /workspace/linked/secret.txt")
    assert secret.read_text(encoding="utf-8") == "HOST_ONLY_SECRET"


def test_docker_image_failure_never_falls_back_to_host_shell(executor, tmp_path):
    missing = DockerExecutor(tmp_path, image="ghcr.io/astral-sh/uv@sha256:" + "0" * 64)
    result = missing.run("printf host-fallback > unsafe.txt")
    assert "exit code" in result
    assert not (tmp_path / "unsafe.txt").exists()


def test_docker_timeout_removes_container_and_child_process(executor):
    before = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=cluecoder.eval.run={executor.run_id}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "timed out" in executor.run("sleep 15 & wait", timeout=2)
    after = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=cluecoder.eval.run={executor.run_id}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert after == before


def test_docker_output_limit_stops_noisy_container(executor):
    assert "output limit" in executor.run("python -c 'print(\"x\" * 2000000)'", timeout=10)
    assert not subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=cluecoder.eval.run={executor.run_id}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_interrupt_cleans_up_docker_client_and_container(executor, monkeypatch):
    real_sleep = time.sleep

    def interrupt_after_start(seconds):
        real_sleep(0.5)
        raise KeyboardInterrupt

    # Replacing the shared time.sleep also interrupts POSIX subprocess.wait in cleanup.
    monkeypatch.setattr(execution_module, "time", SimpleNamespace(
        sleep=interrupt_after_start, monotonic=time.monotonic,
    ))
    with pytest.raises(KeyboardInterrupt):
        executor.run("sleep 15", timeout=20)
    assert not subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=cluecoder.eval.run={executor.run_id}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.mark.parametrize(("name", "uid", "gid", "expected"), [
    ("posix", 1001, 1002, "1001:1002"),
    ("posix", 0, 0, "65534:65534"),
    ("nt", 0, 0, "65534:65534"),
])
def test_sandbox_user_matches_nonroot_caller_with_safe_fallback(monkeypatch, name, uid, gid, expected):
    monkeypatch.setattr(execution_module, "os", SimpleNamespace(
        name=name, getuid=lambda: uid, getgid=lambda: gid,
    ))
    assert _sandbox_user() == expected


def test_private_workspace_is_writable_without_root(tmp_path, docker_ready):
    if sys.platform != "linux" or _sandbox_user() == "65534:65534":
        pytest.skip("Native non-root Linux UID ownership case")
    tmp_path.chmod(0o700)
    executor = DockerExecutor(tmp_path)
    result = executor.run("id -u; printf caller-owned > created.txt")
    assert result == _sandbox_user().split(":")[0]
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "caller-owned"
    assert (tmp_path / "created.txt").stat().st_uid == execution_module.os.getuid()
