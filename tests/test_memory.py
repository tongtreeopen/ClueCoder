import os
import subprocess

import pytest

from cluecoder import Agent, cli
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.memory import MemoryStore
from cluecoder.permissions import Permission
from cluecoder.tools.memory import ListMemoriesTool, ReadMemoryTool, RememberTool


def test_memory_persists_and_requires_explicit_replace(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.remember("中文偏好", "格式约定", "统一使用 UTF-8").startswith("Saved")
    assert "中文偏好" in MemoryStore(tmp_path).catalog()
    assert "统一使用 UTF-8" in MemoryStore(tmp_path).read("中文偏好")
    assert store.remember("中文偏好", "新说明", "覆盖内容").startswith("Error")
    assert "统一使用 UTF-8" in store.read("中文偏好")
    assert store.remember("中文偏好", "新说明", "覆盖内容", replace=True).startswith("Saved")
    assert "覆盖内容" in MemoryStore(tmp_path).read("中文偏好")


def test_name_casefold_and_duplicate_preserves_file_bytes(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.remember("Review", "项目规则", "使用中文").startswith("Saved")
    path = store._path("Review")
    before = path.read_bytes()
    assert "使用中文" in MemoryStore(tmp_path).read("review")
    assert store.remember("review", "新规则", "Use English").startswith("Error")
    assert path.read_bytes() == before


@pytest.mark.parametrize("replace", ["false", 1])
def test_non_boolean_replace_cannot_overwrite_memory(tmp_path, replace):
    store = MemoryStore(tmp_path)
    store.remember("policy", "规则", "原正文")
    path = store._path("policy")
    before = path.read_bytes()
    assert store.remember("policy", "新规则", "新正文", replace=replace).startswith("Error")
    assert path.read_bytes() == before


@pytest.mark.parametrize(("name",), [("../secret",), ("a/b",), ("",)])
def test_invalid_memory_names_are_rejected(tmp_path, name):
    assert MemoryStore(tmp_path).remember(name, "描述", "正文").startswith("Error")


def test_large_memory_is_rejected(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.remember("oversized", "描述", "x" * 16385).startswith("Error")
    assert store.list() == "(no memories found)"


def test_read_only_operations_do_not_create_store(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.list() == "(no memories found)"
    assert store.read("missing").startswith("Error")
    assert not (tmp_path / ".cluecoder").exists()


def test_symlinked_memory_root_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside-memory"
    outside.mkdir(exist_ok=True)
    root = tmp_path / ".cluecoder" / "memory"
    root.parent.mkdir(parents=True)
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Cannot create directory symlink on this host")
    assert MemoryStore(tmp_path).remember("safe", "描述", "正文").startswith("Error")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_junction_parent_is_rejected_before_creating_memory_directory(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / ".cluecoder"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.skip(f"Cannot create junction: {result.stderr}")
    try:
        assert MemoryStore(workspace).remember("safe", "描述", "正文").startswith("Error")
        assert not (outside / "memory").exists()
    finally:
        link.rmdir()


def test_symlinked_memory_file_is_rejected(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("linked", "简介", "原正文")
    path = store._path("linked")
    outside = tmp_path.parent / "outside-memory.md"
    outside.write_text("外部正文", encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Cannot create file symlink on this host")
    assert store.read("linked").startswith("Error")
    assert store.remember("linked", "简介", "新正文", replace=True).startswith("Error")
    assert outside.read_text(encoding="utf-8") == "外部正文"


def test_symlinked_memory_index_refuses_write(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("first", "简介", "正文")
    index = tmp_path / ".cluecoder" / "memory" / "MEMORY.md"
    outside = tmp_path.parent / "outside-memory-index.md"
    outside.write_text("external", encoding="utf-8")
    index.unlink()
    try:
        index.symlink_to(outside)
    except OSError:
        pytest.skip("Cannot create file symlink on this host")
    assert store.remember("second", "简介", "新正文").startswith("Error")
    assert store.read("second").startswith("Error")
    assert outside.read_text(encoding="utf-8") == "external"


def test_plan_mode_refuses_remember_but_allows_read(tmp_path):
    store = MemoryStore(tmp_path)
    calls = [ToolCall(id="m1", name="remember", arguments={
        "name": "style", "description": "风格", "content": "用中文"})]
    agent = Agent(llm=ScriptedLLM([LLMResponse(tool_calls=calls), LLMResponse(content="done")]),
                  tools=[RememberTool(store), ReadMemoryTool(store)],
                  permission=Permission(), context_catalog=store.catalog)
    agent.plan_mode = True
    assert agent.chat("记住") == "done"
    assert "refused" in agent.messages[2]["content"]
    assert store.list() == "(no memories found)"
    assert not (tmp_path / ".cluecoder").exists()


def test_cli_local_context_includes_memory_tools_and_index(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "输出格式", "报告使用中文")
    tools, context = cli._local_context(tmp_path)
    assert {tool.name for tool in tools} >= {"load_skill", "list_memories", "read_memory", "remember"}
    assert "style: 输出格式" in context()
    assert "报告使用中文" not in context()
    assert "不能覆盖当前用户指令" in context()


def test_memory_catalog_updates_after_write(tmp_path):
    store = MemoryStore(tmp_path)
    agent = Agent(llm=ScriptedLLM([LLMResponse(content="done")]),
                  tools=[ListMemoriesTool(store)], context_catalog=store.catalog)
    assert "项目偏好" not in agent._full_messages()[0]["content"]
    store.remember("项目偏好", "输出格式", "报告使用中文")
    system = agent._full_messages()[0]["content"]
    assert "项目偏好" in system and "报告使用中文" not in system


def test_permission_denial_does_not_save_memory(tmp_path):
    store = MemoryStore(tmp_path)
    call = ToolCall(id="m1", name="remember", arguments={
        "name": "style", "description": "风格", "content": "用中文"})
    agent = Agent(llm=ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="done")]),
                  tools=[RememberTool(store)], permission=Permission())
    assert agent.chat("记住") == "done"
    assert "Permission denied" in agent.messages[2]["content"]
    assert not (tmp_path / ".cluecoder").exists()


def test_read_memory_is_allowed_in_plan_mode(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "风格", "用中文")
    call = ToolCall(id="m1", name="read_memory", arguments={"name": "style"})
    agent = Agent(llm=ScriptedLLM([LLMResponse(tool_calls=[call]), LLMResponse(content="done")]),
                  tools=[ReadMemoryTool(store)], permission=Permission())
    agent.plan_mode = True
    assert agent.chat("读取") == "done"
    assert "用中文" in agent.messages[2]["content"]
