"""Tool registry."""

from pathlib import Path

from ..workspace import WorkspacePolicy
from .agent import AgentTool
from .base import Tool
from .bash import DisabledBashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .todo import TodoWriteTool
from .write import WriteFileTool


def default_tools() -> list[Tool]:
    policy = WorkspacePolicy(Path.cwd())
    return [DisabledBashTool(), ReadFileTool(policy), WriteFileTool(policy), EditFileTool(policy),
            GlobTool(policy), GrepTool(policy), TodoWriteTool(), AgentTool()]


ALL_TOOLS = default_tools()

