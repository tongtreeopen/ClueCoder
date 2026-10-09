import pytest

from cluecoder.context import estimate_tokens
from cluecoder.context_assembler import ContextAssembler
from cluecoder.memory import MemoryStore
from cluecoder.session_journal import SessionJournal
from cluecoder.tools.memory import SearchMemoryTool
from cluecoder.tools.session import ReadSessionEventTool, SearchSessionTool
from cluecoder.tools.work import ReadWorkStateTool
from cluecoder.work_state import WorkStateStore


@pytest.mark.parametrize(("history", "actions"), [(0, 8), (0, 30), (9, 8), (9, 30)])
def test_active_request_survives_recent_tail_and_snipping(tmp_path, history, actions):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    request = "Return ONLY JSON; run the fixed command in background; do not change check.py."
    messages = [{"role": "user", "content": f"old {i}"} for i in range(history)]
    messages.append({"role": "user", "content": request})
    for i in range(actions):
        messages.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"id": str(i)}]},
            {"role": "tool", "tool_call_id": str(i), "content": "result"},
        ])
    for message in messages:
        journal.append_message(message)
    built = ContextAssembler(journal, work, lambda: "", max_tokens=128000, recent_messages=4).build("system", messages)
    assert any(m.get("role") == "user" and m.get("content") == request for m in built)
    assert {call["id"] for m in built for call in m.get("tool_calls", [])} == {
        m["tool_call_id"] for m in built if m.get("role") == "tool"}
    assert messages[history]["content"] == request and estimate_tokens(built) <= 128000


def test_l0_keeps_recent_messages_and_only_memory_index(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Fix the project")
    memory = MemoryStore(tmp_path)
    memory.save_candidate("style", "Preferred style", "PRIVATE MEMORY BODY", "user")
    messages = []
    for index in range(12):
        message = {"role": "user", "content": f"request {index}"}
        journal.append_message(message)
        messages.append(message)
    assembled = ContextAssembler(journal, work, memory.index, max_tokens=1200).build("system rules", messages)
    text = str(assembled)
    assert "request 11" in text
    assert "request 0" not in text
    assert "PRIVATE MEMORY BODY" not in text
    assert "style" in text
    assert "Older session events" in text
    assert estimate_tokens(assembled) <= 1200


def test_l0_keeps_tool_call_result_pair(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Task")
    messages = [{"role": "user", "content": f"old {index}"} for index in range(9)]
    messages += [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]
    for message in messages:
        journal.append_message(message)
    built = ContextAssembler(journal, work, lambda: "", max_tokens=1200).build("system", messages)
    assert built[-2:] == messages[-2:]


def test_recent_tail_keeps_tool_call_with_results(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    messages = [{"role": "user", "content": "task"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": f"call-{index}"} for index in range(2)]}]
    messages.extend({"role": "tool", "tool_call_id": f"call-{index}", "content": "result"}
                    for index in range(2))
    for index in range(2, 5):
        messages.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{index}"}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": "result"},
        ])
    for message in messages:
        journal.append_message(message)

    built = ContextAssembler(journal, work, lambda: "", max_tokens=1200).build("system", messages)
    tool_calls = {call["id"] for item in built for call in item.get("tool_calls", [])}

    assert all(item["tool_call_id"] in tool_calls for item in built if item.get("role") == "tool")


def test_session_search_then_read_is_bounded_and_low_trust(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    event_id = journal.append_message({"role": "tool", "tool_call_id": "x", "content": "important " + "x" * 12000})
    assert f"event:{event_id}" in SearchSessionTool(journal).execute("important")
    detail = ReadSessionEventTool(journal).execute(event_id)
    assert "untrusted" in detail
    assert len(detail) < 9000


def test_memory_index_does_not_read_body_after_index_exists(tmp_path, monkeypatch):
    memory = MemoryStore(tmp_path)
    memory.save_candidate("style", "Preferred style", "PRIVATE MEMORY BODY", "user")
    monkeypatch.setattr(memory, "_records", lambda: (_ for _ in ()).throw(AssertionError("body loaded")))
    assert "style" in memory.index()
    assert "PRIVATE MEMORY BODY" not in memory.index()


def test_memory_and_work_l1_l2_reads_are_explicit(tmp_path):
    memory = MemoryStore(tmp_path)
    memory.save_candidate("style", "Preferred style", "PRIVATE MEMORY BODY", "user")
    assert "style" in SearchMemoryTool(memory).execute("preferred")
    assert "PRIVATE MEMORY BODY" not in SearchMemoryTool(memory).execute("preferred")
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    work.ensure("Fix the project")
    assert "Fix the project" in ReadWorkStateTool(work).execute()


def test_budget_formula_includes_output_reserve_and_schema(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=128_000, recent_messages=50)
    assert assembler._input_budget() == 111_104
    with pytest.raises(ValueError, match="Context budget exceeded"):
        ContextAssembler(journal, work, lambda: "", max_tokens=1200).build(
            "system", [{"role": "user", "content": "request"}],
            tools=[{"description": "界" * 10000}],
        )


def test_estimate_scale_uses_recent_usage_for_the_same_model(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    journal.append_event("note", {"type": "context_usage", "model": "model-a",
                                   "prompt_tokens": 150, "estimated_tokens": 100})
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=128_000)
    assert assembler._estimate_scale("model-a") == pytest.approx(1.575)
    assert assembler._estimate_scale("model-b") == 1.0


def test_snip_compact_keeps_recent_request_and_complete_tool_pairs(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    messages = [{"role": "user", "content": f"request {index}"} for index in range(14)]
    messages += [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-tail"}]},
        {"role": "tool", "tool_call_id": "call-tail", "content": "result-tail"},
    ]
    messages += [{"role": "user", "content": f"request {index}"} for index in range(16, 60)]
    for message in messages:
        journal.append_message(message)
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=128_000, recent_messages=50)
    view = assembler.build("system", messages)
    ids = {call["id"] for item in view for call in item.get("tool_calls", [])}
    assert "request 59" in str(view)
    assert "snip_compact" in assembler.last_compaction["stages"]
    assert any(item.get("tool_call_id") == "call-tail" and "call-tail" in ids for item in view)
    seen = set()
    for message in view:
        seen.update(call["id"] for call in message.get("tool_calls", []))
        if message.get("role") == "tool":
            assert message["tool_call_id"] in seen


def test_microcompact_preserves_last_three_consumed_tool_results(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    messages = [{"role": "user", "content": "task"}]
    for index in range(6):
        interaction = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"call-{index}"}]},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": str(index) * 5000},
            {"role": "assistant", "content": f"consumed {index}"},
        ]
        messages.extend(interaction)
        for message in interaction:
            journal.append_message(message)
    messages.append({"role": "user", "content": "continue"})
    journal.append_message(messages[-1])
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=15000, recent_messages=50)
    view = assembler.build("system", messages)
    tool_results = [item for item in view if item.get("role") == "tool"]
    assert "micro_compact" in assembler.last_compaction["stages"]
    assert [item["content"] for item in tool_results[-3:]] == [str(index) * 5000 for index in range(3, 6)]
    assert any("older tool result archived" in item["content"] for item in tool_results[:-3])
    assert journal.read_archive(2).startswith("00000")
    assert "archived_content" in ReadSessionEventTool(journal).execute(2)


