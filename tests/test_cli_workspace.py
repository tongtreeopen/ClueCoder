"""Strict CLI tool assembly, independent of LLM API calls."""

import sys

import pytest

from tracecoder import Agent
from tracecoder import cli as cli_module
from tracecoder import session as session_module
from tracecoder.checkpoints import clear, pending, record, undo
from tracecoder.cli import _cli_context
from tracecoder.demo import ScriptedLLM
from tracecoder.llm import LLMResponse, ToolCall
from tracecoder.permissions import Permission


class RecordingLLM(ScriptedLLM):
    def __init__(self, script):
        super().__init__(script)
        self.seen = []

    def chat(self, messages, tools=None, on_token=None):
        self.seen.append(messages)
        return super().chat(messages, tools, on_token)


def test_cli_context_is_strict_and_has_no_hook_or_mcp_tools(tmp_path):
    tools, _, policy = _cli_context(tmp_path)
    by_name = {tool.name: tool for tool in tools}
    assert policy.root == tmp_path.resolve()
    assert all(by_name[name].policy is policy for name in ("read_file", "write_file", "edit_file", "glob", "grep"))
    assert not any(name.startswith("mcp__") for name in by_name)
    assert "isolation backend" in by_name["bash"].execute("echo hello")
    assert "outside workspace" in by_name["write_file"].execute(str(tmp_path.parent / "outside.txt"), "x")
    assert not (tmp_path.parent / "outside.txt").exists()


def test_yes_and_subagent_cannot_bypass_cli_policy(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    tools, catalog, _ = _cli_context(tmp_path)
    llm = RecordingLLM([
        LLMResponse(tool_calls=[ToolCall(id="a", name="agent", arguments={"task": "write outside"})]),
        LLMResponse(tool_calls=[ToolCall(id="b", name="write_file", arguments={"file_path": str(outside), "content": "x"})]),
        LLMResponse(content="blocked"),
        LLMResponse(content="done"),
    ])
    agent = Agent(
        llm=llm,
        tools=tools,
        permission=Permission(allow_all=True),
        context_catalog=catalog,
    )
    assert agent.chat("go") == "done"
    assert not outside.exists()
    assert any("outside workspace" in msg.get("content", "") for msg in llm.seen[2] if msg["role"] == "tool")


def test_cli_bash_rejects_commands_even_with_yes(tmp_path):
    tools, _, _ = _cli_context(tmp_path)
    agent = Agent(
        llm=ScriptedLLM([
            LLMResponse(tool_calls=[ToolCall(id="b", name="bash", arguments={"command": "echo hello"})]),
            LLMResponse(content="done"),
        ]),
        tools=tools,
        permission=Permission(allow_all=True),
    )
    assert agent.chat("go") == "done"
    assert "isolation backend" in agent.messages[2]["content"]


@pytest.mark.parametrize("args", [["-p", "hello"], ["-p", "hello", "--yes"], ["--resume", "saved", "-p", "hello"], []])
def test_main_uses_strict_tools_in_every_mode(tmp_path, monkeypatch, args):
    seen = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(sys, "argv", ["tracecoder", *args])
    monkeypatch.setattr(cli_module, "_run_once", lambda agent, prompt, memory, journal: seen.append((agent, memory)))
    monkeypatch.setattr(cli_module, "_repl", lambda agent, config, policy, memory, journal: seen.append((agent, memory)))
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    if "--resume" in args:
        session_module.save_session([], "test-model", "saved")
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    cli_module.main()
    assert len(seen) == 1
    agent, memory = seen[0]
    assert memory.auto is True
    assert "outside workspace" in next(t for t in agent.tools if t.name == "read_file").execute(
        str(tmp_path.parent / "outside.txt")
    )
    assert "isolation backend" in next(t for t in agent.tools if t.name == "bash").execute("echo hello")
    assert not agent.hooks
    assert not any(t.name.startswith("mcp__") for t in agent.tools)


def test_cli_demo_is_offline_and_does_not_create_sessions(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sys, "argv", ["tracecoder", "--demo"])
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    with pytest.raises(SystemExit) as result:
        cli_module.main()
    assert result.value.code == 0
    assert "tracecoder demo (offline)" in capsys.readouterr().out
    assert not (tmp_path / "sessions").exists()


def test_undo_cannot_restore_outside_checkpoint(tmp_path):
    outside = tmp_path.parent / "undo-outside.txt"
    outside.write_text("original", encoding="utf-8")
    clear()
    try:
        record(outside)
        outside.write_text("changed", encoding="utf-8")
        assert "outside workspace" in undo(_cli_context(tmp_path)[2])
        assert pending() == 1
        assert outside.read_text(encoding="utf-8") == "changed"
    finally:
        clear()
        outside.unlink(missing_ok=True)
