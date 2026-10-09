"""Run a fixed evaluation suite with no dotenv or dependency downloads."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

from tracecoder.eval.cases import _tree_hash, load_suite, materialize
from tracecoder.eval.gate import run_gate
from tracecoder.eval.report import render_markdown, summarize
from tracecoder.eval.runner import (
    SOURCE_ROOT,
    _clean,
    _cleanup_workers,
    _json,
    _prepare_workspace,
    _redactor,
    _revision,
    run_case,
)
from tracecoder.execution import DEFAULT_IMAGE, DockerExecutor

TASK_IDS = [
    "csv_quotes",
    "retry_backoff",
    "ttl_cache",
    "tree_walk",
    "date_ranges",
    "config_merge",
    "log_filter",
    "dependency_order",
]
HOLDOUT_TASK_IDS = [
    "chunked_iterable",
    "stable_unique",
    "query_parameters",
    "bounded_queue",
    "weighted_average",
    "text_edits",
    "balanced_brackets",
    "ragged_transpose",
]
SUITES = {"tracecoder-baseline-v1": TASK_IDS, "tracecoder-holdout-v1": HOLDOUT_TASK_IDS}


def run_suite(manifest: Path, output: Path, model: str, provider: str, base_url: str | None, max_tokens: int = 100000) -> dict:
    if max_tokens < 1:
        raise ValueError("Token budget must be positive")
    output = output.resolve()
    if output.is_relative_to(SOURCE_ROOT) or output.is_relative_to(manifest.resolve().parent):
        raise ValueError("Output must be outside the Agent checkout and suite directory")
    output.mkdir(parents=True, exist_ok=False)
    redact = _redactor(dict(os.environ))
    results, checks = [], []
    scheduled = TASK_IDS
    metadata = {
        "suite_id": "tracecoder-baseline-v1",
        "suite_sha256": None,
        "configuration": {
            "model": model,
            "provider": provider,
            "base_url": "[UNVALIDATED_URL]" if base_url else None,
            "image": DEFAULT_IMAGE,
            "temperature": 0,
            "max_calls": 50,
            "max_tokens": max_tokens,
            "seconds": 900,
            "gate_seconds": 120,
            "max_output_tokens": 4096,
            "max_context_tokens": 128000,
        },
        "preflight_error": None,
    }
    try:
        declared = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(declared, dict) and isinstance(declared.get("id"), str) and declared["id"] in SUITES:
            scheduled = SUITES[declared["id"]]
            metadata["suite_id"] = declared["id"]
        suite = load_suite(manifest)
        metadata.update(suite_id=suite.id, suite_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        scheduled = SUITES.get(suite.id, TASK_IDS)
        if suite.id not in SUITES or [task.id for task in suite.tasks] != scheduled:
            raise ValueError("Expected one of the fixed eight-task suites with all tasks in order")
        metadata["gate_hashes"] = {task.id: _tree_hash(task.gate_dir) for task in suite.tasks}
        metadata["snapshot_hashes"] = {task.id: task.snapshot_sha256 for task in suite.tasks}
        metadata["gate_commands"] = {task.id: task.gate_command for task in suite.tasks}
        if not model.strip() or provider not in {"openai", "litellm"}:
            raise ValueError("Explicit model and supported provider required")
        if provider == "litellm" and find_spec("litellm") is None:
            raise ValueError("The optional litellm provider dependency is unavailable")
        if base_url:
            metadata["configuration"]["base_url"] = "[INVALID_URL]"
            url = urlsplit(base_url)
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("Invalid base URL port")
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Base URL must be HTTP(S) without credentials, query or fragment")
            metadata["configuration"]["base_url"] = base_url
        if not any(os.environ.get(key) for key in ("TRACECODER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")):
            raise ValueError("Missing credential: set TRACECODER_API_KEY, OPENAI_API_KEY or DEEPSEEK_API_KEY in the environment")
        metadata["agent_revision"] = _revision(SOURCE_ROOT)
        with tempfile.TemporaryDirectory(prefix="tracecoder-suite-preflight-") as temporary:
            for task in suite.tasks:
                workspace = Path(temporary).resolve() / task.id
                materialize(task, workspace)
                _prepare_workspace(workspace)
                run_id = uuid.uuid4().hex
                try:
                    ready = DockerExecutor(workspace, DEFAULT_IMAGE, run_id).run(
                        "test -w /workspace && test ! -e /gate && printf tracecoder-eval-ready", timeout=15
                    )
                finally:
                    cleaned = _cleanup_workers(run_id)
                if ready != "tracecoder-eval-ready" or not cleaned:
                    raise RuntimeError(f"Worker Docker preflight or cleanup failed: {ready}")
                gate = run_gate(task, workspace, DEFAULT_IMAGE)
                checks.append({"task_id": task.id, **asdict(gate)})
                if gate.status != "test_fail":
                    raise RuntimeError(f"{task.id}: initial snapshot must fail its Gate; got {gate.status}")
        # Validate every input again after preflight, before the first provider request.
        if load_suite(manifest) != suite or hashlib.sha256(manifest.read_bytes()).hexdigest() != metadata["suite_sha256"]:
            raise ValueError("Suite changed during preflight")
        for task in suite.tasks:
            if _tree_hash(task.gate_dir) != metadata["gate_hashes"][task.id]:
                raise ValueError("Gate changed during preflight")
        for task in suite.tasks:
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != metadata["suite_sha256"] or any(
                _tree_hash(item.gate_dir) != metadata["gate_hashes"][item.id] for item in suite.tasks
            ):
                raise ValueError("Suite changed during evaluation")
            results.append(run_case(task, output, model, provider, base_url, DEFAULT_IMAGE, max_tokens))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        metadata["preflight_error"] = str(error)
    for task_id in scheduled[len(results) :]:
        results.append(
            {
                "task_id": task_id,
                "status": "setup_error",
                "gate_status": None,
                "error": metadata["preflight_error"] or "Scheduled case did not run",
            }
        )
    summary = _clean({**summarize(results, len(scheduled)), **metadata, "preflight_checks": checks}, redact)
    (output / "summary.json").write_bytes(_json(summary).encode("utf-8"))
    (output / "summary.md").write_bytes(render_markdown(summary).encode("utf-8"))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--provider", default="openai", choices=["openai", "litellm"])
    run.add_argument("--base-url")
    run.add_argument("--max-tokens", type=int, default=100000)
    args = parser.parse_args()
    summary = run_suite(args.suite, args.output, args.model, args.provider, args.base_url, args.max_tokens)
    print(render_markdown(summary))
    return 0 if summary["protocol_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
