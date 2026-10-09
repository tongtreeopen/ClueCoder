"""Real subprocess and Docker coverage for the external evaluation runner."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from tracecoder.eval.runner import (
    _archive_events,
    _child_env,
    _cleanup_workers,
    _model_calls_trace_complete,
    _patch,
    _redactor,
    _revision,
    _run_process,
)
from tracecoder.execution import DEFAULT_IMAGE
from tracecoder.session_journal import SessionJournal, _digest

ROOT = Path(__file__).resolve().parents[1]
SECRET = "synthetic-provider-credential-987654321"


def test_model_calls_trace_requires_each_call_to_be_consistent():
    usage = {"calls": 2, "tokens": 9, "usage_unknown": False, "limit": None}
    trace = {
        "version": 1,
        "limit": None,
        "rejected_attempts": 0,
        "calls": [
            {
                "call_index": 1,
                "actor": "parent",
                "outcome": "completed",
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "usage_status": "complete",
                "cumulative_prompt_tokens": 2,
                "cumulative_completion_tokens": 1,
                "cumulative_tokens": 3,
                "cumulative_usage_unknown": False,
            },
            {
                "call_index": 2,
                "actor": "child:" + "a" * 32,
                "outcome": "completed",
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "usage_status": "complete",
                "cumulative_prompt_tokens": 6,
                "cumulative_completion_tokens": 3,
                "cumulative_tokens": 9,
                "cumulative_usage_unknown": False,
            },
        ],
    }
    assert _model_calls_trace_complete(trace, usage)
    for field, value in (
        ("cumulative_tokens", 4),
        ("cumulative_prompt_tokens", 3),
        ("outcome", "invented"),
        ("usage_status", "partial"),
        ("cumulative_usage_unknown", True),
    ):
        altered = deepcopy(trace)
        altered["calls"][0][field] = value
        assert not _model_calls_trace_complete(altered, usage)
    assert not _model_calls_trace_complete({**trace, "calls": ""}, {**usage, "calls": 0, "tokens": 0})
    assert not _model_calls_trace_complete({**trace, "limit": {}}, usage)


def test_model_calls_trace_accepts_explicit_unknown_usage():
    usage = {"calls": 2, "tokens": 3, "usage_unknown": True, "limit": None}
    trace = {
        "version": 1,
        "limit": None,
        "rejected_attempts": 0,
        "calls": [
            {
                "call_index": 1,
                "actor": "parent",
                "outcome": "completed",
                "prompt_tokens": 3,
                "completion_tokens": None,
                "usage_status": "partial",
                "cumulative_prompt_tokens": 3,
                "cumulative_completion_tokens": 0,
                "cumulative_tokens": 3,
                "cumulative_usage_unknown": True,
            },
            {
                "call_index": 2,
                "actor": "parent",
                "outcome": "provider_error",
                "exception_type": "APIError",
                "prompt_tokens": None,
                "completion_tokens": None,
                "usage_status": "missing",
                "cumulative_prompt_tokens": 3,
                "cumulative_completion_tokens": 0,
                "cumulative_tokens": 3,
                "cumulative_usage_unknown": True,
            },
        ],
    }
    assert _model_calls_trace_complete(trace, usage)
    assert not _model_calls_trace_complete(
        {**trace, "calls": [trace["calls"][0], {**trace["calls"][1], "cumulative_tokens": 4}]}, usage
    )


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def test_archive_rejects_gapped_or_duplicate_journal_sequences(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    journal.append_event("note", {"type": "first"})
    journal.append_event("note", {"type": "second"})
    records = [json.loads(line) for line in journal.events_path.read_text(encoding="utf-8").splitlines()]
    records[1]["seq"] = 1
    records[1]["checksum"] = _digest({key: value for key, value in records[1].items() if key != "checksum"})
    journal.events_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    archived, warnings = _archive_events([journal.events_path], lambda value: value)
    assert len(archived.splitlines()) == 1
    assert warnings and "line 2" in warnings[0]


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    shutil.copytree(ROOT / "tracecoder", root / "tracecoder", ignore=shutil.ignore_patterns("__pycache__"))
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Eval Test", "-c", "user.email=eval@example.invalid", "commit", "-m", "fixture")
    return root


@contextmanager
def provider(write_path="solution.py", repeat_tools=False, shell_write=False):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append(body)
            if len(self.server.requests) == 1:
                function = (
                    {
                        "name": "bash",
                        "arguments": json.dumps(
                            {
                                "command": "printf 'answer = 42\\n' > " + shlex.quote(write_path),
                            }
                        ),
                    }
                    if shell_write
                    else {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "file_path": write_path,
                                "content": "answer = 42\n",
                            }
                        ),
                    }
                )
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "write",
                            "type": "function",
                            "function": {
                                **function,
                            },
                        }
                    ]
                }
            elif repeat_tools:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"read-{len(self.server.requests)}",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps(
                                    {
                                        "file_path": "solution.py",
                                    }
                                ),
                            },
                        }
                    ]
                }
            else:
                delta = {"content": "Done " + SECRET + " password=synthetic-password-value"}
            chunks = [
                {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": "test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test",
                    "choices": [],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                },
            ]
            response = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def launch(source, tmp_path, image, url, max_tokens=100000):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "solution.py").write_text("answer = 0\n", encoding="utf-8")
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "verify.py").write_text(
        "from solution import answer\nassert answer == 42\nprint('" + SECRET + "')\n", encoding="utf-8"
    )
    script = """
