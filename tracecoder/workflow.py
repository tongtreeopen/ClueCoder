"""Trusted, serial workflows with source-bound resumable steps."""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .agent import Agent
from .permissions import Permission
from .tools.agent import AgentTool
from .tools.glob_tool import GlobTool
from .tools.grep import GrepTool
from .tools.read import ReadFileTool
from .workflow_budget import WorkflowModel
from .workflow_journal import WorkflowJournal, atomic_json, digest
from .workspace import WorkspacePolicy


def git(workspace: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True, timeout=30).stdout


def capture_source(workspace: Path) -> dict:
    if Path(git(workspace, "rev-parse", "--show-toplevel").decode().strip()).resolve() != workspace:
        raise ValueError("Workflow workspace must be the Git repository root")
    others = git(workspace, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    if any(path and not path.startswith(".tracecoder/") for path in others):
        raise ValueError("Workflow requires tracked source files; add untracked files first")
    files = {}
    for name in git(workspace, "ls-files", "-z").decode().split("\0"):
        if not name:
            continue
        path = workspace / name
        if name.startswith((".tracecoder/", ".git/")) or path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise ValueError("Workflow source contains internal files or an escaping link")
        if path.is_dir():
            raise ValueError("Workflow submodules are not supported")
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    diff = git(workspace, "diff", "--binary", "HEAD").decode("utf-8", errors="replace")
    if len(diff) > 24000:
        raise ValueError("Workflow diff exceeds 24000 characters; narrow the change")
    return {"head": git(workspace, "rev-parse", "HEAD").decode().strip(), "files": files,
            "staged": hashlib.sha256(git(workspace, "diff", "--binary", "--cached")).hexdigest(),
            "unstaged": hashlib.sha256(git(workspace, "diff", "--binary")).hexdigest(),
            "diff_sha256": digest(diff), "diff": diff}


def model_config(model) -> dict:
    from .llm import LiteLLM

    base = str(getattr(model, "base_url", getattr(getattr(model, "client", None), "base_url", "")))
    parsed = urlsplit(base)
    if parsed.username or parsed.password or parsed.query:
        raise ValueError("Workflow model endpoint cannot contain credentials or query parameters")
    config = {"model": model.model, "provider": getattr(model, "provider", "litellm" if isinstance(
        getattr(model, "_model", model), LiteLLM) else "openai"), "base_url": base,
              "extra": getattr(model, "extra", {})}
    from .session_journal import _redact

    if _redact(config) != config:
        raise ValueError("Workflow model configuration contains secrets")
    digest(config)
    return config


@dataclass(frozen=True)
class WorkflowIdentity:
    workspace: str
    workflow: str
    version: str
    args: dict
    source_sha256: str
    model: dict
    contract: int = 1

    def as_dict(self) -> dict:
        return dict(vars(self))


class WorkflowStopped(RuntimeError):
    def __init__(self, status: str, report: dict):
        super().__init__(report.get("summary", status))
        self.status, self.report = status, report


@dataclass(frozen=True)
class ReviewPolicy(WorkspacePolicy):
    tracked: frozenset[str] | None = None

    def resolve(self, path):
        resolved = super().resolve(path)
        if resolved.relative_to(self.root).parts and resolved.relative_to(self.root).parts[0].casefold() in {".tracecoder", ".git"}:
            raise ValueError("Workflow cannot read internal runtime files")
        if resolved.is_file() and self.tracked is not None and resolved.relative_to(self.root).as_posix() not in self.tracked:
            raise ValueError("Workflow can only read captured tracked files")
        return resolved


class WorkflowContext:
    def __init__(self, runtime, journal, source):
        self.runtime, self.journal, self.source = runtime, journal, source
        self.model = WorkflowModel(runtime.model, journal, runtime.limits, runtime.goal, runtime.on_usage)
        self.labels: dict[str, tuple[str, dict]] = {}
        self.cached_steps = 0

    def check_source(self):
        if digest(capture_source(self.runtime.workspace)) != digest(self.source):
            raise ValueError("Workflow source changed during execution")

    def step(self, label: str, prompt: str, dependencies: list[str] | None = None) -> dict:
        self.check_source()
        dependencies = dependencies or []
        if not label or any(name not in self.labels or name == label for name in dependencies):
            raise ValueError("Invalid workflow label or dependencies")
        key = digest({"identity": self.journal.identity, "label": label, "prompt": prompt, "schema": 1,
                      "dependencies": [self.labels[name] for name in dependencies]})
        if label in self.labels and self.labels[label][0] != key:
            raise ValueError("Workflow label reused with different input")
        cached = self.journal.completed_steps.get(key)
        if cached is not None:
            self.cached_steps += 1
            self.labels[label] = (key, cached)
            self.journal.append("step_cached", {"key": key, "label": label})
            return cached
        self.journal.append("step_started", {"key": key, "label": label})
        policy = ReviewPolicy(self.runtime.workspace, frozenset(self.source["files"]))
        tool = AgentTool()
        parent = Agent(self.model, tools=[ReadFileTool(policy), GlobTool(policy), GrepTool(policy)],
                       permission=Permission(allow_all=True), on_subagent_event=self.record_child)
        tool._parent_agent = parent
        result = json.loads(tool.execute(prompt, output_format="structured"))
        self.check_source()
        if result["status"] != "completed":
            self.journal.append("step_failed", {"key": key, "label": label, "result": result})
            raise WorkflowStopped(result["status"], result)
        self.journal.append("step_completed", {"key": key, "label": label, "result": result})
        self.labels[label] = (key, result)
        return result

    def record_child(self, child_id: str, kind: str, data: dict):
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 32000:
            data = {"preview": json.dumps(data, ensure_ascii=False)[:8000], "sha256": digest(data),
                    "tool_call_id": data.get("tool_call_id"), "truncated": True}
        self.journal.append("child_event", {"child_id": child_id, "kind": kind, "detail": data})


class WorkflowRuntime:
    def __init__(self, workspace: Path, model, registry: dict | None = None, limits: dict | None = None,
                 goal=None, on_usage=None):
        from .workflows.review import review_changes

        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".tracecoder" / "workflows"
        self.model = model
        self.goal, self.on_usage = goal, on_usage
        self.registry = registry if registry is not None else {"review-changes": ("1", review_changes)}
        self.limits = {"calls": 50, "tokens": 100000, "seconds": 900, **(limits or {})}
        if set(self.limits) != {"calls", "tokens", "seconds"} or any(
                type(value) not in (int, float) or value <= 0 for value in self.limits.values()):
            raise ValueError("Invalid workflow limits")

    def run(self, name: str, args: dict | None = None, resume_from_run_id: str | None = None) -> dict:
        if name not in self.registry:
            raise ValueError("Unknown workflow")
        if args is not None and (not isinstance(args, dict) or args):
            raise ValueError("This workflow accepts only empty args")
        version, script = self.registry[name]
        source = capture_source(self.workspace)
        identity = WorkflowIdentity(str(self.workspace), name, version, args or {}, digest(source),
                                    {**model_config(self.model), "limits": self.limits}).as_dict()
        journal = (WorkflowJournal.open(self.root, resume_from_run_id) if resume_from_run_id
                   else WorkflowJournal.create(self.root, identity))
        with journal.lock:
            journal.reload(repair=True)
            if journal.identity != identity:
                raise ValueError("Workflow resume identity mismatch; start a new run")
            if resume_from_run_id and any(event["kind"] == "step_completed" and not event["data"].get("cacheable", True)
                                          for event in journal.events):
                raise ValueError("Workflow has noncacheable redacted results; start a new run")
            journal.append("run_resumed" if resume_from_run_id else "run_started", {})
            context = WorkflowContext(self, journal, source)
            try:
                result = script(context, args or {})
                context.check_source()
                status = "completed"
            except WorkflowStopped as error:
                status, result = error.status, error.report
            except KeyboardInterrupt:
                journal.append("run_interrupted", {"usage": context.model.usage()})
                raise
            except Exception as error:
                status, result = "error", {"summary": type(error).__name__, "blockers": [str(error)]}
            report = {"run_id": journal.run_id, "status": status, "cached_steps": context.cached_steps,
                      "completed_steps": len(journal.completed_steps), "usage": context.model.usage(),
                      "result": result, "artifacts": str(journal.directory)}
            journal.append("run_" + status, report)
            from .session_journal import _redact

            atomic_json(journal.directory / "result.json", _redact(report))
            return report

    def status(self, run_id: str) -> dict:
        journal = WorkflowJournal(self.root, run_id)
        with journal.lock:
            journal.reload()
            terminal = next((event["data"] for event in reversed(journal.events)
                             if event["kind"] in {"run_completed", "run_error", "run_limit", "run_blocked", "run_interrupted"}), None)
            kind = journal.events[-1]["kind"] if journal.events else "created"
            return {"run_id": run_id, "status": kind.removeprefix("run_") if kind.startswith("run_") else "running",
                    "completed_steps": len(journal.completed_steps), "last_result": terminal,
                    "artifacts": str(journal.directory)}
