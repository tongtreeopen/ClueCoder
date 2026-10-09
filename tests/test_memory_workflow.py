import json
from types import SimpleNamespace

from cluecoder import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLM, LLMResponse, ToolCall
from cluecoder.memory import MemoryStore
from cluecoder.memory_workflow import MemoryWorkflow


class RecordingLLM(LLM):
    def __init__(self):
        self.model = "test"
        self.extra = {"max_tokens": 900}
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.requests = []

    def _call_with_retry(self, params, max_retries=3):
        self.requests.append(params.copy())
        yield SimpleNamespace(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2), choices=[
            SimpleNamespace(delta=SimpleNamespace(content="ok", tool_calls=None))
        ])


def test_auxiliary_request_limits_output_without_changing_main_request():
    llm = RecordingLLM()
    assert llm.chat([{"role": "user", "content": "select"}], tools=[], max_output_tokens=120).content == "ok"
    assert llm.chat([{"role": "user", "content": "main"}]).content == "ok"
    assert llm.requests[0]["max_tokens"] == 120
    assert llm.requests[1]["max_tokens"] == 900
    assert "tools" not in llm.requests[0]
    assert (llm.total_prompt_tokens, llm.total_completion_tokens) == (8, 4)


def test_agent_completion_signal_excludes_round_limit():
    agent = Agent(llm=ScriptedLLM([LLMResponse(content="answer")]), tools=[])
    assert agent.chat("question") == "answer"
    assert agent.last_turn_completed is True

    limited = Agent(llm=ScriptedLLM([LLMResponse(tool_calls=[ToolCall("x", "missing", {})])]), tools=[], max_rounds=1)
    assert limited.chat("question") == "(reached maximum tool-call rounds)"
    assert limited.last_turn_completed is False


class AuxiliaryLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def chat(self, messages, tools=None, on_token=None, max_output_tokens=None):
        self.requests.append((messages, tools, max_output_tokens))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(content=answer)


def test_recall_selects_once_and_marks_memory_as_untrusted(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "Use Chinese", "Always respond in Chinese")
    store.remember("build", "Run pytest", "Run tests before reporting")
    records = store.records()
    wanted = next(i for i, record in enumerate(records) if record["name"] == "style")
    llm = AuxiliaryLLM(f"[{wanted}]")
    recalled = MemoryWorkflow(store, llm).begin_turn("请用中文回复")
    assert "Always respond in Chinese" in recalled and "Run tests before reporting" not in recalled
    assert "当前用户请求优先" in recalled and "style" in recalled
    assert llm.requests[0][1] == [] and llm.requests[0][2] == 200


