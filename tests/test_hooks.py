"""Pre/PostToolUse shell hooks: loading, matching, blocking, failing open."""

import logging
import os
import shlex
import subprocess
import sys

import pytest

from tracecoder import Agent
from tracecoder.demo import ScriptedLLM
from tracecoder.hooks import Hooks, load_hooks
from tracecoder.llm import LLMResponse, ToolCall
from tracecoder.permissions import Permission
from tracecoder.tools.agent import AgentTool
from tracecoder.tools.write import WriteFileTool


def _python_script(tmp_path, name, body):
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    args = [sys.executable, str(script)]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def _write_call(call_id, path):
    return ToolCall(id=call_id, name="write_file", arguments={"file_path": str(path), "content": "x\n"})


def _agent(tmp_path, hooks, permission=None):
    return Agent(
        llm=ScriptedLLM(
            [
                LLMResponse(tool_calls=[_write_call("c1", tmp_path / "a.txt")]),
                LLMResponse(content="done"),
            ]
        ),
        tools=[WriteFileTool()],
        permission=permission,
        hooks=hooks,
    )


def test_pre_hook_blocks_with_reason_and_the_tool_never_runs(tmp_path):
    blocker = _python_script(tmp_path, "blocker.py", "import sys\nsys.stderr.write('writes are frozen today')\nsys.exit(2)\n")
    asked = []
    agent = _agent(
        tmp_path,
        Hooks(pre=[{"matcher": "*", "command": blocker}], post=[]),
        permission=Permission(ask=lambda n, a: asked.append(n) or "once"),
    )

    assert agent.chat("go") == "done"  # the loop survived the veto
    assert not (tmp_path / "a.txt").exists()  # the tool never executed
    result = agent.messages[2]
    assert result["role"] == "tool" and result["tool_call_id"] == "c1"
    assert "writes are frozen today" in result["content"]  # the model gets the reason
    assert asked == []  # hooks gate before consent is asked


def test_pre_hook_passing_lets_the_call_through(tmp_path):
    agent = _agent(tmp_path, Hooks(pre=[{"matcher": "", "command": _python_script(tmp_path, "pass.py", "")}], post=[]))

    assert agent.chat("go") == "done"
    assert (tmp_path / "a.txt").exists()


def test_post_hook_observes_the_finished_call(tmp_path):
    marker = tmp_path / "seen.jsonl"
    writer = _python_script(
        tmp_path,
        "writer.py",
        "import pathlib, sys\npathlib.Path(sys.argv[1]).open('a', encoding='utf-8').write(sys.stdin.read())\n",
    )
    path_arg = subprocess.list2cmdline([str(marker)]) if os.name == "nt" else shlex.quote(str(marker))
    agent = _agent(tmp_path, Hooks(pre=[], post=[{"matcher": "*", "command": f"{writer} {path_arg}"}]))

    assert agent.chat("go") == "done"
    seen = marker.read_text()
    assert '"tool_name": "write_file"' in seen  # the call JSON arrived on stdin
    assert '"tool_response"' in seen  # post hooks see the result too


def test_matcher_scopes_a_hook_to_one_tool(tmp_path):
    # a bash-only veto must not touch a write_file call
    cmd = _python_script(tmp_path, "unused_block.py", "raise SystemExit(2)\n")
    agent = _agent(tmp_path, Hooks(pre=[{"matcher": "bash", "command": cmd}], post=[]))

    assert agent.chat("go") == "done"
    assert (tmp_path / "a.txt").exists()


def test_missing_hooks_file_means_no_hooks(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        hooks = load_hooks(tmp_path / "nope.json")
    assert not hooks
    assert caplog.records == []


def test_broken_hooks_file_is_ignored_with_one_warning(tmp_path, caplog):
    bad = tmp_path / "hooks.json"
    bad.write_text("{not json")
    with caplog.at_level(logging.WARNING):
        hooks = load_hooks(bad)
    assert not hooks
    assert len(caplog.records) == 1
    assert "ignoring" in caplog.records[0].getMessage()


def test_failing_hook_is_skipped_with_a_warning(tmp_path, caplog):
    agent = _agent(
        tmp_path, Hooks(pre=[{"matcher": "*", "command": _python_script(tmp_path, "fail.py", "raise SystemExit(1)\n")}], post=[])
    )
    with caplog.at_level(logging.WARNING):
        assert agent.chat("go") == "done"
    assert (tmp_path / "a.txt").exists()  # fail open
    assert any("exited 1" in r.getMessage() for r in caplog.records)


def test_slow_hook_times_out_and_is_skipped(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("tracecoder.hooks.TIMEOUT", 0.3)
    agent = _agent(
        tmp_path,
        Hooks(pre=[{"matcher": "*", "command": _python_script(tmp_path, "slow.py", "import time\ntime.sleep(5)\n")}], post=[]),
    )
    with caplog.at_level(logging.WARNING):
        assert agent.chat("go") == "done"
    assert (tmp_path / "a.txt").exists()
    assert any("TimeoutExpired" in r.getMessage() for r in caplog.records)


def test_hooks_gate_each_call_of_a_parallel_batch(tmp_path):
    calls = [
        _write_call("c1", tmp_path / "a.txt"),
        _write_call("c2", tmp_path / "b.txt"),
    ]
    pre_log = tmp_path / "pre.jsonl"
    post_log = tmp_path / "post.jsonl"
    writer = _python_script(
        tmp_path,
        "writer.py",
        "import pathlib, sys\npathlib.Path(sys.argv[1]).open('a', encoding='utf-8').write(sys.stdin.read() + '\\n')\n",
    )
    pre_arg = subprocess.list2cmdline([str(pre_log)]) if os.name == "nt" else shlex.quote(str(pre_log))
    post_arg = subprocess.list2cmdline([str(post_log)]) if os.name == "nt" else shlex.quote(str(post_log))
    agent = Agent(
        llm=ScriptedLLM([LLMResponse(tool_calls=calls), LLMResponse(content="done")]),
        tools=[WriteFileTool()],
        hooks=Hooks(
            pre=[{"matcher": "*", "command": f"{writer} {pre_arg}"}],
            post=[{"matcher": "*", "command": f"{writer} {post_arg}"}],
        ),
    )

    assert agent.chat("go") == "done"
    assert (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").exists()
    assert pre_log.read_text().count('"tool_name"') == 2
    assert post_log.read_text().count('"tool_response"') == 2


def test_sub_agent_inherits_the_hooks(tmp_path):
    target = tmp_path / "sub.txt"
    agent = Agent(
        llm=ScriptedLLM(
            [
                LLMResponse(tool_calls=[ToolCall(id="c1", name="agent", arguments={"task": "write the file"})]),
                LLMResponse(tool_calls=[_write_call("c2", target)]),  # the sub-agent's move
                LLMResponse(content="could not write"),  # the sub-agent's reply
                LLMResponse(content="parent done"),
            ]
        ),
        tools=[AgentTool(), WriteFileTool()],
        hooks=Hooks(
            pre=[
                {
                    "matcher": "write_file",
                    "command": _python_script(tmp_path, "block.py", "import sys\nsys.stderr.write('no')\nsys.exit(2)\n"),
                }
            ],
            post=[],
        ),
    )

    assert agent.chat("go") == "parent done"
    assert not target.exists()  # the veto followed the work into the sub-agent


pytestmark = pytest.mark.usefixtures("tool_workspace")
