"""A long first message must not abort the run before the model is called.

Long problem statements can exceed the 1,000-character work-card goal limit. The work card is seeded
from the first user message and WorkStateStore caps goal text at 1,000, so the seed
must be truncated rather than raising ValueError at the append_message callback.
"""

import sys

import pytest

from tracecoder import cli as cli_module
from tracecoder import session as session_module


class _OfflineLLM:
    """Stands in for LLM so no real HTTP client is constructed."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _drive_cli(tmp_path, monkeypatch, prompt):
    """Run main() far enough to create the journal, work card and agent wiring."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(cli_module.DockerExecutor, "try_create", lambda workspace: None)
    monkeypatch.setattr(cli_module, "LLM", _OfflineLLM)
    monkeypatch.setattr(cli_module, "LiteLLM", _OfflineLLM)
    monkeypatch.setattr(sys, "argv", ["tracecoder", "--yes", "-p", prompt])

    captured = {}

    def fake_run_once(agent, prompt_text, memory=None, journal=None):
        # Drives the same callback the Agent uses for every message.
        agent.on_message({"role": "user", "content": prompt_text})
        captured["agent"] = agent

    monkeypatch.setattr(cli_module, "_run_once", fake_run_once)
    cli_module.main()
    return captured["agent"]


@pytest.mark.parametrize("length", [999, 1000, 1001, 5362])
def test_long_first_message_seeds_a_bounded_work_card(tmp_path, monkeypatch, length):
    agent = _drive_cli(tmp_path, monkeypatch, "x" * length)

    card = agent.work_state.card
    assert card is not None, "work card was never created"
    assert card["goal"] == "x" * min(length, 1000)
    assert len(card["goal"]) <= 1000


def test_short_first_message_is_unchanged(tmp_path, monkeypatch):
    agent = _drive_cli(tmp_path, monkeypatch, "Fix the failing tests")

    assert agent.work_state.card["goal"] == "Fix the failing tests"
