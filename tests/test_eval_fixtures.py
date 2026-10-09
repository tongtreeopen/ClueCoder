"""All fixed fixtures must fail before and pass after their exact repair."""

import subprocess
from pathlib import Path

from tracecoder.eval.cases import load_suite, materialize
from tracecoder.eval.gate import run_gate
from tracecoder.execution import DEFAULT_IMAGE


def test_all_fixed_cases_discriminate(docker_ready, tmp_path: Path):
    suite = load_suite(Path(__file__).resolve().parents[1] / "benchmarks/tracecoder-baseline-v1/manifest.json")
    assert suite.id == "tracecoder-baseline-v1"
    assert {task.id for task in suite.tasks} == {
        "csv_quotes", "retry_backoff", "ttl_cache", "tree_walk", "date_ranges", "config_merge",
        "log_filter", "dependency_order",
    }
    assert len(suite.tasks) == len({task.snapshot_sha256 for task in suite.tasks}) == 8
    for task in suite.tasks:
        assert task.gate_command == ["python", "-m", "unittest", "discover", "-s", "/gate", "-p", "test_*.py"]
        assert {path.relative_to(task.snapshot_dir).as_posix() for path in task.snapshot_dir.rglob("*")} == {"solution.py"}
        assert task.allowed_paths == ["solution.py"]
        assert (task.gate_dir / "test_solution.py").is_file()
        workspace = tmp_path / task.id
        assert materialize(task, workspace) == task.snapshot_sha256
        broken = run_gate(task, workspace, DEFAULT_IMAGE)
        assert broken.status == "test_fail", f"{task.id} baseline: {broken.status}\n{broken.output}"
        assert broken.exit_code == 1
        patch = task.snapshot_dir.parent / "reference.patch"
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=workspace, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(patch)], cwd=workspace, check=True, capture_output=True)
        fixed = run_gate(task, workspace, DEFAULT_IMAGE)
        assert fixed.status == "pass", f"{task.id} reference: {fixed.status}\n{fixed.output}"
        assert fixed.exit_code == 0
        assert broken.image_id == fixed.image_id == DEFAULT_IMAGE.rsplit("@", 1)[1]
        print(f"{task.id}: base={broken.status}, reference={fixed.status}, image={fixed.image_id}")
