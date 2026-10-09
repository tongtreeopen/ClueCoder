import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from tracecoder import Agent, cli
from tracecoder.demo import ScriptedLLM
from tracecoder.llm import LLMResponse, ToolCall
from tracecoder.permissions import Permission
from tracecoder.tasks import TaskStore
from tracecoder.tools.task import CreateTaskTool


def test_task_survives_new_store_instance(tmp_path):
    task = TaskStore(tmp_path).create("修复解析器", "覆盖空输入")
    loaded = TaskStore(tmp_path).get(task.id)
    assert loaded.subject == "修复解析器"
    assert loaded.status == "pending" and loaded.blocked_by == []


def test_task_id_mismatch_is_rejected(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("检查配置")
    path = tmp_path / ".tracecoder" / "tasks" / f"{task.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = "task_ffffffff"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="ID"):
        store.get(task.id)


def test_invalid_id_and_empty_subject(tmp_path):
    store = TaskStore(tmp_path)
    with pytest.raises(ValueError):
        store.get("../outside")
    with pytest.raises(ValueError):
        store.create("  ")


def test_symlinked_tasks_directory_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside-tasks"
    outside.mkdir(exist_ok=True)
    directory = tmp_path / ".tracecoder" / "tasks"
    directory.parent.mkdir(parents=True)
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Cannot create directory symlink on this host")
    with pytest.raises(ValueError, match="escapes"):
        TaskStore(tmp_path).create("safe")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_junction_parent_is_rejected_before_creating_tasks_directory(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / ".tracecoder"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.skip(f"Cannot create junction: {result.stderr}")
    try:
        with pytest.raises(ValueError, match="escapes"):
            TaskStore(workspace).create("safe")
        assert not (outside / "tasks").exists()
    finally:
        link.rmdir()


def test_malformed_task_status_is_a_validation_error(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("校验输入")
    path = tmp_path / ".tracecoder" / "tasks" / f"{task.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="status"):
        store.get(task.id)


def test_dependencies_block_claim_until_complete(tmp_path):
    store = TaskStore(tmp_path)
    first = store.create("建表")
    second = store.create("实现接口")
    store.update(second.id, [first.id])
    with pytest.raises(ValueError, match="blocked"):
        store.claim(second.id)
    store.claim(first.id)
    store.complete(first.id)
    assert store.claim(second.id).status == "in_progress"


def test_cycle_rejected_without_changing_task(tmp_path):
    store = TaskStore(tmp_path)
    a, b = store.create("A"), store.create("B")
    store.update(a.id, [b.id])
    with pytest.raises(ValueError, match="cycle"):
        store.update(b.id, [a.id])
    assert store.get(b.id).blocked_by == []


def try_claim(store: TaskStore, task_id: str) -> bool:
    try:
        store.claim(task_id)
    except ValueError:
        return False
    return True


def test_parallel_claim_has_one_winner(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("唯一任务")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: try_claim(store, task.id), range(2)))
    assert sorted(results) == [False, True]
    assert store.get(task.id).status == "in_progress"


def test_invalid_dependency_and_owner_leave_task_unchanged(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("A")
    with pytest.raises(ValueError, match="dependency"):
        store.update(task.id, ["task_ffffffff"])
    assert store.get(task.id).blocked_by == []
    store.claim(task.id)
    with pytest.raises(ValueError, match="owner"):
        store.complete(task.id, owner="someone-else")
    with pytest.raises(ValueError, match="pending"):
        store.update(task.id, [])
    assert store.complete(task.id).status == "completed"
    with pytest.raises(ValueError, match="completed"):
        store.complete(task.id)
    with pytest.raises(ValueError, match="completed"):
        store.claim(task.id)


def test_dependency_update_validates_values_before_writing(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("安全更新")
    path = tmp_path / ".tracecoder" / "tasks" / f"{task.id}.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="dependency"):
        store.update(task.id, [["not-an-id"]])
    assert path.read_bytes() == before


def test_claim_rejects_invalid_owner_before_writing(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("认领")
    with pytest.raises(ValueError, match="owner"):
        store.claim(task.id, owner="")
    assert store.get(task.id).status == "pending"


def test_plan_mode_refuses_task_creation(tmp_path):
    store = TaskStore(tmp_path)
    agent = Agent(llm=ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall(id="t1", name="create_task", arguments={"subject": "修复测试"})]),
        LLMResponse(content="done"),
    ]), tools=[CreateTaskTool(store)], permission=Permission())
    agent.plan_mode = True
    assert agent.chat("创建任务") == "done"
    assert "refused" in agent.messages[2]["content"]
    assert store.list() == []


def test_claimed_task_can_complete_after_restart(tmp_path):
    first = TaskStore(tmp_path)
    task = first.create("修复测试")
    first.claim(task.id)
    assert TaskStore(tmp_path).complete(task.id).status == "completed"


def test_cli_task_tools_share_workspace_store(tmp_path):
    tools, _ = cli._local_context(tmp_path)
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) >= {"create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"}
    prerequisite = json.loads(by_name["create_task"].execute("准备数据"))
    created = json.loads(by_name["create_task"].execute("检查示例"))
    assert json.loads(by_name["get_task"].execute(created["id"]))["status"] == "pending"
    assert json.loads(by_name["update_task"].execute(created["id"], [prerequisite["id"]]))["blocked_by"] == [prerequisite["id"]]
    with pytest.raises(ValueError, match="blocked"):
        by_name["claim_task"].execute(created["id"])
    by_name["claim_task"].execute(prerequisite["id"])
    by_name["complete_task"].execute(prerequisite["id"])
    assert json.loads(by_name["claim_task"].execute(created["id"]))["owner"] == "tracecoder"
    assert json.loads(by_name["complete_task"].execute(created["id"]))["status"] == "completed"
    assert {task["id"] for task in json.loads(by_name["list_tasks"].execute())} == {created["id"], prerequisite["id"]}


def test_mutating_task_tools_need_consent(tmp_path):
    store = TaskStore(tmp_path)
    pending = store.create("待处理")
    in_progress = store.create("进行中")
    store.claim(in_progress.id)
    tools, _ = cli._local_context(tmp_path)
    by_name = {tool.name: tool for tool in tools}
    requests = [
        ("create_task", {"subject": "新任务"}),
        ("update_task", {"task_id": pending.id, "blocked_by": []}),
        ("claim_task", {"task_id": pending.id}),
        ("complete_task", {"task_id": in_progress.id}),
    ]
    before = [task.__dict__ for task in store.list()]
    for name, arguments in requests:
        agent = Agent(llm=ScriptedLLM([
            LLMResponse(tool_calls=[ToolCall(id="t1", name=name, arguments=arguments)]),
            LLMResponse(content="done"),
        ]), tools=[by_name[name]], permission=Permission())
        assert agent.chat("操作") == "done"
        assert "Permission denied" in agent.messages[2]["content"]
    assert [task.__dict__ for task in store.list()] == before


def test_read_task_tools_work_in_plan_mode(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create("读取")
    tools, _ = cli._local_context(tmp_path)
    by_name = {tool.name: tool for tool in tools}
    for name, arguments in [("get_task", {"task_id": task.id}), ("list_tasks", {})]:
        agent = Agent(llm=ScriptedLLM([
            LLMResponse(tool_calls=[ToolCall(id="t1", name=name, arguments=arguments)]),
            LLMResponse(content="done"),
        ]), tools=[by_name[name]], permission=Permission())
        agent.plan_mode = True
        assert agent.chat("查看") == "done"
        assert task.id in agent.messages[2]["content"]
