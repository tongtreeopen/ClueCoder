"""The held-out fixtures discriminate and the CLI schedules only fixed suites."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cluecoder.eval.__main__ import HOLDOUT_TASK_IDS, TASK_IDS
from cluecoder.eval.cases import load_suite, materialize
from cluecoder.eval.gate import run_gate
from cluecoder.execution import DEFAULT_IMAGE

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "benchmarks/cluecoder-holdout-v1"
COMMAND = ["python", "-m", "unittest", "discover", "-s", "/gate", "-p", "test_*.py"]


def test_all_holdout_cases_discriminate_in_real_docker(docker_ready, tmp_path: Path):
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", DEFAULT_IMAGE],
                              capture_output=True, text=True, check=True).stdout.strip()
    suite = load_suite(HOLDOUT / "manifest.json")
    assert suite.id == "cluecoder-holdout-v1"
    assert [task.id for task in suite.tasks] == HOLDOUT_TASK_IDS
    assert set(HOLDOUT_TASK_IDS).isdisjoint(TASK_IDS)
    assert len({task.snapshot_sha256 for task in suite.tasks}) == 8
    for task in suite.tasks:
        assert task.gate_command == COMMAND
        assert {path.relative_to(task.snapshot_dir).as_posix() for path in task.snapshot_dir.rglob("*")} == {
            "solution.py"
        }
        assert task.allowed_paths == ["solution.py"]
        assert (task.gate_dir / "test_solution.py").is_file()
        workspace = tmp_path / task.id
        assert materialize(task, workspace) == task.snapshot_sha256
        broken = run_gate(task, workspace, DEFAULT_IMAGE)
        assert broken.status == "test_fail" and broken.exit_code == 1, (task.id, broken)
        patch = task.snapshot_dir.parent / "reference.patch"
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=workspace, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(patch)], cwd=workspace, check=True, capture_output=True)
        fixed = run_gate(task, workspace, DEFAULT_IMAGE)
        assert fixed.status == "pass" and fixed.exit_code == 0, (task.id, fixed)
        assert broken.image_id == fixed.image_id == image_id
        print(f"{task.id}: base={broken.status}, reference={fixed.status}, image={fixed.image_id}")


def _run_without_credentials(manifest: Path, output: Path) -> dict:
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
    completed = subprocess.run([sys.executable, "-m", "cluecoder.eval", "run", "--suite", str(manifest),
                                "--output", str(output), "--model", "test"], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 2, completed.stdout + completed.stderr
    return json.loads((output / "summary.json").read_text(encoding="utf-8"))


def test_holdout_cli_keeps_eight_rows_on_missing_credentials(tmp_path: Path):
    summary = _run_without_credentials(HOLDOUT / "manifest.json", tmp_path / "report")
    assert summary["suite_id"] == "cluecoder-holdout-v1"
    assert [item["task_id"] for item in summary["results"]] == HOLDOUT_TASK_IDS
    assert summary["failure_counts"] == {"setup_error": 8}
    assert summary["success_rate"] is None and summary["gate_completed"] == 0
    assert "credential" in summary["preflight_error"].lower()


def test_holdout_cli_rejects_changed_task_order_before_provider(tmp_path: Path):
    copy = tmp_path / "suite"
    shutil.copytree(HOLDOUT, copy)
    manifest = copy / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["tasks"][0], data["tasks"][1] = data["tasks"][1], data["tasks"][0]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    summary = _run_without_credentials(manifest, tmp_path / "report")
    assert [item["task_id"] for item in summary["results"]] == HOLDOUT_TASK_IDS
    assert "fixed eight-task suites" in summary["preflight_error"]
    assert summary["success_rate"] is None


def test_holdout_cli_rejects_unknown_suite_id(tmp_path: Path):
    copy = tmp_path / "suite"
    shutil.copytree(HOLDOUT, copy)
    manifest = copy / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["id"] = "cluecoder-unknown-v1"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    summary = _run_without_credentials(manifest, tmp_path / "report")
    assert "fixed eight-task suites" in summary["preflight_error"]
    assert summary["failure_counts"] == {"setup_error": 8}


def test_holdout_cli_keeps_schedule_when_snapshot_hash_changes(tmp_path: Path):
    copy = tmp_path / "suite"
    shutil.copytree(HOLDOUT, copy)
    (copy / "tasks/chunked_iterable/snapshot/solution.py").write_text("changed\n", encoding="utf-8")
    summary = _run_without_credentials(copy / "manifest.json", tmp_path / "report")
    assert summary["suite_id"] == "cluecoder-holdout-v1"
    assert [item["task_id"] for item in summary["results"]] == HOLDOUT_TASK_IDS
    assert "Snapshot hash mismatch" in summary["preflight_error"]
    assert summary["success_rate"] is None
