from cluecoder import session as session_module
from cluecoder.session import load_session, resume_journal, save_session, start_journal


def test_default_session_ids_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    first_id = save_session([{"role": "user", "content": "first"}], "model-a")
    second_id = save_session([{"role": "user", "content": "second"}], "model-b")

    assert first_id != second_id
    assert load_session(first_id) == (
        [{"role": "user", "content": "first"}],
        "model-a",
    )
    assert load_session(second_id) == (
        [{"role": "user", "content": "second"}],
        "model-b",
    )


def test_session_id_path_traversal_is_neutralized(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    sid = save_session([{"role": "user", "content": "x"}], "m", "../../etc/passwd")

    assert sid == "passwd"
    assert (tmp_path / "passwd.json").exists()
    # the same traversal string round-trips through the parent-dir boundary check
    assert load_session("../../etc/passwd") == ([{"role": "user", "content": "x"}], "m")


def test_session_id_absolute_path_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    sid = save_session([{"role": "user", "content": "x"}], "m", "/etc/shadow")

    assert sid == "shadow"
    assert (tmp_path / "shadow.json").exists()


def test_session_id_windows_backslash_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    sid = save_session([{"role": "user", "content": "x"}], "m", r"..\..\secret")

    assert sid == "secret"


def test_session_id_length_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    sid = save_session([{"role": "user", "content": "x"}], "m", "a" * 500)

    assert len(sid) <= 100
    assert (tmp_path / f"{sid}.json").exists()


def test_corrupt_session_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    (tmp_path / "broken.json").write_text("{ not valid json", encoding="utf-8")

    assert load_session("broken") is None


def test_session_roundtrips_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    msgs = [{"role": "user", "content": "请帮我修复这个 bug"}]
    sid = save_session(msgs, "model-zh")

    raw = (tmp_path / f"{sid}.json").read_bytes()
    assert "请帮我修复这个 bug".encode() in raw
    assert load_session(sid) == (msgs, "model-zh")


def test_new_journal_and_legacy_import_are_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = start_journal(workspace, "model-new")
    journal.append_message({"role": "user", "content": "new"})
    assert resume_journal(journal.id, workspace).messages() == [{"role": "user", "content": "new"}]

    old_id = save_session([{"role": "user", "content": "legacy"}], "model-old")
    imported = resume_journal(old_id, workspace)
    assert imported.model == "model-old"
    assert imported.messages() == [{"role": "user", "content": "legacy"}]
    assert any(event["kind"] == "note" for event in imported.events)
    assert len(resume_journal(old_id, workspace).messages()) == 1


def test_new_journal_is_listed_and_cross_workspace_resume_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path / "sessions")
    journal = start_journal(tmp_path, "m")
    journal.append_message({"role": "user", "content": "preview"})
    assert session_module.list_sessions()[0]["id"] == journal.id
    assert session_module.list_sessions()[0]["preview"] == "preview"
    try:
        resume_journal(journal.id, tmp_path / "other")
    except ValueError as error:
        assert "workspace" in str(error)
    else:
        assert False, "cross-workspace resume should require authorization"
