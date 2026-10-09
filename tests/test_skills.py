import pytest

from cluecoder import Agent, cli
from cluecoder.demo import ScriptedLLM
from cluecoder.llm import LLMResponse, ToolCall
from cluecoder.permissions import Permission
from cluecoder.skills import SkillCatalog
from cluecoder.tools.agent import AgentTool
from cluecoder.tools.skill import LoadSkillTool


def test_project_skill_wins_and_loads_full_text(tmp_path):
    project = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    user = tmp_path / "user" / "review" / "SKILL.md"
    project.parent.mkdir(parents=True)
    user.parent.mkdir(parents=True)
    project.write_text("---\nname: review\ndescription: 项目规则\n---\n项目正文", encoding="utf-8")
    user.write_text("---\nname: review\ndescription: 用户规则\n---\n用户正文", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=user.parent.parent)
    assert "项目规则" in catalog.catalog() and "项目正文" not in catalog.catalog()
    assert "项目正文" in catalog.load("review")


def test_unknown_skill_is_not_a_path(tmp_path):
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    assert catalog.load("../secrets.txt").startswith("Error: unknown skill")


def test_oversized_skill_is_skipped(tmp_path):
    manifest = tmp_path / ".cluecoder" / "skills" / "large" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("x" * 65537, encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    assert "large" not in catalog.catalog()
    assert catalog.load("large").startswith("Error: unknown skill")


@pytest.mark.parametrize("frontmatter", ["name: [broken", "- not-a-mapping"])
def test_broken_yaml_is_skipped_with_warning(tmp_path, caplog, frontmatter):
    manifest = tmp_path / ".cluecoder" / "skills" / "broken" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"---\n{frontmatter}\n---\nbody", encoding="utf-8")
    assert "broken" not in SkillCatalog(tmp_path, user_root=tmp_path / "user").catalog()
    assert "Cannot load Skill" in caplog.text


def test_symlinked_skill_root_is_skipped(tmp_path):
    outside = tmp_path.parent / "outside-skills"
    manifest = outside / "unsafe" / "SKILL.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("---\nname: unsafe\ndescription: outside\n---\nsecret", encoding="utf-8")
    root = tmp_path / ".cluecoder" / "skills"
    root.parent.mkdir(parents=True)
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Cannot create directory symlink on this host")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    assert catalog.catalog() == ""


def test_skill_root_symlink_within_workspace_is_skipped(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    manifest = elsewhere / "unsafe" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: unsafe\ndescription: outside root\n---\nsecret", encoding="utf-8")
    root = tmp_path / ".cluecoder" / "skills"
    root.parent.mkdir(parents=True)
    try:
        root.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("Cannot create directory symlink on this host")
    assert "unsafe" not in SkillCatalog(tmp_path, user_root=tmp_path / "user").catalog()


def test_symlinked_skill_manifest_is_skipped(tmp_path):
    outside = tmp_path.parent / "outside-skill.md"
    outside.write_text("---\nname: outside\ndescription: 外部\n---\nsecret", encoding="utf-8")
    manifest = tmp_path / ".cluecoder" / "skills" / "linked" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    try:
        manifest.symlink_to(outside)
    except OSError:
        pytest.skip("Cannot create file symlink on this host")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    assert "outside" not in catalog.catalog()
    assert catalog.load("outside").startswith("Error: unknown skill")


def test_catalog_is_a_startup_snapshot(tmp_path):
    manifest = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: review\ndescription: old\n---\nold body", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    manifest.write_text("---\nname: review\ndescription: new\n---\nnew body", encoding="utf-8")
    assert "old" in catalog.catalog() and "new" not in catalog.catalog()
    assert "old body" in catalog.load("review")


def test_load_skill_tool_is_read_only_in_plan_mode(tmp_path):
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    agent = Agent(llm=ScriptedLLM([
        LLMResponse(tool_calls=[ToolCall(id="s1", name="load_skill", arguments={"name": "missing"})]),
        LLMResponse(content="done"),
    ]), tools=[LoadSkillTool(catalog)], permission=Permission(), context_catalog=catalog.catalog)
    agent.plan_mode = True
    assert agent.chat("查看 Skill") == "done"
    assert "unknown skill" in agent.messages[2]["content"]


def test_cli_local_context_uses_workspace_skill_catalog(tmp_path):
    manifest = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: review\ndescription: 审查代码\n---\n项目正文", encoding="utf-8")
    tools, context = cli._local_context(tmp_path)
    assert {tool.name for tool in tools} >= {"load_skill", "list_memories", "read_memory", "remember"}
    assert "review: 审查代码" in context() and "项目正文" not in context()
    assert "项目正文" in next(tool for tool in tools if tool.name == "load_skill").execute("review")


def test_skill_body_appears_only_after_tool_call(tmp_path):
    manifest = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: review\ndescription: 审查\n---\n项目正文", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    agent = Agent(llm=ScriptedLLM([LLMResponse(content="done")]),
                  tools=[LoadSkillTool(catalog)], context_catalog=catalog.catalog)
    assert "review: 审查" in agent._full_messages()[0]["content"]
    assert "项目正文" not in agent._full_messages()[0]["content"]
    assert "项目正文" in catalog.load("review")


def test_sub_agent_inherits_skill_context_and_tool(tmp_path):
    manifest = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: review\ndescription: 审查\n---\n项目正文", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")

    class RecordingLLM(ScriptedLLM):
        def __init__(self, script):
            super().__init__(script)
            self.requests = []

        def chat(self, messages, tools=None, on_token=None):
            self.requests.append((messages, tools))
            return super().chat(messages, tools, on_token)

    llm = RecordingLLM([
        LLMResponse(tool_calls=[ToolCall(id="a1", name="agent", arguments={"task": "load review"})]),
        LLMResponse(tool_calls=[ToolCall(id="s1", name="load_skill", arguments={"name": "review"})]),
        LLMResponse(content="sub done"),
        LLMResponse(content="parent done"),
    ])
    agent = Agent(llm=llm, tools=[AgentTool(), LoadSkillTool(catalog)],
                  permission=Permission(allow_all=True), context_catalog=catalog.catalog)
    assert agent.chat("delegate") == "parent done"
    assert "review: 审查" in llm.requests[1][0][0]["content"]
    assert "项目正文" not in llm.requests[1][0][0]["content"]
    assert "sub done" in agent.messages[2]["content"]
    assert "load_skill" in [tool["function"]["name"] for tool in llm.requests[1][1]]


def test_cli_one_shot_loads_project_skill(tmp_path, monkeypatch):
    manifest = tmp_path / ".cluecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("---\nname: review\ndescription: 审查\n---\n项目正文", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["cluecoder", "--api-key", "offline", "-p", "load review"])
    requests = []

    class RecordingLLM(ScriptedLLM):
        def chat(self, messages, tools=None, on_token=None):
            requests.append((messages, tools))
            return super().chat(messages, tools, on_token)

    def offline_llm(**_):
        return RecordingLLM([
            LLMResponse(tool_calls=[ToolCall(id="s1", name="load_skill", arguments={"name": "review"})]),
            LLMResponse(content="done"),
        ])

    monkeypatch.setattr(cli, "LLM", offline_llm)
    monkeypatch.setattr(cli, "LiteLLM", offline_llm)
    cli.main()
    assert "review: 审查" in requests[0][0][0]["content"]
    assert "项目正文" not in requests[0][0][0]["content"]
    assert "项目正文" in requests[1][0][-1]["content"]