def test_tool_result_budget_archives_large_current_batch(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    calls = [{"id": f"call-{index}"} for index in range(11)]
    messages = [{"role": "assistant", "content": None, "tool_calls": calls}]
    journal.append_message(messages[0])
    for call in calls:
        message = {"role": "tool", "tool_call_id": call["id"], "content": "result " + "x" * 20000}
        messages.append(message)
        journal.append_message(message)
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=128_000, recent_messages=50)
    view = assembler.build("system", messages)
    assert "tool_result_budget" in assembler.last_compaction["stages"]
    tools = [message for message in view[1:] if message.get("role") == "tool"]
    assert any(message.get("archive_ref") for message in tools)
    assert sum(len(message["content"]) for message in tools) <= 200_000
    assert len(journal.read_archive(2)) > 6000


def test_explicit_compact_summarizes_once_and_keeps_current_request(tmp_path):
    class CountingSummary:
        calls = 0

        def chat(self, messages):
            self.calls += 1
            return type("Response", (), {"content": "summary"})()

    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    work = WorkStateStore(journal)
    messages = [{"role": "user", "content": f"old request {i} " + "x" * 1600} for i in range(7)]
    messages.append({"role": "user", "content": "current request"})
    summary = CountingSummary()
    assembler = ContextAssembler(journal, work, lambda: "", max_tokens=5000, summary_llm=summary)
    first = assembler.build("system", messages, force_compact=True)
    second = assembler.build("system", messages)
    assert "current request" in str(first) and "current request" in str(second)
    assert summary.calls == 1
