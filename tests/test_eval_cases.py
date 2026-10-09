import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cluecoder.eval.cases import _tree_hash as hash_tree
from cluecoder.eval.cases import load_suite, materialize


def _tree_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        path = name.encode("utf-8")
        digest.update(len(path).to_bytes(8, "big") + path)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _suite(tmp_path: Path) -> Path:
    case = tmp_path / "task"
    (case / "snapshot").mkdir(parents=True)
    (case / "gate").mkdir()
    (case / "snapshot" / "solution.py").write_bytes(b"def solve():\n    return None\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "id": "baseline-v1",
        "tasks": [{
            "id": "repair",
            "prompt": "Fix solve.",
            "snapshot_dir": "task/snapshot",
            "snapshot_sha256": _tree_hash({"solution.py": b"def solve():\n    return None\n"}),
            "gate_dir": "task/gate",
            "gate_command": ["python", "-m", "unittest", "discover", "-s", "/gate"],
            "allowed_paths": ["solution.py"],
        }],
    }), encoding="utf-8")
    return manifest


def test_suite_rejects_duplicate_or_escaping_cases(tmp_path: Path):
    manifest = _suite(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert load_suite(manifest).tasks[0].gate_command == ["python", "-m", "unittest", "discover", "-s", "/gate"]

    for change in (
        {"tasks": [data["tasks"][0], data["tasks"][0]]},
        {"tasks": [{**data["tasks"][0], "snapshot_dir": "../outside"}]},
        {"tasks": [{**data["tasks"][0], "gate_dir": "task/snapshot"}]},
        {"tasks": [{**data["tasks"][0], "allowed_paths": ["../escape.py"]}]},
        {"tasks": [{**data["tasks"][0], "gate_command": "python -m unittest"}]},
        {"tasks": [{**data["tasks"][0], "unexpected": True}]},
    ):
        manifest.write_text(json.dumps({**data, **change}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_suite(manifest)

    manifest.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "task" / "snapshot" / "link.py").symlink_to(tmp_path / "task" / "gate", target_is_directory=True)
    with pytest.raises(ValueError):
        load_suite(manifest)


def test_materialize_matches_snapshot_hash(tmp_path: Path):
    manifest = _suite(tmp_path)
    task = load_suite(manifest).tasks[0]
    destination = tmp_path / "fresh"
    assert materialize(task, destination) == task.snapshot_sha256
    assert (destination / "solution.py").read_bytes() == b"def solve():\n    return None\n"
    with pytest.raises(FileExistsError):
        materialize(task, destination)
    with pytest.raises(ValueError):
        materialize(task, task.snapshot_dir / "nested")
    with pytest.raises(ValueError):
        materialize(task, task.gate_dir / "nested")

    (tmp_path / "task" / "snapshot" / "solution.py").write_bytes(b"changed\n")
    with pytest.raises(ValueError):
        load_suite(manifest)
    with pytest.raises(ValueError):
        materialize(task, tmp_path / "another")


def test_tree_hash_separates_file_boundaries(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.bin").write_bytes(b"x\0b.bin\0y")
    (second / "a.bin").write_bytes(b"x")
    (second / "b.bin").write_bytes(b"y")
    assert hash_tree(first) != hash_tree(second)


@pytest.mark.parametrize("replaced", ["snapshot", "task"])
def test_materialize_rejects_replaced_source_symlink(tmp_path: Path, replaced: str):
    suite = tmp_path / "suite"
    suite.mkdir()
    task = load_suite(_suite(suite)).tasks[0]
    original = suite / "task" / "snapshot" if replaced == "snapshot" else suite / "task"
    outside = tmp_path / "outside"
    shutil.move(original, outside)
    original.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        materialize(task, tmp_path / "fresh")
