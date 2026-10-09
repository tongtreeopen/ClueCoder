"""Bash tools for explicit host execution, disabled execution, and isolated Docker tasks."""

import json
import os
import re
import subprocess
import threading
from typing import ClassVar

from ..background import BackgroundManager
from ..execution import DockerExecutor
from .base import Tool

# Thread-local cwd keeps parallel commands from sharing mutable directory state.
_local = threading.local()


def get_tracked_cwd() -> str | None:
    """The cwd tracked for this thread, if any."""
    return getattr(_local, "cwd", None)


def set_tracked_cwd(path: str | None) -> None:
    """Set the tracked cwd for this thread (session handoff at pool edges)."""
    if path is None:
        if hasattr(_local, "cwd"):
            del _local.cwd
    else:
        _local.cwd = path


# patterns that could wreck the filesystem or leak secrets
_DANGEROUS_PATTERNS = [
    # recursive delete aimed at root/home (force flag optional)
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    # recursive (-r/-R) and force (-f) flags together, in any order or spacing
    (r"\brm\b(?=(?:.*\s)?-\w*[rR])(?=(?:.*\s)?-\w*f)", "force recursive delete"),
    # the same, written with long-form flags
    (r"\brm\b.*--recursive\b.*--force\b|\brm\b.*--force\b.*--recursive\b", "force recursive delete"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe curl to shell"),
    (r"\bwget\b.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe wget to shell"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command. Returns stdout, stderr, and exit code. "
        "Use this for running tests, installing packages, git operations, etc."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
            },
        },
        "required": ["command"],
    }

    def execute(self, command: str, timeout: int = 120) -> str:
        # safety check
        warning = _check_dangerous(command)
        if warning:
            return f"⚠ Blocked: {warning}\nCommand: {command}\nIf intentional, modify the command to be more specific."

        # use this thread's own tracked working directory
        cwd = get_tracked_cwd() or os.getcwd()

        try:
            proc = subprocess.run(
                command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
            )

            # track cd commands so next command runs in the right place
            if proc.returncode == 0:
                _update_cwd(command, cwd)
            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"
            # keep head + tail to preserve the most useful info
            if len(out) > 15_000:
                out = out[:6000] + f"\n\n... truncated ({len(out)} chars total) ...\n\n" + out[-3000:]
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except Exception as e:  # noqa: BLE001
            # anything else from the OS (spawn failure etc.) also comes back as text
            return f"Error running command: {e}"


class DisabledBashTool(BashTool):
    """CLI placeholder until an isolation backend is verified."""

    description = "Bash is unavailable: no verified isolation backend is configured. Do not claim a command or test ran."

    def execute(self, command: str, timeout: int = 120, run_in_background: bool = False) -> str:
        return "Error: bash unavailable: no verified isolation backend; host shell execution is disabled."


class DockerBashTool(BashTool):
    parameters: ClassVar = {
        **BashTool.parameters,
        "properties": {
            **BashTool.parameters["properties"],
            "run_in_background": {
                "type": "boolean",
                "description": "Return a session task ID immediately; query with background_status.",
            },
        },
    }
    description = (
        "Run a shell command inside an isolated, network-disabled Docker container with only the launch workspace mounted. "
        "Each command starts in /workspace; cd does not persist between calls."
    )

    def __init__(self, executor: DockerExecutor, manager: BackgroundManager | None = None):
        self.executor = executor
        self.manager = manager or BackgroundManager(executor)
        self.description = self.description.replace("/workspace", executor.workspace_target)

    def execute(self, command: str, timeout: int = 120, run_in_background: bool = False) -> str:
        if warning := _check_dangerous(command):
            return f"Blocked: {warning}"
        if type(run_in_background) is not bool:
            raise ValueError("run_in_background must be boolean")
        if run_in_background:
            return json.dumps(
                {
                    "task_id": self.manager.start(command, timeout),
                    "status": "started",
                    "notice": "Not success evidence; query background_status.",
                }
            )
        return self.executor.run(command, timeout)


def _check_dangerous(cmd: str) -> str | None:
    """Return a warning string if the command looks destructive, else None."""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None


def _split_words(statement: str) -> list[str]:
    """Split one statement into words on unquoted whitespace, stripping the
    quotes. Backslashes are kept verbatim (Windows paths must not lose them,
    so this deliberately does not use shlex's posix escape processing)."""
    words: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in statement:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch.isspace():
            if buf:
                words.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        words.append("".join(buf))
    return words


def _split_statements(command: str) -> list[list[str]]:
    """Split a shell command into per-statement word lists on `&&` and `;`,
    honoring quotes. Words come back dequoted, so `cd "my dir"` arrives as
    ["cd", "my dir"] and a separator inside quotes never splits."""
    statements: list[str] = []
    quote: str | None = None
    start = 0
    i = 0
    while i < len(command):
        ch = command[i]
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        else:
            is_and = command[i : i + 2] == "&&"
            if ch == ";" or is_and:
                statement = command[start:i]
                if statement.strip():
                    statements.append(statement)
                i += 2 if is_and else 1
                start = i
                continue
        i += 1
    tail = command[start:]
    if tail.strip():
        statements.append(tail)
    return [_split_words(statement) for statement in statements]


def _update_cwd(command: str, current_cwd: str):
    """Track directory changes from cd commands, per thread."""
    # walk each cd in a && or ; chain, resolving relative targets against the dir the
    # previous cd landed in (not the original cwd) so `cd a && cd b` ends in a/b.
    # a parenthesized group is a subshell — `( cd a )` never changes this shell's
    # cwd, so scrub those before scanning; a bare `cd` goes home.
    scrubbed = re.sub(r"\([^()]*\)", " ", command)
    running = current_cwd
    changed = False
    for words in _split_statements(scrubbed):
        if words and words[0] == "cd":
            target = words[1] if len(words) > 1 else "~"
            new_dir = os.path.normpath(os.path.join(running, os.path.expanduser(target)))
            if os.path.isdir(new_dir):
                running = new_dir
                changed = True
    if changed:
        _local.cwd = running
