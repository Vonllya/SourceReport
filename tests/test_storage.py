import sqlite3
from copy import deepcopy

import pytest

from evidencepilot.fetcher import WebFetcher
from evidencepilot.providers import FakeLLMProvider
from evidencepilot.search import MockSearchProvider
from evidencepilot.storage import SQLiteStore
from evidencepilot.workflow import ResearchWorkflow


@pytest.mark.parametrize("node_status,task_status", [("completed", "running"), ("failed", "failed")])
def test_node_commit_rolls_back_checkpoint_sources_and_errors(tmp_path, node_status, task_status):
    store = SQLiteStore(str(tmp_path / "atomic.db"))
    workflow = ResearchWorkflow(FakeLLMProvider(), MockSearchProvider(), WebFetcher(), store)
    state = workflow.initial_state("Atomic checkpoint research")
    store.save_state(state)
    store.record_node(state["task_id"], "search_web", "started")
    updated = deepcopy(state)
    updated["research_round"] = 1
    updated["sources"] = [{"source_id": "S1", "title": "Docs", "url": "https://example.com"}]
    with store.connect() as db:
        db.execute("""CREATE TRIGGER interrupt_commit BEFORE INSERT ON node_runs
            WHEN NEW.status != 'started'
            BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated interruption"):
        store.commit_node(updated, "search_web", node_status, task_status, ["new error"])
    assert store.load_state(state["task_id"]) == state
    assert store.task_status(state["task_id"]) == "running"
    assert store.latest_node_run(state["task_id"])["status"] == "started"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM errors").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_restart_after_committed_search_does_not_repeat_round(tmp_path):
    class ProcessExit(BaseException):
        pass

    class ExitAfterCommit(SQLiteStore):
        def commit_node(self, state, node, node_status, task_status, errors, detail=""):
            super().commit_node(state, node, node_status, task_status, errors, detail)
            if node == "search_web" and node_status == "completed":
                raise ProcessExit()

    path = str(tmp_path / "restart.db")
    store = ExitAfterCommit(path)
    workflow = ResearchWorkflow(FakeLLMProvider(), MockSearchProvider(), WebFetcher(), store)
    with pytest.raises(ProcessExit):
        await workflow.run("How can research remain verifiable?", max_rounds=2)
    task_id = store.list_tasks()[0]["task_id"]
    saved = store.load_state(task_id)
    assert saved["research_round"] == 1
    fresh = ResearchWorkflow(FakeLLMProvider(), MockSearchProvider(), WebFetcher(), SQLiteStore(path))
    result = await fresh.resume(task_id)
    assert result["report"]
    assert result["research_round"] == 1
    assert result["metrics"]["search_count"] == saved["metrics"]["search_count"]
    assert fresh.store.task_status(task_id) == "completed"
