"""Provider client construction must survive host no-proxy configuration.

Windows commonly lists both ``::1`` and ``[::1]``. httpx parses every no-proxy entry
as a URL, so a bracketed host makes it misread the port (``[::1]`` -> ``':1]'``,
``[::1]:8080`` -> ``':1]:8080'``) and raise InvalidURL from inside OpenAI(), before
any request is attempted.
"""

import os

import httpx
import pytest
from openai import APIConnectionError

from tracecoder.llm import EVAL_TRANSIENT_ATTEMPTS, LLM, normalize_no_proxy

BRACKETED = "169.254.127.60,localhost,127.0.0.1,::1,[::1]"
CLEAN = "169.254.127.60,localhost,127.0.0.1,::1"


def _set_no_proxy(monkeypatch, value):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, value)


def test_bare_bracketed_ipv6_entry_no_longer_breaks_httpx(monkeypatch):
    _set_no_proxy(monkeypatch, BRACKETED)

    assert normalize_no_proxy() == CLEAN
    # Construction is what reads NO_PROXY; before the fix this raised InvalidURL.
    assert httpx.Client() is not None
    assert os.environ["NO_PROXY"] == CLEAN


def test_bracketed_entry_with_a_port_is_dropped(monkeypatch):
    # httpx's documented port form still breaks parsing, so it must not survive.
    # The bare ::1 entry beside it remains as the host-only equivalent.
    _set_no_proxy(monkeypatch, "localhost,[::1]:8080,[::1],127.0.0.1")

    assert normalize_no_proxy() == "localhost,::1,127.0.0.1"
    assert httpx.Client() is not None


def test_unclosed_bracket_entry_is_dropped(monkeypatch):
    _set_no_proxy(monkeypatch, "localhost,[::1,127.0.0.1")

    assert normalize_no_proxy() == "localhost,127.0.0.1"


def test_entries_are_deduplicated_and_empties_dropped(monkeypatch):
    _set_no_proxy(monkeypatch, "localhost,,localhost,[::1],127.0.0.1")

    # The bracketed duplicate collapses onto the unbracketed loopback entry.
    assert normalize_no_proxy() == "localhost,::1,127.0.0.1"


def test_unset_no_proxy_stays_empty(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    assert normalize_no_proxy() == ""


class _Chunk:
    def __init__(self, content):
        self.choices = [type("choice", (), {"delta": type("delta", (), {"content": content})()})()]
        self.usage = None


class _FlakyStream:
    """Streams one chunk, then drops the connection, for the first N attempts."""

    def __init__(self, failures, seen):
        self.failures = failures
        self.seen = seen

    def __iter__(self):
        yield _Chunk("partial")
        self.seen["attempts"] += 1
        if self.seen["attempts"] <= self.failures:
            raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
        yield _Chunk("complete")


class _StreamingClient:
    """Returns a fresh stream per create() call, so each attempt is independent."""

    def __init__(self, failures):
        self.seen = {"attempts": 0, "creates": 0}
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.seen["creates"] += 1
                return _FlakyStream(failures, outer.seen)

        self.chat = type("chat", (), {"completions": Completions()})()

    def with_options(self, **kwargs):
        return self


def _eval_mode_llm(failures: int) -> LLM:
    llm = LLM.__new__(LLM)
    llm.model = "test-model"
    llm.extra = {}
    llm.client = _StreamingClient(failures)
    llm.total_prompt_tokens = 0
    llm.total_completion_tokens = 0
    llm.enable_eval_single_attempt()
    return llm


def test_eval_mode_retries_a_connection_dropped_mid_stream(monkeypatch):
    monkeypatch.setattr("tracecoder.llm.time.sleep", lambda _seconds: None)
    llm = _eval_mode_llm(failures=1)

    # The connection drops after the first chunk; the call must still succeed, and
    # the partly received attempt must not leak into the final response.
    response = llm.chat([{"role": "user", "content": "hi"}])

    assert response.content == "partialcomplete"
    assert llm.client.seen["creates"] == 2


def test_eval_mode_gives_up_after_bounded_attempts(monkeypatch):
    monkeypatch.setattr("tracecoder.llm.time.sleep", lambda _seconds: None)
    llm = _eval_mode_llm(failures=99)

    with pytest.raises(APIConnectionError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.client.seen["creates"] == EVAL_TRANSIENT_ATTEMPTS