def test_recall_invalid_response_uses_keyword_fallback_but_valid_empty_does_not(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("中文风格", "中文答复", "使用中文")
    assert "使用中文" in MemoryWorkflow(store, AuxiliaryLLM("not json")).begin_turn("请用中文答复")
    assert MemoryWorkflow(store, AuxiliaryLLM("[]")).begin_turn("请用中文答复") == ""
    assert MemoryWorkflow(store, AuxiliaryLLM("```json\n[0]\n```" )).begin_turn("请用中文答复")


def test_recall_caps_to_five_records_and_twenty_thousand_body_characters(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(6):
        store.remember(f"item{index}", f"fact {index}", "x" * 4500)
    recalled = MemoryWorkflow(store, AuxiliaryLLM("[0, 1, 2, 3, 4, 5]")).begin_turn("facts")
    loaded = json.loads(recalled.split("\n", 2)[2])
    assert len(loaded) == 5
    assert sum(len(item["content"]) for item in loaded) == 20000


def test_extract_uses_only_current_turn_and_filters_duplicates_and_secrets(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("existing", "style", "Use Chinese")
    response = '[{"name":"duplicate","description":"same","type":"user","content":"Use Chinese"},' \
        '{"name":"secret","description":"bad","type":"project","content":"api_key=sk-' + 'a' * 24 + '"},' \
        '{"name":"style","description":"new","type":"feedback","content":"Use concise Chinese"},' \
        '{"name":"testing","description":"habit","type":"project","content":"Run pytest"}]'
    llm = AuxiliaryLLM(response)
    result = MemoryWorkflow(store, llm).finish_turn("current user", "final answer")
    assert [candidate.name for candidate in result.candidates] == ["style", "testing"]
    assert [candidate.action for candidate in result.candidates] == ["create", "create"]
    prompt = llm.requests[0][0][0]["content"]
    assert "current user" in prompt and "final answer" in prompt
    assert "tool output" not in prompt and "Use Chinese" not in prompt
    assert store.read("testing").startswith("Error")


def test_extract_conflict_requires_explicit_approval_and_detects_intervening_edit(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "old", "Old style")
    llm = AuxiliaryLLM('[{"name":"style","description":"new","type":"feedback","content":"New style"}]')
    proposal = MemoryWorkflow(store, llm).finish_turn("change", "done").candidates[0]
    assert proposal.action == "update"
    workflow = MemoryWorkflow(store, llm)
    assert workflow.save(proposal).startswith("Error: confirmation")
    store.remember("style", "manual", "Manual edit", replace=True)
    assert workflow.save(proposal, approve_replace=True).startswith("Error: memory changed")
    assert "Manual edit" in store.read("style")


def test_extract_failure_returns_warning_without_writing(tmp_path):
    store = MemoryStore(tmp_path)
    result = MemoryWorkflow(store, AuxiliaryLLM(RuntimeError("offline"))).finish_turn("question", "answer")
    assert result.candidates == [] and "offline" in result.warning
    assert store.list() == "(no memories found)"


def test_suspicious_but_unmatched_secret_gets_warning(tmp_path):
    store = MemoryStore(tmp_path)
    answer = '[{"name":"review","description":"private workflow","type":"project","content":"Keep notes private"}]'
    result = MemoryWorkflow(store, AuxiliaryLLM(answer)).finish_turn("question", "answer")
    assert len(result.candidates) == 1
    assert "sensitive" in result.warning


def test_consolidation_requires_valid_proposal_and_current_store_version(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(10):
        store.remember(f"item{index}", f"fact {index}", f"Body {index}")
    answer = '[{"name":"merged","description":"summary","type":"project","content":"Combined facts"}]'
    workflow = MemoryWorkflow(store, AuxiliaryLLM(answer))
    proposal, warning = workflow.propose_consolidation()
    assert warning is None and proposal is not None
    assert "item0" in proposal.removed and "merged" in proposal.added
    assert "item0" in store.list()
    store.remember("newer", "new", "New fact")
    assert workflow.apply_consolidation(proposal).startswith("Error: memory changed")
    assert "Body 0" in store.read("item0")


def test_consolidation_applies_and_undoes_whole_batch(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(10):
        store.remember(f"item{index}", f"fact {index}", f"Body {index}")
    answer = '[{"name":"merged","description":"summary","type":"project","content":"Combined facts"}]'
    workflow = MemoryWorkflow(store, AuxiliaryLLM(answer))
    proposal, _ = workflow.propose_consolidation()
    assert workflow.apply_consolidation(proposal) == "Saved consolidation"
    assert len(store.records()) == 1 and "Combined facts" in store.read("merged")
    assert MemoryStore(tmp_path).undo_last() == "Undid consolidation"
    assert len(store.records()) == 10 and "Body 0" in store.read("item0")


def test_invalid_consolidation_is_rejected(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(10):
        store.remember(f"item{index}", f"fact {index}", f"Body {index}")
    duplicate = '[{"name":"same","description":"one","type":"project","content":"one"},' \
        '{"name":"same","description":"two","type":"project","content":"two"}]'
    proposal, warning = MemoryWorkflow(store, AuxiliaryLLM(duplicate)).propose_consolidation()
    assert proposal is None and "invalid" in warning.lower()
    assert len(store.records()) == 10
