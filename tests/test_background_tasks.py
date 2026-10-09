import json
import subprocess
import sys
import time

import pytest

from tracecoder.execution import MAX_OUTPUT_BYTES, DockerExecutor


def _executor(tmp_path):
    if not DockerExecutor.available():
        pytest.skip("Pinned Docker image unavailable")
    return DockerExecutor(tmp_path)


def test_background_returns_before_completion(tmp_path):
    executor = _executor(tmp_path)
    started = time.monotonic()
    run = executor.start('python -u -c \'import time; print("ready", end="", flush=True); time.sleep(4)\'', background=True)
    try:
        assert time.monotonic() - started < 2
        deadline = time.monotonic() + 3
        while "ready" not in run.snapshot()["output"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "ready" in run.snapshot()["output"]
        assert run.snapshot()["status"] == "running"
        assert executor.run("echo independent") == "independent"
        result = run.wait()
        assert result["status"] == "completed" and result["exit_code"] == 0 and result["cleanup_verified"]
    finally:
        run.cancel()


def test_cancel_terminates_container_and_children(tmp_path):
    run = _executor(tmp_path).start("sleep 30 & wait", background=True)
    assert run.cancel()["status"] == "cancelled"
    assert run.cancel()["cleanup_verified"]
    assert subprocess.run(["docker", "inspect", run.name], capture_output=True, check=False).returncode != 0


def test_bounded_wait_returns_running_then_completed(tmp_path):
    from tracecoder.background import BackgroundManager
    from tracecoder.tools.background import BackgroundStatusTool

    manager = BackgroundManager(_executor(tmp_path))
    task_id = manager.start("sleep 4; echo done")
    try:
        started = time.monotonic()
        assert manager.status(task_id, 1)["status"] == "running"
        assert 0.8 <= time.monotonic() - started < 3
        started = time.monotonic()
        result = json.loads(BackgroundStatusTool(manager).execute(task_id))
        assert result["status"] == "completed" and result["cleanup_verified"] and not result["interfered"]
        assert result["output"].strip() == "done" and time.monotonic() - started < 8
        started = time.monotonic()
        assert manager.status(task_id, 30) == result
        assert time.monotonic() - started < 1
        for invalid in (-1, 31, True, 0.5):
            with pytest.raises(ValueError, match="wait_seconds"):
                manager.status(task_id, invalid)
    finally:
        manager.close()


def test_wait_does_not_block_parallel_cancel(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from tracecoder.background import BackgroundManager

    manager = BackgroundManager(_executor(tmp_path))
    task_id = manager.start("sleep 30")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiter = pool.submit(manager.status, task_id, 30)
            time.sleep(0.2)
            started = time.monotonic()
            assert manager.cancel(task_id)["status"] == "cancelled"
            assert waiter.result(timeout=5)["cleanup_verified"]
            assert time.monotonic() - started < 5
    finally:
        manager.close()


@pytest.mark.parametrize(
    ("command", "timeout", "status"),
    [
        ("exit 7", 120, "failed"),
        ("sleep 10", 1, "timed_out"),
        (f"python -c 'print(\"x\" * {MAX_OUTPUT_BYTES + 1})'", 120, "output_limit"),
    ],
)
def test_background_failure_limits(tmp_path, command, timeout, status):
    result = _executor(tmp_path).start(command, timeout, background=True).wait()
    assert result["status"] == status and result["cleanup_verified"]


@pytest.mark.parametrize(("command", "timeout"), [("", 120), ("echo ok", True), ("echo ok", 301)])
def test_background_validation(tmp_path, command, timeout):
    with pytest.raises(ValueError):
        DockerExecutor(tmp_path).start(command, timeout, background=True)


def test_cleanup_failure_is_not_success(tmp_path):
    class UnverifiedExecutor(DockerExecutor):
        @staticmethod
        def _cleanup(name):
            DockerExecutor._cleanup(name)
            return False

    _executor(tmp_path)
    result = UnverifiedExecutor(tmp_path).start("echo ok", background=True).wait()
    assert result["status"] == "cleanup_failed" and not result["cleanup_verified"]


def test_manager_capacity_and_repeat_queries(tmp_path):
    from tracecoder.background import BackgroundManager

    manager = BackgroundManager(_executor(tmp_path))
    try:
        ids = [manager.start("sleep 20") for _ in range(4)]
        with pytest.raises(ValueError, match="4"):
            manager.start("echo fifth")
        assert manager.status(ids[0])["status"] == "running"
        assert manager.cancel(ids[0])["status"] == "cancelled"
        assert manager.status(ids[0]) == manager.status(ids[0])
        first = manager.drain()
        assert len(first) == 1 and first[0]["task_id"] == ids[0]
        assert manager.drain() == []
        manager.start("echo replacement")
        with pytest.raises(ValueError, match="Unknown"):
            manager.status("old-id")
    finally:
        assert all(item["cleanup_verified"] for item in manager.close())


def test_background_permissions_and_disabled_backend(tmp_path):
    from tracecoder.agent import Agent
    from tracecoder.demo import ScriptedLLM
    from tracecoder.llm import ToolCall
    from tracecoder.permissions import Permission
    from tracecoder.tools.bash import DisabledBashTool

    agent = Agent(ScriptedLLM([]), tools=[DisabledBashTool()], permission=Permission())
    call = ToolCall(id="start", name="bash", arguments={"command": "echo ok", "run_in_background": True})
    assert "denied" in agent._permit(call)
    agent.plan_mode = True
    assert "Plan mode" in agent._permit(call)
    assert "unavailable" in DisabledBashTool().execute("echo ok", run_in_background=True)
    assert Permission().check("background_status", {"task_id": "id"}) is None
    assert "denied" in Permission().check("background_cancel", {"task_id": "id"})


def _background_evidence(tmp_path, status="completed", interfered=False):
    from tracecoder.session_journal import SessionJournal
    from tracecoder.work_state import WorkStateStore

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "offline")
    work = WorkStateStore(journal)
    work.ensure("Check background execution")
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "bg-call",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "echo check", "run_in_background": True})},
                },
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "bg-call", "content": '{"task_id":"bg-id","status":"started"}'})
    journal.append_event(
        "note", {"type": "background_start", "task_id": "bg-id", "command": "echo check", "tool_call_id": "bg-call"}
    )
    event_id = journal.append_event(
        "note",
        {
            "type": "background_result",
            "task_id": "bg-id",
            "command": "echo check",
            "tool_call_id": "bg-call",
            "status": status,
            "exit_code": 0,
            "cleanup_verified": status != "cleanup_failed",
            "interfered": interfered,
            "output": "check",
        },
    )
    work.update(criteria=[{"kind": "check", "description": "check", "command": "echo check", "event_id": event_id}])
    return journal, work, event_id