import json, sys
from pathlib import Path
from tracecoder.eval.cases import TaskSpec, _tree_hash
from tracecoder.eval.runner import run_case
root = Path(sys.argv[1])
task = TaskSpec('test', 'Fix solution.py so answer is 42.', root / 'snapshot',
                _tree_hash(root / 'snapshot'), root / 'gate',
                ['python', '-c', "exec(open('/gate/verify.py').read())"], ['solution.py'])
print(json.dumps(run_case(task, root / 'output', 'test', 'openai', sys.argv[3], sys.argv[2], int(sys.argv[4]))))
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(source),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TRACECODER_API_KEY": SECRET,
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), image, url, str(max_tokens)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
     check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_runner_isolates_home_and_hides_gate(docker_ready, source, tmp_path):
    env = _child_env(tmp_path / "home", tmp_path / "usage.json", "test", "openai", None, DEFAULT_IMAGE, "a" * 32)
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path / "home")
    assert env["TRACECODER_EVAL_MAX_CALLS"] == "50"
    assert env["TRACECODER_EVAL_MAX_TOKENS"] == "100000"
    assert (
        _child_env(tmp_path / "home", tmp_path / "usage.json", "test", "openai", None, DEFAULT_IMAGE, "a" * 32, max_tokens=30000)[
            "TRACECODER_EVAL_MAX_TOKENS"
        ]
        == "30000"
    )
    assert "TRACECODER_EVAL_MODE" not in os.environ
    with provider() as server:
        result = launch(source, tmp_path, DEFAULT_IMAGE, f"http://127.0.0.1:{server.server_port}/v1", max_tokens=30000)
    assert result["status"] == "pass", result
    assert result["calls"] == 2 and result["tokens"] == 12
    assert result["turn_completed"] is True and result["turn_reason"] == "completed"
    directory = Path(result["run_dir"])
    metadata = json.loads((directory / "run.json").read_text())
    assert metadata["budgets"]["tokens"] == 30000
    assert metadata["agent_revision"] == git(source, "rev-parse", "HEAD").strip()
    assert metadata["image_id"].startswith("sha256:")
    assert metadata["worker_command"][1:4] == ["-m", "tracecoder", "--yes"]
    assert metadata["task_sha256"] and metadata["gate_sha256"] and metadata["snapshot_sha256"]
    assert len(result["session_ids"]) == 1
    assert result["changed_paths"] == ["solution.py"]
    assert result["artifact_hashes"]["events.jsonl"]
    trace = json.loads((directory / "model_calls.json").read_text())
    assert result["model_calls_trace_complete"] is True
    assert (
        result["artifact_hashes"]["model_calls.json"] == hashlib.sha256((directory / "model_calls.json").read_bytes()).hexdigest()
    )
    assert [
        (
            call["call_index"],
            call["prompt_tokens"],
            call["completion_tokens"],
            call["cumulative_tokens"],
            call["actor"],
            call["outcome"],
        )
        for call in trace["calls"]
    ] == [(1, 4, 2, 6, "parent", "completed"), (2, 4, 2, 12, "parent", "completed")]
    assert not any("verify.py" in json.dumps(request) for request in server.requests)
    assert SECRET not in "".join(path.read_text() for path in directory.iterdir())
    assert "[REDACTED_SECRET]" in (directory / "gate.txt").read_text()
    assert "synthetic-password-value" not in (directory / "events.jsonl").read_text()
    for line in (directory / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        checksum = event.pop("checksum")
        assert checksum == _digest(event)


def test_child_env_uses_per_task_token_budget(tmp_path):
    env = _child_env(
        tmp_path / "home", tmp_path / "usage.json", "test", "openai", None, DEFAULT_IMAGE, "a" * 32, max_tokens=30000
    )
    assert env["TRACECODER_EVAL_MAX_TOKENS"] == "30000"


def test_runner_rejects_dirty_official_run(source, tmp_path):
    (source / "untracked.py").write_text("dirty")
    with pytest.raises(ValueError, match="clean"):
        _revision(source)
    with provider() as server:
        result = launch(source, tmp_path, DEFAULT_IMAGE, f"http://127.0.0.1:{server.server_port}/v1")
        assert result["status"] == "setup_error" and not server.requests
    assert result["model_calls_trace_complete"] is False
    assert json.loads((Path(result["run_dir"]) / "model_calls.json").read_text())["calls"] == []


def test_runner_requires_worker_docker(source, tmp_path):
    with provider() as server:
        result = launch(
            source, tmp_path, "missing.invalid/worker@sha256:" + "0" * 64, f"http://127.0.0.1:{server.server_port}/v1"
        )
        assert result["status"] == "setup_error" and not server.requests
    assert result["gate_status"] is None


def test_natural_round_limit_cannot_score_gate_pass(docker_ready, source, tmp_path):
    with provider(repeat_tools=True) as server:
        result = launch(source, tmp_path, DEFAULT_IMAGE, f"http://127.0.0.1:{server.server_port}/v1")
    assert len(server.requests) == 50
    assert result["gate_status"] == "pass" and result["status"] == "agent_limit"
    assert result["calls"] == 50 and result["limit"] is None
    assert result["turn_completed"] is False and result["turn_reason"] == "round_limit"


def test_runner_rejects_forbidden_changes_before_gate(docker_ready, source, tmp_path):
    with provider("unexpected.py", repeat_tools=True, shell_write=True) as server:
        result = launch(source, tmp_path, DEFAULT_IMAGE, f"http://127.0.0.1:{server.server_port}/v1")
    assert result["status"] == "agent_error" and result["turn_completed"] is False
    assert result["forbidden_paths"] == ["unexpected.py"] and result["gate_status"] is None
    assert "unexpected.py" in (Path(result["run_dir"]) / "patch.diff").read_text()


def test_runner_file_tool_refuses_forbidden_path_before_creation(docker_ready, source, tmp_path):
    with provider("unexpected.py") as server:
        result = launch(source, tmp_path, DEFAULT_IMAGE, f"http://127.0.0.1:{server.server_port}/v1")
    assert result["status"] == "test_fail" and result["gate_status"] == "test_fail"
    assert result["forbidden_paths"] == [] and result["changed_paths"] == []
    assert "outside the allowed modification scope" in (Path(result["run_dir"]) / "events.jsonl").read_text()


def test_timeout_cleans_only_run_labeled_containers(docker_ready, tmp_path):
    run_id = uuid.uuid4().hex
    other = subprocess.run(
        ["docker", "run", "-d", "--rm", "--pull=never", "--network=none", DEFAULT_IMAGE, "sleep", "60"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    try:
        env = {
            **os.environ,
            "TRACECODER_EVAL_MODE": "1",
            "TRACECODER_EVAL_RUN_ID": run_id,
            "TRACECODER_EVAL_IMAGE": DEFAULT_IMAGE,
            "PYTHONPATH": str(ROOT),
        }
        command = [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; from tracecoder.execution import DockerExecutor; "
                "DockerExecutor.try_create(Path.cwd()).run('printf started > started.txt; sleep 60', timeout=120)"
            ),
        ]
        outcome = _run_process(command, tmp_path, env, timeout=15)
        assert outcome["timed_out"]
        assert (tmp_path / "started.txt").read_text() == "started"
        assert _cleanup_workers(run_id)
        assert subprocess.run(["docker", "inspect", other], capture_output=True, check=False).returncode == 0
        assert not subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=tracecoder.eval.run={run_id}"], capture_output=True, text=True, check=True
        ).stdout.strip()
    finally:
        subprocess.run(["docker", "rm", "-f", other], capture_output=True, check=False)


def test_runner_redacts_stdout_and_stderr(tmp_path):
    result = _run_process(
        [sys.executable, "-c", "import sys; print(sys.argv[1]); print(sys.argv[1], file=sys.stderr)", SECRET],
        tmp_path,
        dict(os.environ),
        timeout=10,
    )
    redact = _redactor({"TRACECODER_API_KEY": SECRET})
    assert redact(result["stdout"]).strip() == "[REDACTED_SECRET]"
    assert redact(result["stderr"]).strip() == "[REDACTED_SECRET]"


def test_worker_uses_requested_image_and_fails_closed(tmp_path):
    env = {
        **os.environ,
        "TRACECODER_EVAL_MODE": "1",
        "TRACECODER_EVAL_RUN_ID": uuid.uuid4().hex,
        "TRACECODER_EVAL_IMAGE": "missing.invalid/worker@sha256:" + "0" * 64,
        "PYTHONPATH": str(ROOT),
        "PYTHONSAFEPATH": "1",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from tracecoder.execution import DockerExecutor; "
                "DockerExecutor.try_create(Path.cwd()).run('touch HOST_FALLBACK')"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
     check=False)
    assert result.returncode != 0 and "worker Docker image unavailable" in result.stderr
    assert not (tmp_path / "HOST_FALLBACK").exists()


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"answer = 0\n", b"answer = 42"),
        (b"answer = 0", b"answer = 42\n"),
        (b"answer = 0", b"answer = 42"),
    ],
)
def test_patch_replays_files_without_final_newline(tmp_path, before, after):
    (tmp_path / "solution.py").write_bytes(before)
    changed, patch = _patch({"solution.py": before}, {"solution.py": after})
    patch_path = tmp_path / "candidate.patch"
    patch_path.write_bytes(patch.encode())
    git(tmp_path, "init")
    git(tmp_path, "-c", "core.autocrlf=false", "apply", str(patch_path))
    assert changed == ["solution.py"] and (tmp_path / "solution.py").read_bytes() == after


