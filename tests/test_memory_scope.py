from pathlib import Path

from tracecoder.cli import _local_context
from tracecoder.memory import MemoryStore
from tracecoder.tools.memory import ReadMemoryTool, SearchMemoryTool


def test_user_and_project_memory_are_isolated_and_explicit(tmp_path):
    project = MemoryStore(tmp_path)
    user = MemoryStore(tmp_path, scope="user", user_root=tmp_path / "user" / "memory")
    project.save_candidate("style", "Project style", "Use project style", "project")
    user.save_candidate("style", "User style", "Use user style", "user")

    reader = ReadMemoryTool(project, user)
    assert "Use project style" in reader.execute("style")
    assert "Use user style" in reader.execute("style", scope="user")
    assert "Use user style" not in project.index()
    assert "[user]" in SearchMemoryTool(project, user).execute("style")


def test_sourced_memory_records_keep_session_evidence(tmp_path):
    store = MemoryStore(tmp_path)
    store.apply_changes(
        {"preference": ("Response style", "Use Chinese", "user")}, {"preference": None},
        "memory preference", source="session_extraction", mode="auto",
        provenance={"preference": {"session_id": "session_1", "event_ids": [2]}},
    )
    record = store.records()[0]
    assert record["source_session_id"] == "session_1"
    assert record["source_event_ids"] == [2]
    assert record["scope"] == "project"
    assert record["version"] == 1
    assert record["created_at"] and record["updated_at"]
    assert record["write_mode"] == "auto"


def test_cli_index_discloses_user_and_project_names_only(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    MemoryStore(tmp_path).save_candidate("project_style", "Project format", "PROJECT BODY", "project")
    MemoryStore(tmp_path, scope="user").save_candidate("user_style", "Global format", "USER BODY", "user")
    tools, catalog = _local_context(tmp_path)
    index = catalog()
    assert "project_style" in index and "user_style" in index
    assert "PROJECT BODY" not in index and "USER BODY" not in index
    reader = next(tool for tool in tools if tool.name == "read_memory")
    assert "USER BODY" in reader.execute("user_style", scope="user")
