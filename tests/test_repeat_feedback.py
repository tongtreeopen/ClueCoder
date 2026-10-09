from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.permissions import Permission
from cluecoder.tools.read import ReadFileTool
from cluecoder.tools.write import WriteFileTool
from cluecoder.workspace import WorkspacePolicy


def test_repeated_unchanged_reads_warn_but_execute_and_new_turn_resets(tmp_path):
    (tmp_path / "note.txt").write_text("actual file", encoding="utf-8")
    agent = Agent(
        ScriptedLLM(
            [
                *[LLMResponse(tool_calls=[ToolCall(str(i), "read_file", {"file_path": "note.txt"})]) for i in range(3)],
                LLMResponse(content="done"),
                LLMResponse(content="next turn"),
            ]
        ),
        tools=[ReadFileTool(WorkspacePolicy(tmp_path))],
    )
    assert agent.chat("Read note.txt") == "done"
    assert "No progress" in agent._full_messages()[0]["content"]
    assert len([message for message in agent.messages if message["role"] == "tool"]) == 3
    assert agent.chat("New request") == "next turn"
    assert "No progress" not in agent._full_messages()[0]["content"]


def test_mutation_and_changed_result_reset_repeat_warning(tmp_path):
    (tmp_path / "note.txt").write_text("before", encoding="utf-8")
    agent = Agent(
        ScriptedLLM(
            [
                *[LLMResponse(tool_calls=[ToolCall(str(i), "read_file", {"file_path": "note.txt"})]) for i in range(3)],
                LLMResponse(content="done"),
            ]
        ),
        tools=[ReadFileTool(WorkspacePolicy(tmp_path)), WriteFileTool(WorkspacePolicy(tmp_path))],
        permission=Permission(allow_all=True),
    )
    assert agent.chat("Inspect") == "done"
    assert "No progress" in agent._full_messages()[0]["content"]
    agent._append_message(
        LLMResponse(tool_calls=[ToolCall("edit", "write_file", {"file_path": "note.txt", "content": "after"})]).message
    )
    agent._append_message(
        {
            "role": "tool",
            "tool_call_id": "edit",
            "content": agent._exec_tool(ToolCall("edit", "write_file", {"file_path": "note.txt", "content": "after"})),
        }
    )
    assert "No progress" not in agent._full_messages()[0]["content"]
    agent._append_message(LLMResponse(tool_calls=[ToolCall("fresh", "read_file", {"file_path": "note.txt"})]).message)
    agent._append_message(
        {
            "role": "tool",
            "tool_call_id": "fresh",
            "content": agent._exec_tool(ToolCall("fresh", "read_file", {"file_path": "note.txt"})),
        }
    )
    assert "after" in agent.messages[-1]["content"] and "No progress" not in agent._full_messages()[0]["content"]


def test_necessary_bash_rechecks_are_not_classified_as_read_only_repetition():
    agent = Agent(ScriptedLLM([]), tools=[])
    for i in range(3):
        agent._append_message(LLMResponse(tool_calls=[ToolCall(str(i), "bash", {"command": "python check.py"})]).message)
        agent._append_message({"role": "tool", "tool_call_id": str(i), "content": "passed"})
    assert "No progress" not in agent._full_messages()[0]["content"]


def test_incomplete_legacy_calls_do_not_break_request_assembly():
    agent = Agent(ScriptedLLM([]), tools=[])
    agent.messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "legacy"}, {"id": "invalid", "function": {"name": "read_file", "arguments": "not JSON"}}],
        },
        {"role": "tool", "tool_call_id": "legacy", "content": "old result"},
    ]
    assert "No progress" not in agent._full_messages()[0]["content"]
