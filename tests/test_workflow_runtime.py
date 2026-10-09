import json
import subprocess

import pytest

from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.workflow import ReviewPolicy, WorkflowRuntime, capture_source


def repository(path):
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    (path / "a.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["add", "a.py"], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def response():
    return LLMResponse(content=json.dumps({"status": "completed", "summary": "reviewed", "findings": [],
                                         "evidence_refs": [], "blockers": []}), prompt_tokens=10, completion_tokens=5)


def test_review_resume_reuses_completed_steps_and_rejects_source_drift(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    model = ScriptedLLM([response(), response(), response()])
    runtime = WorkflowRuntime(tmp_path, model)
    first = runtime.run("review-changes")
    assert first["status"] == "completed" and first["usage"]["calls"] == 3
    restored = WorkflowRuntime(tmp_path, ScriptedLLM([])).run("review-changes", resume_from_run_id=first["run_id"])
    assert restored["status"] == "completed" and restored["cached_steps"] == 3
    assert restored["usage"]["calls"] == 3 and restored["result"]["findings"] == []
    (tmp_path / "a.py").write_text("x = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        runtime.run("review-changes", resume_from_run_id=first["run_id"])


def test_interruption_only_replays_unfinished_step_and_preserves_budget(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

    class Interrupted(ScriptedLLM):
        def chat(self, *args, **kwargs):
            if not self._turns:
                raise KeyboardInterrupt
            return super().chat(*args, **kwargs)

    runtime = WorkflowRuntime(tmp_path, Interrupted([response()]))
    with pytest.raises(KeyboardInterrupt):
        runtime.run("review-changes")
    run_id = next(runtime.root.iterdir()).name
    # An in-flight call has unknown usage. Cached work survives; new calls fail closed.
    restored = WorkflowRuntime(tmp_path, ScriptedLLM([response(), response()])).run("review-changes", resume_from_run_id=run_id)
    assert restored["status"] == "limit" and restored["cached_steps"] == 1 and restored["usage"]["unknown"]
    assert restored["usage"]["calls"] == 2


def test_between_step_interruption_can_resume_remaining_work(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

    def script(context, args):
        context.step("one", "read")
        if context.model.calls == 1 and context.cached_steps == 0:
            raise KeyboardInterrupt
        return context.step("two", "read again", ["one"])

    registry = {"probe": ("1", script)}
    runtime = WorkflowRuntime(tmp_path, ScriptedLLM([response()]), registry)
    with pytest.raises(KeyboardInterrupt):
        runtime.run("probe")
    run_id = next(runtime.root.iterdir()).name
    result = WorkflowRuntime(tmp_path, ScriptedLLM([response()]), registry).run("probe", resume_from_run_id=run_id)
    assert result["status"] == "completed" and result["cached_steps"] == 1
    assert result["completed_steps"] == 2 and result["usage"]["calls"] == 2


def test_staging_partition_is_part_of_identity(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    before = capture_source(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py"], check=True)
    assert capture_source(tmp_path) != before


def test_worker_cannot_write_or_read_runtime_and_unknown_usage_stops_new_calls(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    model = ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("write", "write_file", {"file_path": str(tmp_path / "a.py"), "content": "bad"})],
                    prompt_tokens=5, completion_tokens=2),
        response(), response(), LLMResponse(content=response().content, prompt_tokens=None, completion_tokens=None),
    ])
    result = WorkflowRuntime(tmp_path, model).run("review-changes")
    assert (tmp_path / "a.py").read_text() == "x = 2\n" and result["usage"]["unknown"]
    journal = (tmp_path / ".cluecoder" / "workflows" / result["run_id"] / "events.jsonl").read_text(encoding="utf-8")
    assert "unknown tool" in journal.lower()


def test_model_version_and_untracked_changes_invalidate_resume(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")

    def script(context, args):
        return context.step("one", "read")

    registry = {"probe": ("1", script)}
    result = WorkflowRuntime(tmp_path, ScriptedLLM([response()]), registry).run("probe")
    for model, changed_registry in [(ScriptedLLM([], model="different"), registry),
                                    (ScriptedLLM([]), {"probe": ("2", script)})]:
        with pytest.raises(ValueError, match="identity"):
            WorkflowRuntime(tmp_path, model, changed_registry).run("probe", resume_from_run_id=result["run_id"])
    (tmp_path / "untracked.py").write_text("new", encoding="utf-8")
    with pytest.raises(ValueError, match="untracked"):
        WorkflowRuntime(tmp_path, ScriptedLLM([]), registry).run("probe", resume_from_run_id=result["run_id"])
    with pytest.raises(ValueError, match="internal"):
        ReviewPolicy(tmp_path).resolve(".cluecoder/workflows")


def test_budget_is_cumulative_across_resume_and_completed_cache_works_at_limit(tmp_path):
    repository(tmp_path)

    def script(context, args):
        context.step("one", "read")
        return context.step("two", "read again", ["one"])

    runtime = WorkflowRuntime(tmp_path, ScriptedLLM([response()]), {"probe": ("1", script)}, {"calls": 1})
    first = runtime.run("probe")
    restored = runtime.run("probe", resume_from_run_id=first["run_id"])
    assert first["status"] == restored["status"] == "limit" and restored["cached_steps"] == 1
    assert restored["usage"]["calls"] == 1

    def single(context, args):
        return context.step("one", "read")

    runtime = WorkflowRuntime(tmp_path, ScriptedLLM([response()]), {"single": ("1", single)}, {"calls": 1})
    first = runtime.run("single")
    restored = runtime.run("single", resume_from_run_id=first["run_id"])
    assert restored["status"] == "completed" and restored["cached_steps"] == 1 and restored["usage"]["calls"] == 1


def test_source_drift_during_step_never_enters_completed_cache(tmp_path):
    repository(tmp_path)

    class Mutating(ScriptedLLM):
        def chat(self, *args, **kwargs):
            (tmp_path / "a.py").write_text("outside edit", encoding="utf-8")
            return super().chat(*args, **kwargs)

    def single(context, args):
        return context.step("one", "read")

    result = WorkflowRuntime(tmp_path, Mutating([response()]), {"single": ("1", single)}).run("single")
    assert result["status"] == "error" and result["completed_steps"] == 0


def test_ignored_source_is_not_readable_by_workflow(tmp_path):
    repository(tmp_path)
    (tmp_path / "ignored.txt").write_text("untracked state", encoding="utf-8")
    # The policy must reject any file outside the captured tracked set.
    policy = ReviewPolicy(tmp_path, frozenset({"a.py"}))
    with pytest.raises(ValueError, match="tracked"):
        policy.resolve("ignored.txt")
