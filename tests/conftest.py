"""Shared pytest fixtures and helpers."""

import pytest

from cluecoder.execution import DockerExecutor
from cluecoder.tools import default_tools
from cluecoder.tools.bash import BashTool


def get_tool(name: str):
    """Look up a tool by name."""
    if name == "bash":
        return BashTool()
    for t in default_tools():
        if t.name == name:
            return t
    return None


@pytest.fixture
def docker_ready():
    if not DockerExecutor.available():
        pytest.skip("Pinned local Docker image or Linux daemon is unavailable")


@pytest.fixture
def tool_workspace(tmp_path, monkeypatch):
    """Bind legacy tool tests to their temporary workspace without relaxing policy."""
    monkeypatch.chdir(tmp_path)
