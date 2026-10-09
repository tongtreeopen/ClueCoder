"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Since most providers (DeepSeek, Qwen, Kimi, GLM, Ollama, etc.) expose an
OpenAI-compatible endpoint, we just use the openai SDK directly.  Switch
provider by changing OPENAI_BASE_URL + OPENAI_API_KEY. That's it.

For providers that are NOT OpenAI-compatible (AWS Bedrock, Google Vertex,
etc.), use the LiteLLM backend which routes to 100+ providers through a
single unified interface. Set TRACECODER_PROVIDER=litellm.
"""

import json
import os
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0
    reasoning_content: str | None = None
    finish_reason: str | None = None

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.reasoning_content is not None:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


# Approximate USD list price per million tokens: (input, output).
# This deliberately ignores cache discounts. For time-, region-, or context-
# tiered models it uses peak, international, and shortest-context list rates,
# respectively, so the CLI estimate stays simple and avoids promotional rates.
# Added/updated rate sources (checked 2026-09-26):
# api-docs.deepseek.com/quick_start/pricing, platform.minimax.io/subscribe/token-plan,
# docs.z.ai/guides/overview/pricing, platform.kimi.ai/docs/pricing/chat,
# www.alibabacloud.com/help/en/model-studio/model-pricing.
_PRICING = {
    # OpenAI - current flagships
    "gpt-5.5": (5, 30),
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek - peak, cache-miss rates
    "deepseek-flash": (0.3, 1.2),
    "deepseek-v4-flash": (0.3, 1.2),  # legacy alias served by Flash
    "deepseek-v4-pro": (1.32, 3.96),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # MiniMax - standard/list rate; M3 uses its <=512K tier
    "MiniMax-M3": (0.6, 2.4),
    "MiniMax-M2.7": (0.3, 1.2),
    "MiniMax-M2.7-highspeed": (0.6, 2.4),
    # Z.AI / Zhipu GLM
    "glm-5.2": (1.4, 4.4),
    # Alibaba Qwen - international, shortest-context list tier
    "qwen3.8-max": (2, 6),
    "qwen3.7-plus": (0.4, 1.6),
    "qwen3.8-flash": (0.15, 0.47),
    "qwen3-coder-plus": (1, 5),
    "qwen3-coder-flash": (0.3, 1.5),
    "qwen3-max": (1.2, 6),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (1.6, 6.4),
    # Moonshot Kimi
    "kimi-k2.6": (0.95, 4),
    "kimi-k2.7-code": (0.95, 4),
    "kimi-k2.7-code-highspeed": (1.9, 8),
}


def _adapt_rejected_param(params: dict, exc: BadRequestError) -> bool:
    """Translate or drop one parameter a provider rejected with a 400.

    Newer OpenAI models (gpt-5, o-series) accept only ``max_completion_tokens``
    and the default temperature; the 400 message quotes the offender
    (``'max_tokens'``). Quoted matching is load-bearing: the rejection text
    for ``max_completion_tokens`` must not re-trigger the translation.
    Returns False when nothing recognizable was rejected.
    """
    message = str(exc).lower()
    if "'max_tokens'" in message and "max_tokens" in params:
        params["max_completion_tokens"] = params.pop("max_tokens")
        return True
    if "'temperature'" in message and "temperature" in params:
        params.pop("temperature")
        return True
    return False


# Total attempts for a model call in evaluation before it is reported as failed.
# Covers connection resets, timeouts, 429 and 5xx, including a connection dropped
# while the response body was arriving. Two seconds and upward is deliberate: a
# host or proxy outage that resets every stream lasts longer than a single retry.
EVAL_TRANSIENT_ATTEMPTS = 5


def _backoff(attempt: int) -> None:
    """Sleep before the next attempt: 1s, 2s, 4s, ... capped at 8s."""
    time.sleep(min(2 ** attempt, 8))


def normalize_no_proxy() -> str:
    """Rewrite no-proxy entries httpx cannot parse, in os.environ.

    httpx reads NO_PROXY/no_proxy and parses every entry as a URL. A bracketed
    IPv6 host makes it misread the port -- ``[::1]`` gives ``':1]'`` and
    ``[::1]:8080`` gives ``':1]:8080'`` -- raising InvalidURL while the provider
    client is constructed, so no request is ever attempted. A bracketed entry with
    no port loses its brackets; one carrying a port is dropped, since it only
    restates the host entry beside it.
    """
    entries, seen = [], set()
    for name in ("NO_PROXY", "no_proxy"):
        for entry in os.environ.get(name, "").split(","):
            entry = entry.strip()
            if entry.startswith("["):
                if "]" not in entry:
                    continue
                host, _, port = entry[1:].partition("]")
                if port:
                    continue
                entry = host
            if not entry or entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)
    value = ",".join(entries)
    for name in ("NO_PROXY", "no_proxy"):
        os.environ[name] = value
    return value


class LLM:
    is_deepseek = False

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        normalize_no_proxy()
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.is_deepseek = self.client.base_url.host == "api.deepseek.com"
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def enable_eval_single_attempt(self) -> None:
        """Keep one chat invocation to one *logical* provider request.

        Transient failures (connection resets, timeouts, 429, 5xx) are still retried
        a bounded number of times: they are host and proxy artifacts, and scoring
        them as agent failures measures the network rather than the agent.
        """
        self._eval_single_attempt = True
        self.client = self.client.with_options(max_retries=0)

    @property
    def estimated_cost(self) -> float | None:
        """Rough cost estimate in USD. Returns None if model not in pricing table."""
        pricing = _PRICING.get(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1_000_000
            + self.total_completion_tokens * output_rate / 1_000_000
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        max_output_tokens: int | None = None,
        extra_body: dict | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if max_output_tokens is not None:
            params.pop("max_completion_tokens", None)
            params["max_tokens"] = max_output_tokens
        if tools:
            params["tools"] = tools
            if self.is_deepseek:
                params["messages"] = [
                    {**message, "reasoning_content": message.get("reasoning_content") or ""}
                    if message.get("role") == "assistant" else message for message in messages
                ]
        if extra_body is not None:
            params["extra_body"] = {**(params.get("extra_body") or {}), **extra_body}
        if response_format is not None:
            params["response_format"] = response_format

        # Provider-dialect fallbacks. A 400 that quotes a parameter is adapted
        # (newer OpenAI models take max_completion_tokens, default temperature
        # only); a 400 that names nothing drops stream_options once, matching
        # the previous single-fallback behavior for servers that reject the
        # OpenAI extension silently. The loop is bounded by the param set, and
        # an unrecognized 400 re-raises instead of doubling the retries below.
        # LiteLLM never lands here: drop_params strips unsupported keys on that
        # path.
        params["stream_options"] = {"include_usage": True}
        attempts = EVAL_TRANSIENT_ATTEMPTS if getattr(self, "_eval_single_attempt", False) else 3
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                stream = self._call_with_retry(params, max_retries=1)
            except BadRequestError as e:
                # A rejected parameter is a dialect problem, not a transient one.
                if last or getattr(self, "_eval_single_attempt", False):
                    raise
                if _adapt_rejected_param(params, e):
                    continue
                if "stream_options" not in params:
                    raise
                params.pop("stream_options")
                continue
            except (RateLimitError, APITimeoutError, APIConnectionError):
                # Includes a connection lost while the body was being consumed:
                # retrying the whole request is the only way to recover it, and
                # a partly received response is discarded rather than merged.
                if last:
                    raise
                _backoff(attempt)
                continue

            except APIError as error:
                if last or (getattr(error, "status_code", 0) or 0) < 500:
                    raise
                _backoff(attempt)
                continue

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            finish_reason = None
            tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
            prompt_tok = None
            completion_tok = None
            try:
                for chunk in stream:
                    # usage info comes in the final chunk; getattr-style reads stay
                    # safe across OpenAI SDK objects and litellm's provider shapes
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        prompt_tok = getattr(usage, "prompt_tokens", None)
                        completion_tok = getattr(usage, "completion_tokens", None)

                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    finish_reason = getattr(chunk.choices[0], "finish_reason", None) or finish_reason
                    if getattr(delta, "reasoning_content", None):
                        reasoning_parts.append(delta.reasoning_content)

                    # accumulate text
                    if getattr(delta, "content", None):
                        content_parts.append(delta.content)
                        if on_token:
                            on_token(delta.content)

                    # accumulate tool calls across chunks
                    if getattr(delta, "tool_calls", None):
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tc_map:
                                tc_map[idx] = {"id": "", "name": "", "args": ""}
                            if tc_delta.id:
                                tc_map[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tc_map[idx]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tc_map[idx]["args"] += tc_delta.function.arguments
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if last:
                    raise
                _backoff(attempt)
                continue
            break

        # parse accumulated tool calls
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok or 0
        self.total_completion_tokens += completion_tok or 0

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
            finish_reason=finish_reason,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                time.sleep(wait)
            except APIError as e:
                # retry 5xx server errors but not 4xx; base APIError has no status_code so read it defensively
                status_code = getattr(e, "status_code", None)
                if status_code and status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise


class LiteLLM(LLM):
    """LLM backend via LiteLLM, supporting 100+ providers.

    Use this when your target provider is NOT OpenAI-compatible
    (AWS Bedrock, Google Vertex, Cohere, etc.) or when you want
    a single interface to switch between any provider by changing
    the model string.

    Set TRACECODER_PROVIDER=litellm and use LiteLLM model strings
    like ``anthropic/claude-3-haiku``, ``bedrock/anthropic.claude-v2``,
    ``vertex_ai/gemini-pro``, etc.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        # skip LLM.__init__ which creates an OpenAI client
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def enable_eval_single_attempt(self) -> None:
        self._eval_single_attempt = True

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff via litellm."""
        import litellm

        params["drop_params"] = True
        if getattr(self, "_eval_single_attempt", False):
            params["num_retries"] = 0
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url

        for attempt in range(max_retries):
            try:
                return litellm.completion(**params)
            except Exception as e:
                err = str(e).lower()
                is_transient = any(
                    kw in err
                    for kw in ["rate_limit", "timeout", "connection", "502", "503", "529"]
                )
                is_server = any(kw in err for kw in ["500", "502", "503", "504"])
                if (is_transient or is_server) and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
