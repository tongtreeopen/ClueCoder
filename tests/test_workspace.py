"""Workspace policy for model-facing file tools."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tracecoder.tools.edit import EditFileTool
from tracecoder.tools.glob_tool import GlobTool
from tracecoder.tools.grep import GrepTool
from tracecoder.tools.read import ReadFileTool
from tracecoder.tools.write import WriteFileTool
from tracecoder.workspace import WorkspacePolicy


def test_in_root_paths_work_for_all_file_tools(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    policy = WorkspacePolicy(root)
    target = root / "src" / "a.py"
    assert WriteFileTool(policy).execute("src/a.py", "needle = 1\n").startswith("Wrote")
    assert "needle = 1" in ReadFileTool(policy).execute(str(target))
    assert "Edited" in EditFileTool(policy).execute("src/a.py", "needle = 1", "needle = 2")
    assert str(target) in GlobTool(policy).execute("**/*.py")
    assert "needle = 2" in GrepTool(policy).execute("needle", path="src")


def test_outside_paths_rejected_without_side_effects(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("SECRET", encoding="utf-8")
    policy = WorkspacePolicy(root)
    for path in (str(target), "../outside/secret.txt"):
        assert "outside workspace" in ReadFileTool(policy).execute(path)
        assert "outside workspace" in EditFileTool(policy).execute(path, "SECRET", "changed")
        assert "outside workspace" in WriteFileTool(policy).execute(path, "changed")
    assert target.read_text(encoding="utf-8") == "SECRET"
    assert not (outside / "new").exists()
    assert "outside workspace" in WriteFileTool(policy).execute("../outside/new/deep.txt", "x")
    assert not (outside / "new").exists()
    assert "outside workspace" in GlobTool(policy).execute("*.txt", path=outside)
    assert "outside workspace" in GrepTool(policy).execute("SECRET", path=outside)


def test_search_does_not_follow_links_outside_root(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("UNIQUE_SECRET", encoding="utf-8")
    try:
        (root / "linked.txt").symlink_to(outside / "secret.txt")
        (root / "linked_dir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host cannot create test symlinks")
    policy = WorkspacePolicy(root)
    assert "UNIQUE_SECRET" not in ReadFileTool(policy).execute("linked.txt")
    assert "outside workspace" in WriteFileTool(policy).execute("linked_dir/new.txt", "changed")
    assert not (outside / "new.txt").exists()
    assert "linked.txt" not in GlobTool(policy).execute("*.txt")
    assert "UNIQUE_SECRET" not in GrepTool(policy).execute("UNIQUE_SECRET")
    assert "outside workspace" in GlobTool(policy).execute("../outside/*.txt")


def test_policy_root_is_fixed_and_parallel_instances_are_independent(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_policy = WorkspacePolicy(first)
    second_policy = WorkspacePolicy(second)
    assert first_policy.root == Path(first).resolve()
    assert "outside workspace" in WriteFileTool(first_policy).execute(second / "a.txt", "x")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: WriteFileTool(pair[0]).execute("a.txt", pair[1]), [
            (first_policy, "first"), (second_policy, "second"),
        ]))
    assert all(result.startswith("Wrote") for result in results)
    assert (first / "a.txt").read_text(encoding="utf-8") == "first"
    assert (second / "a.txt").read_text(encoding="utf-8") == "second"


def test_windows_junction_cannot_escape_workspace(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction case")
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.skip("This host cannot create test junctions")
    assert "outside workspace" in WriteFileTool(WorkspacePolicy(root)).execute("junction/new.txt", "x")
    assert not (outside / "new.txt").exists()
