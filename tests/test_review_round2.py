import pytest

from tracecoder.memory import MemoryStore
from tracecoder.skills import SkillCatalog
from tracecoder.tasks import TaskStore


def test_remember_is_versioned_and_undoable(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.remember("rule", "format", "first").startswith("Saved")
    assert store.records()[0]["write_mode"] != "legacy"
    assert store.remember("rule", "format", "second", replace=True).startswith("Saved")
    assert store.undo_last().startswith("Undid")
    assert "first" in store.read("rule") and "second" not in store.read("rule")
    assert store.undo_last().startswith("Undid")
    assert store.read("rule").startswith("Error: unknown")


def test_tasks_isolate_corrupt_records_and_sanitize_missing_id(tmp_path, caplog):
    store = TaskStore(tmp_path)
    task = store.create("valid")
    (store.directory / "task_00000000.json").write_text("{", encoding="utf-8")
    assert store.list() == [task]
    assert "Cannot load task" in caplog.text
    with pytest.raises(ValueError):
        store.update(task.id, ["task_00000000"])
    with pytest.raises(FileNotFoundError, match="Unknown task task_11111111") as error:
        store.get("task_11111111")
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(("header", "name", "description"), [
    ("", "fallback", "body"),
    ("---\nname: named\n---\n", "named", "body"),
    ("---\ndescription: described\n---\n", "fallback", "described"),
])
def test_skill_metadata_fallback(tmp_path, header, name, description):
    manifest = tmp_path / ".tracecoder/skills/fallback/SKILL.md"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(header + "body\nfull content", encoding="utf-8")
    catalog = SkillCatalog(tmp_path, user_root=tmp_path / "user")
    assert name in catalog.catalog() and description in catalog.catalog()
    assert "full content" in catalog.load(name)


def test_skill_manifest_link_inside_root_is_readable(tmp_path):
    root = tmp_path / ".tracecoder/skills"
    target = root / "source.md"
    manifest = root / "linked/SKILL.md"
    manifest.parent.mkdir(parents=True)
    target.write_text("root body\nmore text", encoding="utf-8")
    try:
        manifest.symlink_to(target)
    except OSError:
        pytest.skip("Cannot create file symlink on this host")
    assert "more text" in SkillCatalog(tmp_path, user_root=tmp_path / "user").load("linked")


def test_task_update_rejects_missing_transitive_dependency(tmp_path):
    store = TaskStore(tmp_path)
    dependency = store.create("dependency")
    task = store.create("task")
    store.update(task.id, [dependency.id])
    store._path(dependency.id).unlink()
    with pytest.raises(ValueError, match="Unknown dependency"):
        store.update(task.id, [])
    assert store.get(task.id).blocked_by == [dependency.id]