def test_background_goal_evidence(tmp_path):
    journal, work, event_id = _background_evidence(tmp_path)
    assert work.goal_evidence(1)[0] == [event_id]
    assert work.complete()["status"] == "completed"
    work.reopen()
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "new-bg",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "sleep 30", "run_in_background": True})},
                },
            ],
        }
    )
    assert not work.goal_evidence(1)[0]


@pytest.mark.parametrize(
    ("status", "interfered"),
    [
        ("running", False),
        ("failed", False),
        ("cancelled", False),
        ("timed_out", False),
        ("output_limit", False),
        ("cleanup_failed", False),
        ("completed", True),
    ],
)
def test_background_bad_evidence_cannot_complete(tmp_path, status, interfered):
    journal, work, _ = _background_evidence(tmp_path, status, interfered)
    assert not work.goal_evidence(1)[0]
    assert work.complete()["status"] != "completed"
    if status in {"running", "cleanup_failed"}:
        journal.append_event("note", {"type": "user_work_confirmation", "revision": work.card["revision"]})
        with pytest.raises(ValueError, match="[Bb]ackground"):
            work.confirm()
        assert not work.goal_evidence(1)[0]


def test_background_recovery_and_message_pairing(tmp_path):
    from tracecoder.session_journal import SessionJournal
    from tracecoder.work_state import WorkStateStore

    journal, work, event_id = _background_evidence(tmp_path)
    work.update(criteria=[{"kind": "check", "description": "fake", "command": "other", "event_id": event_id}])
    assert not work.goal_evidence(1)[0]
    journal.append_event("note", {"type": "background_start", "task_id": "unfinished", "command": "sleep 10"})
    resumed = WorkStateStore(SessionJournal.open(journal.root, journal.id, tmp_path))
    assert not resumed.goal_evidence(1)[0]
    with pytest.raises(ValueError, match="[Bb]ackground"):
        resumed.confirm()


