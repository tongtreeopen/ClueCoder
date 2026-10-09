import json
import subprocess
import sys
import time

import pytest

from tracecoder.workflow_journal import WorkflowJournal


def test_completed_step_survives_reopen_and_bad_tail(tmp_path):
    journal = WorkflowJournal.create(tmp_path, {"source": "frozen"})
    journal.append("step_started", {"key": "one"})
    journal.append("step_completed", {"key": "one", "result": {"status": "completed"}})
    with journal.events_path.open("ab") as stream:
        stream.write(b'{"partial":')
    restored = WorkflowJournal.open(tmp_path, journal.run_id)
    assert restored.completed_steps == {"one": {"status": "completed"}}
    assert list(restored.directory.glob("bad-tail-*.bin"))
    restored.events_path.write_text(restored.events_path.read_text().replace("frozen", "changed"), encoding="utf-8")
    # Corrupt an actual event, rather than the separate manifest.
    restored.events_path.write_text(restored.events_path.read_text().replace("one", "two"), encoding="utf-8")
    with pytest.raises(ValueError):
        WorkflowJournal.open(tmp_path, journal.run_id)


def test_bad_snapshot_is_rebuilt_and_duplicate_completion_rejected(tmp_path):
    journal = WorkflowJournal.create(tmp_path, {"source": "frozen"})
    journal.append("step_started", {"key": "one"})
    journal.append("step_completed", {"key": "one", "result": {"status": "completed"}})
    (journal.directory / "state.json").write_text("broken", encoding="utf-8")
    restored = WorkflowJournal.open(tmp_path, journal.run_id)
    assert json.loads((restored.directory / "state.json").read_text())["events"] == 2
    with pytest.raises(ValueError):
        restored.append("step_completed", {"key": "one", "result": {"status": "completed"}})


def test_os_lock_blocks_other_process_and_releases_on_exit(tmp_path):
    journal = WorkflowJournal.create(tmp_path, {"source": "frozen"})
    marker = tmp_path / "acquired"
    code = (
        "import sys; from pathlib import Path; from tracecoder.workflow_journal import WorkflowJournal; "
        "j=WorkflowJournal(Path(sys.argv[1]), sys.argv[2]); "
        "j.lock.__enter__(); Path(sys.argv[3]).write_text('acquired'); j.lock.__exit__(None,None,None)"
    )
    with journal.lock:
        child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), journal.run_id, str(marker)])
        time.sleep(0.5)
        assert not marker.exists()
    try:
        assert child.wait(timeout=10) == 0 and marker.read_text() == "acquired"
        with journal.lock:
            assert journal.directory.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
