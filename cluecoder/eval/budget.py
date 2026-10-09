"""Durable model-call budget for an isolated evaluation worker."""

import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock

from ..llm import LLM, LLMResponse


class EvalLimitExceeded(RuntimeError):
    """The evaluation worker exhausted its model budget."""


class BudgetedModel:
    def __init__(self, model: LLM, usage_path: Path, max_calls: int = 50, max_tokens: int = 100000):
        if max_calls < 1 or max_tokens < 1:
            raise ValueError("Evaluation budgets must be positive")
        self._model = model
        if isinstance(model, LLM):
            model.enable_eval_single_attempt()
        self._usage_path = usage_path
        self._calls_path = usage_path.with_name("model_calls.json")
        self._actor: ContextVar[str] = ContextVar("eval_model_actor", default="parent")
        self._calls: list[dict] = []
        self._rejected_attempts = 0
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_unknown = False
        self.limit: str | None = None
        self._lock = Lock()
        self._write_usage()
        self._write_calls()

    @contextmanager
    def child_scope(self, child_id: str):
        token = self._actor.set(f"child:{child_id}")
        try:
            yield
        finally:
            self._actor.reset(token)

    @property
    def model(self) -> str:
        return self._model.model

    @model.setter
    def model(self, value: str) -> None:
        self._model.model = value

    def __getattr__(self, name: str):
        return getattr(self._model, name)

    def _write_usage(self) -> None:
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._usage_path.with_name(f".{self._usage_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps({
            "calls": self.calls,
            "tokens": self.tokens,
            "usage_unknown": self.usage_unknown,
            "limit": self.limit,
        }), encoding="utf-8")
        os.replace(temporary, self._usage_path)

    def _write_calls(self) -> None:
        temporary = self._calls_path.with_name(f".{self._calls_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps({"version": 1, "calls": self._calls,
                                         "limit": self.limit, "rejected_attempts": self._rejected_attempts}),
                             encoding="utf-8")
        os.replace(temporary, self._calls_path)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        max_output_tokens: int | None = None,
        extra_body: dict | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        # A shared lock also serializes sibling sub-agents: each next call sees
        # the preceding provider-reported usage before entering the model.
        with self._lock:
            if self.calls >= self.max_calls or self.tokens >= self.max_tokens:
                self.limit = "calls" if self.calls >= self.max_calls else "tokens"
                self._rejected_attempts += 1
                self._write_usage()
                self._write_calls()
                raise EvalLimitExceeded(f"Evaluation {self.limit} budget exhausted")
            prior_unknown = self.usage_unknown
            self.calls += 1
            self.usage_unknown = True
            record = {"call_index": self.calls, "actor": self._actor.get(),
                      "outcome": "in_flight", "prompt_tokens": None, "completion_tokens": None,
                      "usage_status": "missing", "cumulative_tokens": self.tokens,
                      "cumulative_prompt_tokens": self.prompt_tokens,
                      "cumulative_completion_tokens": self.completion_tokens,
                      "cumulative_usage_unknown": True}
            self._calls.append(record)
            self._write_usage()
            self._write_calls()
            try:
                response = self._model.chat(
                    messages=messages, tools=tools, on_token=on_token,
                    max_output_tokens=max_output_tokens,
                    **({"extra_body": extra_body} if extra_body is not None else {}),
                    **({"response_format": response_format} if response_format is not None else {}),
                )
            except Exception as error:
                self.usage_unknown = True
                record["outcome"] = "provider_error"
                record["exception_type"] = type(error).__name__
                self._write_usage()
                self._write_calls()
                raise
            prompt = response.prompt_tokens
            completion = response.completion_tokens
            valid_prompt = type(prompt) is int and prompt >= 0
            valid_completion = type(completion) is int and completion >= 0
            self.prompt_tokens += prompt if type(prompt) is int and prompt > 0 else 0
            self.completion_tokens += completion if type(completion) is int and completion > 0 else 0
            self.tokens += sum(value for value in (prompt, completion)
                               if type(value) is int and value > 0)
            if (type(prompt) is not int or type(completion) is not int
                    or prompt < 0 or completion < 0 or prompt + completion == 0):
                self.usage_unknown = True
            else:
                self.usage_unknown = prior_unknown
            record.update(outcome="completed", prompt_tokens=prompt if valid_prompt else None,
                          completion_tokens=completion if valid_completion else None,
                          usage_status=("complete" if valid_prompt and valid_completion and prompt + completion > 0
                                        else "zero" if valid_prompt and valid_completion
                                        else "partial" if valid_prompt or valid_completion else "missing"),
                          cumulative_tokens=self.tokens, cumulative_prompt_tokens=self.prompt_tokens,
                          cumulative_completion_tokens=self.completion_tokens,
                          cumulative_usage_unknown=self.usage_unknown)
            self._write_usage()
            self._write_calls()
            return response
