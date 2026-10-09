"""Tests for the offline scripted demo and ScriptedLLM."""

import pytest

from tracecoder.demo import ScriptedLLM, _script, run_demo
from tracecoder.llm import LLMResponse, ToolCall


def test_scripted_llm_plays_turns_in_order():
    llm = ScriptedLLM([
        LLMResponse(content="first"),
        LLMResponse(content="second", tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "true"})]),
    ])

    r1 = llm.chat(messages=[])
    r2 = llm.chat(messages=[])

    assert r1.content == "first"
    assert r2.content == "second"
    assert r2.tool_calls[0].name == "bash"

    with pytest.raises(RuntimeError):
        llm.chat(messages=[])


def test_demo_runs_the_full_loop(tmp_path, monkeypatch):
    monkeypatch.setattr("tracecoder.demo.tempfile.mkdtemp", lambda prefix: str(tmp_path))

    assert run_demo() == 0
    fib = (tmp_path / "fib.py").read_text()
    assert "def fib(n):" in fib
    assert (tmp_path / "test_fib.py").exists()


def test_script_points_at_the_demo_workdir(tmp_path):
    turns = _script(tmp_path)
    paths = [tc.arguments.get("file_path", "") for turn in turns for tc in turn.tool_calls]
    assert all(path.startswith(str(tmp_path)) for path in paths if path)


def test_plan_hooks_example_guards_then_lets_through(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "plan_hooks_demo", Path(__file__).parent.parent / "examples" / "plan_hooks_demo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    workdir = tmp_path / "project with spaces"
    workdir.mkdir()
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda prefix: str(workdir))

    assert mod.run() == 0
    fib = (workdir / "fib.py").read_text()
    assert "ValueError" in fib
    assert "test_negative_raises" in (workdir / "test_fib.py").read_text()


def test_plan_hooks_example_reports_shell_failure(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "plan_hooks_demo", Path(__file__).parent.parent / "examples" / "plan_hooks_demo.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(mod.BashTool, "execute", lambda self, **kwargs: "Error: test command failed")
    assert mod.run() == 1
