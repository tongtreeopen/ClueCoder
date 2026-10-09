"""Offline scripted demo: watch the full agent loop with no API key.

Runs one scripted task through the real Agent loop with a ScriptedLLM, so the
thought -> tool call -> observation cycle renders exactly as it would against
a live model. Useful for demos, screenshots, and as a smoke test of the loop.
"""

import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .agent import Agent
from .cli import _bind_work, _brief
from .cli_memory import MemoryCLI
from .execution import DockerExecutor
from .llm import LLMResponse, ToolCall
from .memory import MemoryStore
from .memory_workflow import MemoryWorkflow
from .session_journal import SessionJournal
from .tools.bash import BashTool, DisabledBashTool, DockerBashTool
from .tools.read import ReadFileTool
from .tools.session import ReadSessionEventTool, SearchSessionTool
from .tools.work import ReadWorkStateTool, WorkUpdateTool
from .tools.write import WriteFileTool
from .work_state import WorkStateStore
from .workspace import WorkspacePolicy

console = Console()


class ScriptedLLM:
    """Deterministic offline LLM for demos and smoke tests.

    Plays back a list of LLMResponse turns, one per chat() call, streaming
    each turn's content through on_token. Running out of turns is an error,
    not a silent hang, so a broken loop shows up immediately.
    """

    total_prompt_tokens = 0
    total_completion_tokens = 0

    def __init__(self, script: list[LLMResponse], model: str = "scripted-demo"):
        self._turns = list(script)
        self.model = model

    def chat(self, messages, tools=None, on_token=None, max_output_tokens=None) -> LLMResponse:
        if not self._turns:
            raise RuntimeError("ScriptedLLM ran out of turns")
        resp = self._turns.pop(0)
        if on_token and resp.content:
            on_token(resp.content)
        self.total_completion_tokens += len(resp.content.split())
        return resp


_TASK = "Write a Python function `fib(n)` in fib.py returning the nth Fibonacci number, add a pytest file, then run the tests."


def _script(workdir: Path, strict: bool = False) -> list[LLMResponse]:
    check = (
        f"import sys; sys.path.insert(0, {str(workdir)!r}); "
        "from test_fib import test_fib; test_fib(); print('demo check passed')"
    )
    test_args = [sys.executable, "-c", check]
    test_command = subprocess.list2cmdline(test_args) if sys.platform == "win32" else shlex.join(test_args)
    fib_py = (
        "def fib(n):\n"
        "    if n < 2:\n"
        "        return n\n"
        "    a, b = 0, 1\n"
        "    for _ in range(2, n + 1):\n"
        "        a, b = b, a + b\n"
        "    return b\n"
    )
    test_py = "from fib import fib\n\ndef test_fib():\n    assert fib(0) == 0\n    assert fib(1) == 1\n    assert fib(10) == 55\n"
    return [
        LLMResponse(
            content="I'll write fib.py with an iterative implementation.",
            tool_calls=[
                ToolCall(id="c1", name="write_file", arguments={"file_path": str(workdir / "fib.py"), "content": fib_py})
            ],
        ),
        LLMResponse(
            content="Now the test file covering the base cases and fib(10).",
            tool_calls=[
                ToolCall(id="c2", name="write_file", arguments={"file_path": str(workdir / "test_fib.py"), "content": test_py})
            ],
        ),
        LLMResponse(
            content="Running the tests.",
            tool_calls=[
                ToolCall(
                    id="c3",
                    name="bash",
                    arguments={
                        "command": (
                            f"cd /workspace/{workdir.name} && python -c 'from test_fib import test_fib; test_fib()' "
                            "&& echo 'sandbox test passed'"
                            if strict
                            else test_command
                        ),
                    },
                )
            ],
        ),
        LLMResponse(
            content=(
                "The files were written and a sandbox test was requested. "
                "Check the bash tool result for the actual status; this scripted reply cannot verify it."
                if strict
                else "The script requested tests for the base cases and fib(10). Check the actual tool result below."
            )
        ),
    ]


def run_demo(strict: bool = False, workspace: Path | None = None) -> int:
    workdir = (
        Path(tempfile.mkdtemp(prefix="cluecoder-demo-", dir=workspace))
        if strict
        else Path(tempfile.mkdtemp(prefix="cluecoder-demo-"))
    )
    if strict:
        root = workspace or Path.cwd()
        journal = SessionJournal.create(workdir / "sessions", root, "scripted-demo")
        executor = DockerExecutor.try_create(root)
        policy = WorkspacePolicy(root)
        tools = [DockerBashTool(executor) if executor else DisabledBashTool(), ReadFileTool(policy), WriteFileTool(policy)]
        memory_store = MemoryStore(workdir)
        memory = MemoryCLI(
            MemoryWorkflow(memory_store, ScriptedLLM([LLMResponse(content="[]"), LLMResponse(content="[]")])),
            lambda: "",
            notify=lambda message: console.print(message, markup=False),
        )
        memory.recall_enabled = False
        work = WorkStateStore(journal)
        tools.extend([WorkUpdateTool(work), ReadWorkStateTool(work), SearchSessionTool(journal), ReadSessionEventTool(journal)])
        agent = Agent(llm=ScriptedLLM(_script(workdir, strict=True)), tools=tools, context_catalog=memory.context)
        _bind_work(agent, journal, memory, work)
        memory.begin_turn(_TASK)
    else:
        agent = Agent(llm=ScriptedLLM(_script(workdir)), tools=[WriteFileTool(WorkspacePolicy(workdir)), BashTool()])

    console.print(Panel.fit(f"[bold]{_TASK}[/]", title="cluecoder demo (offline)"))
    result = agent.chat(
        _TASK,
        on_tool=lambda name, args: console.print(f"[cyan]tool:[/] {name} {_brief(args)}"),
    )
    test_result = next(message["content"] for message in agent.messages
                       if message.get("role") == "tool" and message.get("tool_call_id") == "c3")
    console.print("Test result: " + test_result, markup=False)
    console.print(Panel.fit(Markdown(result), title="final"))
    if strict:
        memory.after_turn(agent, _TASK, interactive=False)
        console.print(f"[dim]Session: {journal.id}[/]")
    console.print(f"[dim]workspace kept at {workdir}[/]")
    return 1 if any(marker in test_result for marker in ("Error:", "[exit code:", " failed")) else 0
