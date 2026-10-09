from cluecoder.demo import ScriptedLLM
from cluecoder.extraction_queue import ExtractionQueue
from cluecoder.llm import LLMResponse
from cluecoder.memory import MemoryStore
from cluecoder.session_journal import SessionJournal


def _queue(tmp_path, responses):
    workspace = tmp_path / "project"
    workspace.mkdir()
    journal = SessionJournal.create(tmp_path / "sessions", workspace, "scripted")
    project = MemoryStore(workspace)
    user = MemoryStore(workspace, scope="user", user_root=tmp_path / "user" / "memory")
    return journal, project, user, ExtractionQueue(tmp_path / "sessions", workspace, project, user,
                                                    ScriptedLLM([LLMResponse(content=item) for item in responses]))


def test_empty_extraction_is_done_without_memory_write(tmp_path):
    journal, project, user, queue = _queue(tmp_path, ["[]"])
    event_id = journal.append_message({"role": "user", "content": "hello"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert project.records() == [] and user.records() == []
    assert queue.process_pending() == 0


def test_direct_user_preference_autosaves_with_source_and_undo(tmp_path):
    candidate = ('[{"name":"language","description":"response language","type":"user",'
                 '"content":"I prefer Chinese answers","source_event_ids":[1]}]')
    journal, project, user, queue = _queue(tmp_path, [candidate])
    event_id = journal.append_message({"role": "user", "content": "I prefer Chinese answers"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert project.records() == []
    assert user.records()[0]["source_event_ids"] == [event_id]
    assert user.records()[0]["source_session_id"] == journal.id
    assert queue.undo_last().startswith("Undid")
    assert user.records() == []


def test_conflict_waits_for_review_and_can_be_accepted(tmp_path):
    candidate = ('[{"name":"language","description":"response language","type":"user",'
                 '"content":"I prefer English answers","source_event_ids":[1]}]')
    journal, _project, user, queue = _queue(tmp_path, [candidate])
    user.save_candidate("language", "response language", "I prefer Chinese answers", "user")
    event_id = journal.append_message({"role": "user", "content": "I prefer English answers"})
    queue.enqueue(journal, event_id, event_id)
    queue.process_pending()
    assert user.records()[0]["content"] == "I prefer Chinese answers"
    pending = queue.pending_reviews()
    assert len(pending) == 1
    assert queue.accept(pending[0]["id"]).startswith("Saved")
    assert user.records()[0]["content"] == "I prefer English answers"
    assert queue.undo_last().startswith("Undid")
    assert user.records()[0]["content"] == "I prefer Chinese answers"


def test_unsupported_inference_is_reviewed_and_duplicate_job_is_idempotent(tmp_path):
    candidate = ('[{"name":"style","description":"style","type":"user",'
                 '"content":"Use very terse answers","source_event_ids":[1]}]')
    journal, _, user, queue = _queue(tmp_path, [candidate])
    event_id = journal.append_message({"role": "user", "content": "Please help"})
    queue.enqueue(journal, event_id, event_id)
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert user.records() == []
    assert len(queue.pending_reviews()) == 1


def test_secret_candidate_never_enters_memory_or_review(tmp_path):
    candidate = ('[{"name":"token","description":"key","type":"user",'
                 '"content":"sk-12345678901234567890","source_event_ids":[1]}]')
    journal, project, user, queue = _queue(tmp_path, [candidate])
    event_id = journal.append_message({"role": "user", "content": "Here is sk-12345678901234567890"})
    queue.enqueue(journal, event_id, event_id)
    queue.process_pending()
    assert project.records() == [] and user.records() == []
    assert queue.pending_reviews() == []


def test_existing_session_lock_defers_processing(tmp_path):
    journal, _, _, queue = _queue(tmp_path, ["[]"])
    event_id = journal.append_message({"role": "user", "content": "hello"})
    queue.enqueue(journal, event_id, event_id)
    lock = journal.directory / "extraction.lock"
    lock.write_text("another process", encoding="utf-8")
    assert queue.process_pending() == 0
    lock.unlink()
    assert queue.process_pending() == 1


def test_plan_mode_defers_queued_job_without_consuming_retry(tmp_path):
    candidate = ('[{"name":"language","description":"response language","type":"user",'
                 '"content":"I prefer Chinese answers","source_event_ids":[1]}]')
    journal, _, user, queue = _queue(tmp_path, [candidate])
    event_id = journal.append_message({"role": "user", "content": "I prefer Chinese answers"})
    queue.enqueue(journal, event_id, event_id)
    queue.set_plan_mode(True)
    assert queue.process_pending() == 0
    assert user.records() == []
    queue.set_plan_mode(False)
    assert queue.process_pending() == 1
    assert user.records()[0]["name"] == "language"


def test_plan_mode_entered_during_extraction_defers_save(tmp_path):
    candidate = ('[{"name":"language","description":"response language","type":"user",'
                 '"content":"I prefer Chinese answers","source_event_ids":[1]}]')
    journal, _, user, queue = _queue(tmp_path, [])

    class PauseDuringModel:
        def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
            queue.set_plan_mode(True)
            return LLMResponse(content=candidate)

    queue.workflow.llm = PauseDuringModel()
    event_id = journal.append_message({"role": "user", "content": "I prefer Chinese answers"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert user.records() == []
    assert queue.status().startswith("Memory extraction: 1 queued")
    queue.set_plan_mode(False)
    queue.workflow.llm = ScriptedLLM([LLMResponse(content=candidate)])
    assert queue.process_pending() == 1
    assert user.records()[0]["name"] == "language"


def test_model_failure_is_recorded_and_retried(tmp_path):
    journal, project, user, queue = _queue(tmp_path, [])
    event_id = journal.append_message({"role": "user", "content": "hello"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert "1 failed" in queue.status()
    assert project.records() == [] and user.records() == []
    queue.workflow.llm = ScriptedLLM([LLMResponse(content="[]")])
    assert queue.process_pending() == 1
    assert "0 failed" in queue.status()


def test_empty_auxiliary_response_is_retryable_without_writing(tmp_path):
    journal, project, user, queue = _queue(tmp_path, [""])
    event_id = journal.append_message({"role": "user", "content": "Use Chinese"})
    queue.enqueue(journal, event_id, event_id)
    assert queue.process_pending() == 1
    assert "1 failed" in queue.status()
    assert project.records() == [] and user.records() == []
