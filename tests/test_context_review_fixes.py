from tracecoder.context_assembler import ContextAssembler
from tracecoder.demo import ScriptedLLM
from tracecoder.llm import LLMResponse
from tracecoder.session_journal import SessionJournal
from tracecoder.work_state import WorkStateStore


def test_snipped_summary_keeps_original_source_cursor(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    messages = [{"role": "user", "content": f"request {i}"} for i in range(60)]
    for message in messages:
        journal.append_message(message)
    model = ScriptedLLM([LLMResponse(content="summary")])
    assembler = ContextAssembler(journal, WorkStateStore(journal), lambda: "", 128000, summary_llm=model)
    first = assembler.build("system", messages, force_compact=True)
    assert assembler._summary_count == 59
    assert assembler.last_compaction["source_event_range"] == [1, 59]
    assert first[-1] == messages[-1]
    second = assembler.build("system", messages, force_compact=True)
    assert second[1:] == messages[-1:] and assembler.last_compaction["summary"] == ""
    assert len(model._turns) == 0


def test_current_tool_result_fits_by_archiving_without_losing_request(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "m")
    content = "output " * 2800
    messages = [{"role": "user", "content": "Keep this request"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "read"}]},
                {"role": "tool", "tool_call_id": "read", "content": content}]
    for message in messages:
        journal.append_message(message)
    assembler = ContextAssembler(journal, WorkStateStore(journal), lambda: "", 4000)
    view = assembler.build("system", messages)
    assert view[1] == messages[0] and view[-2]["tool_calls"] == messages[1]["tool_calls"]
    assert view[-1]["archive_ref"] and view[-1]["tool_call_id"] == "read"
    assert assembler.last_compaction["estimated_tokens"] <= assembler._input_budget()
    assert "tool_result_fit" in assembler.last_compaction["stages"]
    assert journal.read_archive(3, max_chars=len(content)) == content
    assert messages[-1]["content"] == content