@pytest.mark.parametrize(("parallel",), [("single",), ("read",), ("bash",)])
def test_agent_persists_background_once_and_reset_cleans(tmp_path, parallel):
    import threading

    from tracecoder.agent import Agent
    from tracecoder.cli import _bind_work
    from tracecoder.cli_memory import MemoryCLI
    from tracecoder.demo import ScriptedLLM
    from tracecoder.llm import LLMResponse, ToolCall
    from tracecoder.memory import MemoryStore
    from tracecoder.memory_workflow import MemoryWorkflow
    from tracecoder.permissions import Permission
    from tracecoder.session_journal import SessionJournal
    from tracecoder.tools.background import BackgroundStatusTool
    from tracecoder.tools.bash import DockerBashTool
    from tracecoder.tools.work import ReadWorkStateTool
    from tracecoder.work_state import WorkStateStore

    bash = DockerBashTool(_executor(tmp_path))
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "offline")
    work = WorkStateStore(journal)
    llm = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("start", "bash", {"command": "sleep 2; echo evidence", "run_in_background": True})]),
            LLMResponse(content="Started"),
        ]
    )
    agent = Agent(
        llm, tools=[bash, BackgroundStatusTool(bash.manager), ReadWorkStateTool(work)], permission=Permission(allow_all=True)
    )
    _bind_work(agent, journal, MemoryCLI(MemoryWorkflow(MemoryStore(tmp_path), llm), lambda: "", lambda _: None), work)
    main_thread = threading.get_ident()
    record = agent.on_background_event

    def record_on_main(detail):
        assert threading.get_ident() == main_thread
        return record(detail)

    agent.on_background_event = record_on_main
    assert agent.chat("Start check") == "Started"
    task_id = next(iter(bash.manager.tasks))
    calls = [ToolCall("query", "background_status", {"task_id": task_id, "wait_seconds": 30})]
    if parallel == "read":
        calls.append(ToolCall("work", "read_work_state", {}))
    elif parallel == "bash":
        calls.append(ToolCall("other", "bash", {"command": "echo other"}))
    llm._turns.extend([LLMResponse(tool_calls=calls), LLMResponse(content="Checked")])
    assert agent.chat("Wait for the real result") == "Checked"
    agent.collect_background()
    agent.collect_background()
    results = [event for event in journal.events if event["data"].get("type") == "background_result"]
    assert len(results) == 1
    assert sum(message["role"] == "user" for message in agent.messages) == 2
    assert sum(message.get("tool_call_id") == "start" for message in agent.messages) == 1
    assert bash.manager.status(task_id)["result_event_id"] == results[0]["seq"]
    query = next(message for message in agent.messages if message.get("tool_call_id") == "query")
    assert json.loads(query["content"])["result_event_id"] == results[0]["seq"]
    assert json.loads(query["content"])["interfered"] is (parallel == "bash")
    work.update(
        criteria=[{"kind": "check", "description": "real", "command": "sleep 2; echo evidence", "event_id": results[0]["seq"]}]
    )
    assert work.goal_evidence(1)[0] == ([] if parallel == "bash" else [results[0]["seq"]])
    active_id = bash.manager.start("sleep 20")
    assert work.background_problem()
    agent.reset()
    assert not bash.manager.tasks
    with pytest.raises(ValueError, match="Unknown"):
        bash.manager.status(active_id)


def test_subagent_shares_manager_and_parent_owns_cleanup(tmp_path):
    from tracecoder.agent import Agent
    from tracecoder.demo import ScriptedLLM
    from tracecoder.llm import LLMResponse, ToolCall
    from tracecoder.permissions import Permission
    from tracecoder.tools.agent import AgentTool
    from tracecoder.tools.bash import DockerBashTool

    bash = DockerBashTool(_executor(tmp_path))
    agent_tool = AgentTool()
    parent = Agent(
        ScriptedLLM(
            [
                LLMResponse(tool_calls=[ToolCall("child-start", "bash", {"command": "sleep 20", "run_in_background": True})]),
                LLMResponse(content="Child finished"),
            ]
        ),
        tools=[bash, agent_tool],
        permission=Permission(allow_all=True),
    )
    try:
        assert "Child finished" in agent_tool.execute("Start a task")
        task_id = next(iter(bash.manager.tasks))
        assert bash.manager.status(task_id)["status"] == "running"
        assert parent.background_manager is bash.manager
    finally:
        assert parent.close_background()


