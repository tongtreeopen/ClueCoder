"""Persistent budget shared by workflow workers and format corrections."""

import time
from contextlib import nullcontext

from .eval.budget import EvalLimitExceeded


class WorkflowModel:
    def __init__(self, model, journal, limits, goal=None, on_usage=None):
        self._model, self.journal, self.limits = model, journal, limits
        self.goal, self.on_usage = goal, on_usage
        usages = [event["data"]["usage"] for event in journal.events if "usage" in event["data"]]
        self.previous = usages[-1] if usages else {"calls": 0, "tokens": 0, "seconds": 0, "unknown": False}
        self.calls, self.tokens, self.unknown = self.previous["calls"], self.previous["tokens"], self.previous["unknown"]
        self.started = time.monotonic()

    def __getattr__(self, name):
        return getattr(self._model, name)

    def child_scope(self, child_id):
        return self._model.child_scope(child_id) if hasattr(self._model, "child_scope") else nullcontext()

    def usage(self):
        return {"calls": self.calls, "tokens": self.tokens, "unknown": self.unknown,
                "seconds": self.previous["seconds"] + time.monotonic() - self.started}

    def chat(self, *args, **kwargs):
        goal = self.goal
        if goal is not None:
            goal.record_usage()
            if goal.status == "limit" or goal.tokens >= goal.MAX_TOKENS or goal.seconds >= goal.MAX_SECONDS:
                goal.stop_limit("Goal budget exhausted during workflow")
                raise EvalLimitExceeded("Outer Goal budget exhausted")
        usage = self.usage()
        if self.unknown or any(usage[key] >= self.limits[key] for key in ("calls", "tokens", "seconds")):
            raise EvalLimitExceeded("Workflow budget exhausted or usage unknown")
        if hasattr(self._model, "max_calls") and (self._model.calls >= self._model.max_calls
                                                  or self._model.tokens >= self._model.max_tokens):
            raise EvalLimitExceeded("Outer model budget exhausted")
        self.calls += 1
        self.unknown = True
        self.journal.append("model_call_started", {"usage": self.usage()})
        response = self._model.chat(*args, **kwargs)
        prompt, completion = response.prompt_tokens, response.completion_tokens
        known = type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0 and prompt + completion > 0
        self.tokens += sum(value for value in (prompt, completion) if type(value) is int and value > 0)
        self.unknown = not known
        self.journal.append("model_call_finished", {"usage": self.usage(), "prompt_tokens": prompt,
                                                    "completion_tokens": completion})
        if self.on_usage is not None:
            self.on_usage({"model": getattr(self._model, "model", "unknown"), "prompt_tokens": prompt,
                           "completion_tokens": completion, "workflow_run_id": self.journal.run_id})
        if goal is not None:
            goal.record_usage(sum(value for value in (prompt, completion) if type(value) is int and value > 0))
            if not known or goal.tokens >= goal.MAX_TOKENS or goal.seconds >= goal.MAX_SECONDS:
                goal.stop_limit("Goal budget exhausted or workflow usage unknown")
        return response