def test_interrupted_journal_keeps_candidate_and_limit_status(docker_ready, source, tmp_path):
    (source / "tracecoder" / "__main__.py").write_text(
        """
import json, os, sys, time
from pathlib import Path
from tracecoder.session_journal import _digest
Path('solution.py').write_bytes(b'answer = 42')
session = Path.home() / '.tracecoder' / 'sessions' / 'interrupted'
session.mkdir(parents=True)
event = {'seq': 1, 'kind': 'message', 'data': {'message': {'role': 'assistant', 'content': 'done'}}, 'redacted': False}
event['checksum'] = _digest(event)
(session / 'events.jsonl').write_bytes((json.dumps(event) + '\\n{"seq": 2, "content": "').encode() + b'\\xe4\\xbd')
Path(os.environ['TRACECODER_EVAL_USAGE_PATH']).write_text(
    json.dumps({'calls': 1, 'tokens': 6, 'usage_unknown': False, 'limit': None}))
Path(os.environ['TRACECODER_EVAL_USAGE_PATH']).with_name('model_calls.json').write_text('{broken')
sys.stdout.write('x' * 5000000)
sys.stdout.flush()
time.sleep(60)
""",
        encoding="utf-8",
    )
    git(source, "add", ".")
    git(source, "-c", "user.name=Eval Test", "-c", "user.email=eval@example.invalid", "commit", "-m", "interrupt fixture")
    result = launch(source, tmp_path, DEFAULT_IMAGE, "http://127.0.0.1:1/v1")
    assert result["status"] == "agent_limit", result
    assert result["output_limit"] is True and result["gate_status"] == "pass"
    assert result["journal_warnings"] == ["interrupted: incomplete final journal record omitted"]
    assert result["model_calls_trace_complete"] is False
    assert result["model_calls_trace_error"] == "JSONDecodeError"
    directory = Path(result["run_dir"])
    assert len((directory / "events.jsonl").read_text().splitlines()) == 1
    assert "answer = 42" in (directory / "patch.diff").read_text()
    assert result["candidate_sha256"] and result["changed_paths"] == ["solution.py"]
    replay = tmp_path / "replay"
    shutil.copytree(tmp_path / "snapshot", replay)
    git(replay, "init")
    git(replay, "-c", "core.autocrlf=false", "apply", str(directory / "patch.diff"))
    assert (replay / "solution.py").read_bytes() == b"answer = 42"