def test_background_failed_journal_write_is_retryable(tmp_path):
    from tracecoder.agent import Agent
    from tracecoder.demo import ScriptedLLM
    from tracecoder.tools.bash import DockerBashTool

    bash = DockerBashTool(_executor(tmp_path))
    agent = Agent(ScriptedLLM([]), tools=[bash])
    task_id = bash.manager.start("echo result")
    bash.manager.tasks[task_id].run.wait()
    seen = []

    def record(detail):
        if detail["type"] == "background_result":
            raise OSError("journal unavailable")
        seen.append(detail)
        return 1

    agent.on_background_event = record
    with pytest.raises(OSError, match="journal"):
        agent.collect_background()
    agent.on_background_event = lambda detail: seen.append(detail) or 2
    agent.collect_background()
    assert sum(item["type"] == "background_result" for item in seen) == 1
    assert agent.close_background()


def test_container_deadline_survives_cli_process_exit(tmp_path):
    _executor(tmp_path)
    script = """
import os, sys
from pathlib import Path
from tracecoder.execution import DockerExecutor
run = DockerExecutor(Path(sys.argv[1])).start('sleep 30 & wait', timeout=2, background=True)
print(run.name, flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == 0, result.stderr
    name = result.stdout.strip()
    try:
        deadline = time.monotonic() + 8
        state = {}
        while time.monotonic() < deadline:
            probe = subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", name], capture_output=True, text=True, timeout=5, check=False
            )
            assert probe.returncode == 0, probe.stderr
            state = json.loads(probe.stdout)
            if state["Status"] == "exited":
                break
            time.sleep(0.1)
        assert state["Status"] == "exited" and state["ExitCode"] == 124
    finally:
        assert DockerExecutor._cleanup(name)


def test_child_agent_call_invalidates_collected_background_evidence(tmp_path):
    journal, work, _ = _background_evidence(tmp_path)
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "child", "function": {"name": "agent", "arguments": json.dumps({"task": "Modify file"})}},
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "child", "content": "Child modified file"})
    assert not work.goal_evidence(1)[0]
    assert work.complete()["status"] != "completed"


def test_foreground_check_overlapping_background_cannot_pass(tmp_path):
    from tracecoder.session_journal import SessionJournal
    from tracecoder.work_state import WorkStateStore

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "offline")
    work = WorkStateStore(journal)
    work.ensure("Verify patch")
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "writer",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": "sleep 2; modify file", "run_in_background": True}),
                    },
                },
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "writer", "content": '{"task_id":"writer","status":"started"}'})
    journal.append_event("note", {"type": "background_start", "task_id": "writer", "tool_call_id": "writer"})
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "check", "function": {"name": "bash", "arguments": json.dumps({"command": "pytest -q"})}},
            ],
        }
    )
    foreground = journal.append_message({"role": "tool", "tool_call_id": "check", "content": "passed"})
    journal.append_event(
        "note",
        {"type": "background_result", "task_id": "writer", "status": "completed", "exit_code": 0, "cleanup_verified": True},
    )
    work.update(criteria=[{"kind": "check", "description": "check", "command": "pytest -q", "event_id": foreground}])
    assert not work.goal_evidence(1)[0]
    assert work.complete()["status"] != "completed"


def test_foreground_exit_124_is_not_a_watchdog_timeout(tmp_path):
    assert "[exit code: 124]" in _executor(tmp_path).run("exit 124")


def test_reused_call_id_does_not_exempt_later_mutation(tmp_path):
    journal, work, _ = _background_evidence(tmp_path)
    journal.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "bg-call",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "printf broken > source.py"})},
                },
            ],
        }
    )
    journal.append_message({"role": "tool", "tool_call_id": "bg-call", "content": "(no output)"})
    assert not work.goal_evidence(1)[0]
    assert work.complete()["status"] != "completed"
