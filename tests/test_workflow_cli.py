import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from cluecoder.agent import Agent
from cluecoder.demo import ScriptedLLM
from cluecoder.goal import GoalController
from cluecoder.goal_evaluator import GoalEvaluator
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.permissions import Permission
from cluecoder.session_journal import SessionJournal
from cluecoder.tools.base import Tool
from cluecoder.tools.workflow import WorkflowStatusTool, WorkflowTool
from cluecoder.work_state import WorkStateStore
from tests.test_workflow_runtime import repository, response


def test_agent_workflow_permission_and_plan_boundaries(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    model = ScriptedLLM([response(), response(), response()])
    tool = WorkflowTool(tmp_path)
    parent = Agent(model, tools=[tool, WorkflowStatusTool(tmp_path)], permission=Permission(allow_all=True))
    result = json.loads(tool.execute("review-changes"))
    assert result["status"] == "completed"
    assert json.loads(parent.tools[1].execute(result["run_id"]))["status"] == "completed"
    parent.plan_mode = True
    assert "Plan Mode" in tool.execute("review-changes")
    denied = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("run", "workflow", {"name": "review-changes"})]),
        LLMResponse(content="denied"),
    ]), tools=[WorkflowTool(tmp_path)], permission=Permission())
    assert denied.chat("review") == "denied"
    assert "denied" in next(message["content"].lower() for message in denied.messages if message["role"] == "tool")


def test_workflow_calls_charge_parent_goal_and_stop_at_its_limit(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    journal = SessionJournal.create(tmp_path / ".cluecoder" / "sessions", tmp_path, "scripted")
    work = WorkStateStore(journal)
    goal = GoalController(journal, work, GoalEvaluator(ScriptedLLM([])))
    goal.MAX_TOKENS = 15
    goal.set("Review code")
    goal.begin()
    usages = []
    tool = WorkflowTool(tmp_path)
    parent = Agent(ScriptedLLM([response(), response(), response()]), tools=[tool], on_usage=usages.append)
    parent.goal_controller, parent.work_state = goal, work
    result = json.loads(tool.execute("review-changes"))
    assert result["status"] == "limit" and goal.tokens == 15
    assert result["usage"]["calls"] == 1 and len(usages) == 1
    again = json.loads(tool.execute("review-changes"))
    assert again["status"] == "limit" and goal.tokens == 15


def test_actual_cli_subprocess_runs_resumes_and_queries_workflow(tmp_path):
    repository(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    child_requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tools = {tool["function"]["name"] for tool in body.get("tools", [])}
            if "workflow" in tools:
                if body["messages"][-1]["role"] == "tool":
                    delta = {"content": "Workflow tool returned"}
                else:
                    text = body["messages"][-1]["content"]
                    if text.startswith("status "):
                        name, args = "workflow_status", {"run_id": text.split()[1]}
                    else:
                        name, args = "workflow", {"name": "review-changes"}
                        if text.startswith("resume "):
                            args["resume_from_run_id"] = text.split()[1]
                    delta = {"tool_calls": [{"index": 0, "id": "workflow-call", "type": "function",
                                             "function": {"name": name, "arguments": json.dumps(args)}}]}
            elif tools:
                child_requests.append(body)
                delta = {"content": response().content}
            else:
                delta = {"content": "[]"}
            chunk = {"id": "local", "object": "chat.completion.chunk", "created": 1, "model": "test",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if "tool_calls" in delta else "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CLUECODER_", "OPENAI_", "DEEPSEEK_"))}
    env.update({"CLUECODER_API_KEY": "local-synthetic-key", "CLUECODER_MODEL": "test",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "USERPROFILE": str(tmp_path.parent / (tmp_path.name + "-home")),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "NO_PROXY": "127.0.0.1,localhost"})
    try:
        def run(prompt):
            process = subprocess.run([sys.executable, "-m", "cluecoder", "--yes", "-p", prompt], cwd=tmp_path,
                                     env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
            assert process.returncode == 0, process.stdout + process.stderr

        run("review")
        run_id = next((tmp_path / ".cluecoder" / "workflows").iterdir()).name
        assert len(child_requests) == 3
        run("resume " + run_id)
        result = json.loads((tmp_path / ".cluecoder" / "workflows" / run_id / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "completed" and result["cached_steps"] == 3 and len(child_requests) == 3
        run("status " + run_id)
        assert len(child_requests) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_workflow_tool_batch_executes_serially_on_caller_thread():
    caller = threading.get_ident()
    observed = []

    class SerialTool(Tool):
        name = "workflow"
        description = "serial probe"
        parameters: ClassVar[dict] = {"type": "object"}

        def execute(self):
            observed.append(threading.get_ident())
            return "finished"

    parent = Agent(ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall("one", "workflow", {}), ToolCall("two", "workflow", {})]),
        LLMResponse(content="done"),
    ]), tools=[SerialTool()])
    assert parent.chat("run") == "done" and observed == [caller, caller]
