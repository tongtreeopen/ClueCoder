import hashlib

import pytest

from tracecoder.memory import MemoryStore


def test_typed_candidate_preserves_legacy_records_and_can_undo_after_restart(tmp_path):
    store = MemoryStore(tmp_path)
    store._path("legacy", create=True).write_text("---\nname: legacy\ndescription: old\n---\nkept", encoding="utf-8")
    assert store.records()[0]["type"] is None
    assert store.save_candidate("format", "response style", "Use Chinese", "user") == "Saved memory format"
    assert {record["name"]: record["type"] for record in MemoryStore(tmp_path).records()} == {
        "format": "user", "legacy": None,
    }
    assert "Use Chinese" in store.read("format")
    assert MemoryStore(tmp_path).undo_last() == "Undid memory format"
    assert MemoryStore(tmp_path).read("format").startswith("Error")
    assert "kept" in MemoryStore(tmp_path).read("legacy")


def test_replacement_undo_rejects_external_change_without_touching_bytes(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "old", "old body")
    before = store.read("style")
    assert store.save_candidate("style", "new", "new body", "feedback", replace=True).startswith("Saved")
    assert MemoryStore(tmp_path).undo_last() == "Undid memory style"
    assert MemoryStore(tmp_path).read("style") == before

    store.save_candidate("style", "new", "new body", "feedback", replace=True)
    store._path("style").write_text("manual body", encoding="utf-8")
    changed = store._path("style").read_bytes()
    assert MemoryStore(tmp_path).undo_last().startswith("Error: memory changed")
    assert store._path("style").read_bytes() == changed


def test_multi_record_operation_undoes_as_one_unit(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("first", "one", "old one")
    store.remember("second", "two", "old two")
    old = {name: store.hash_of(name) for name in ("first", "second", "third")}
    assert store.apply_changes({
        "first": ("merged", "combined", "project"),
        "second": None,
        "third": ("added", "new", "reference"),
    }, old, label="consolidation") == "Saved consolidation"
    assert store.read("second").startswith("Error")
    assert MemoryStore(tmp_path).undo_last() == "Undid consolidation"
    assert "old one" in store.read("first") and "old two" in store.read("second")
    assert store.read("third").startswith("Error")


def test_pending_write_recovers_without_overwriting_unrelated_change(tmp_path):
    store = MemoryStore(tmp_path)
    original = store._write_operation

    def fail_commit(path, operation):
        if operation["status"] == "committed":
            raise OSError("injected commit failure")
        original(path, operation)

    store._write_operation = fail_commit
    with pytest.raises(OSError, match="injected"):
        store.save_candidate("interrupted", "x", "body", "project")
    assert MemoryStore(tmp_path).recover_pending() == 1
    assert MemoryStore(tmp_path).read("interrupted").startswith("Error")

    store = MemoryStore(tmp_path)
    store._write_operation = fail_commit
    with pytest.raises(OSError):
        store.save_candidate("interrupted", "x", "body", "project")
    store._path("interrupted").write_text("external", encoding="utf-8")
    with pytest.raises(ValueError, match="conflict"):
        MemoryStore(tmp_path).recover_pending()
    assert store._path("interrupted").read_text(encoding="utf-8") == "external"


def test_hash_matches_file_bytes(tmp_path):
    store = MemoryStore(tmp_path)
    store.remember("style", "x", "body")
    assert store.hash_of("style") == hashlib.sha256(store._path("style").read_bytes()).hexdigest()


def test_multi_record_undo_refuses_partial_revert_after_conflict(tmp_path):
    store = MemoryStore(tmp_path)
    for name in ("first", "second"):
        store.remember(name, "old", f"Old {name}")
    old = {name: store.hash_of(name) for name in ("first", "second")}
    store.apply_changes({
        "first": ("new", "New first", "project"),
        "second": ("new", "New second", "project"),
    }, old, label="consolidation")
    store._path("second").write_text("Manual second", encoding="utf-8")
    before = {name: store._path(name).read_bytes() for name in ("first", "second")}
    assert MemoryStore(tmp_path).undo_last().startswith("Error: memory changed")
    assert {name: store._path(name).read_bytes() for name in before} == before


def test_interrupted_multi_record_write_rolls_back_all_targets(tmp_path):
    store = MemoryStore(tmp_path)
    for name in ("first", "second"):
        store.remember(name, "old", f"Old {name}")
    before = {name: store._path(name).read_bytes() for name in ("first", "second")}
    original = store._write_operation

    def fail_commit(path, operation):
        if operation["status"] == "committed":
            raise OSError("injected commit failure")
        original(path, operation)

    store._write_operation = fail_commit
    with pytest.raises(OSError):
        store.apply_changes({
            "first": ("new", "New first", "project"),
            "second": ("new", "New second", "project"),
        }, {name: store.hash_of(name) for name in before}, label="consolidation")
    assert MemoryStore(tmp_path).recover_pending() == 1
    assert {name: store._path(name).read_bytes() for name in before} == before
