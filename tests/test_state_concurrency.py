import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cluecoder.session_journal import SessionJournal
from cluecoder.tasks import TaskStore


def _worker(workspace, task_ids, session_id, barrier, results):
    workspace = Path(workspace)
    if session_id:
        journal = SessionJournal.open(workspace / "sessions", session_id, workspace, repair=False)
        barrier.wait(timeout=30)
        try:
            for index in range(8):
                journal.append_event("note", {"index": index})
            results.put("written")
        except ValueError as error:
            results.put(str(error))
    else:
        store = TaskStore(workspace)
        claims = []
        for task_id in task_ids:
            barrier.wait(timeout=30)
            try:
                store.claim(task_id, str(multiprocessing.current_process().pid))
                claims.append(True)
            except ValueError:
                claims.append(False)
            barrier.wait(timeout=30)
        results.put(claims)


def _processes(workspace, task_ids=(), session_id=None):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(4), context.Queue()
    children = [context.Process(target=_worker, args=(str(workspace), task_ids, session_id, barrier, results)) for _ in range(4)]
    for child in children:
        child.start()
    try:
        values = [results.get(timeout=60) for _ in children]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        return values
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(10)


def test_claim_is_exclusive_across_instances_and_processes(tmp_path):
    store = TaskStore(tmp_path)
    tasks = [store.create(f"task-{index}") for index in range(10)]
    claims = _processes(tmp_path, [task.id for task in tasks])
    assert [sum(worker[index] for worker in claims) for index in range(10)] == [1] * 10
    assert all(store.get(task.id).status == "in_progress" for task in tasks)
    completed = store.create("complete after a different instance claims")
    TaskStore(tmp_path).claim(completed.id, "owner")
    assert store.complete(completed.id, "owner").status == "completed"


def test_shared_journal_threads_and_stale_processes(tmp_path):
    journal = SessionJournal.create(tmp_path / "sessions", tmp_path, "probe")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda index: journal.append_event("note", {"index": index}), range(64)))
    assert len(SessionJournal.open(journal.root, journal.id, tmp_path).events) == 64
    outcomes = _processes(tmp_path, session_id=journal.id)
    assert outcomes.count("written") == 1
    assert sum("another writer" in value for value in outcomes) == 3
    restored = SessionJournal.open(journal.root, journal.id, tmp_path, repair=False)
    assert [event["seq"] for event in restored.events] == list(range(1, 73))
    with pytest.raises(ValueError, match="another writer"):
        journal.append_event("note", {"stale": True})
    restored.append_event("note", {"fresh": True})
    assert len(SessionJournal.open(journal.root, journal.id, tmp_path).events) == 73