def test_native_linux_workspace_allows_worker_and_host_edits(docker_ready):
    volume = "tracecoder-eval-permissions-" + uuid.uuid4().hex
    subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
    run = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
    ]
    try:
        # A native Linux volume, with a private parent owned by a different host UID.
        setup = """
from pathlib import Path
import os
root = Path('/native/private')
root.mkdir(mode=0o700)
workspace = root / 'workspace'
workspace.mkdir(mode=0o755)
(workspace / 'solution.py').write_text('old')
for path in (root, workspace, workspace / 'solution.py'):
    os.chown(path, 1000, 1000)
"""
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--user=0:0",
                f"--mount=type=volume,source={volume},target=/native",
                DEFAULT_IMAGE,
                "python",
                "-c",
                setup,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        mount = f"--mount=type=volume,source={volume},target=/workspace,volume-subpath=private/workspace"
        subprocess.run(
            [
                *run,
                "--user=65534:65534",
                mount,
                DEFAULT_IMAGE,
                "python",
                "-c",
                "import os; assert not os.access('/workspace', os.W_OK); assert not os.access('/workspace/solution.py', os.W_OK)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        prepare = """
import ast
from pathlib import Path
source = ast.parse(Path('/runner.py').read_text())
function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_prepare_workspace')
exec(compile(ast.Module(body=[function], type_ignores=[]), '/runner.py', 'exec'))
_prepare_workspace(Path('/native/private/workspace'))
assert Path('/native/private').stat().st_mode & 0o777 == 0o700
"""
        subprocess.run(
            [
                *run,
                "--user=1000:1000",
                f"--mount=type=volume,source={volume},target=/native",
                f"--mount=type=bind,source={ROOT / 'tracecoder/eval/runner.py'},target=/runner.py,readonly",
                DEFAULT_IMAGE,
                "python",
                "-c",
                prepare,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            [
                *run,
                "--user=65534:65534",
                mount,
                DEFAULT_IMAGE,
                "/bin/sh",
                "-c",
                "umask 000; printf changed > /workspace/solution.py; mkdir /workspace/new; printf created > /workspace/new/file",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        verify = """
from pathlib import Path
assert Path('/workspace/solution.py').read_text() == 'changed'
assert Path('/workspace/new/file').read_text() == 'created'
Path('/workspace/new/file').write_text('host edited')
"""
        subprocess.run(
            [*run, "--user=1000:1000", mount, DEFAULT_IMAGE, "python", "-c", verify], capture_output=True, text=True, check=True
        )
    finally:
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=True)
