"""The cell's Celery task: argument marshalling, its configuration, and ONE eager end-to-end.

The task is a thin wrapper by design — `client=` and `session_factory=` cannot cross a broker
message, so every other test of cell behaviour calls `run_cell` directly. What only the task can
answer is whether the wrapper passes what it was given, whether its configuration is the one the
spec requires (`max_retries = 0`), and whether the whole thing survives a real dispatch.

The end-to-end runs against a REAL `SessionLocal` and commits for real (that is what makes it an
end-to-end), so it cleans up by `run_id` AND by repo path: `cell_terminal_status` has no `repo`
column, and cleaning only by path would leave its rows behind.
"""

from __future__ import annotations

import pytest

from drift.persistence.db import SessionLocal
from drift.persistence.models import CellTerminalStatus, Issue, JournalRecord, ScanRun
from drift.tasks.celery_app import celery_app
from drift.tasks.cells import run_cell_task
from tests.fixtures.step2_substrate import (
    SUBSTRATE_COMMIT_SHA,
    build_substrate_repo,
    make_substrate_client,
)

_CONFIG = {
    "budget": 5.0,
    "strict_measurement": False,
    "max_s_candidates": 50,
    "doc_filter": None,
}


@pytest.fixture
def eager_celery():
    """Run Celery tasks synchronously in-process (no live worker / broker)."""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


# --- configuration ------------------------------------------------------------------------


def test_the_cell_task_never_retries():
    """Celery's default would re-run PAID work outside the frame's wallet's knowledge."""
    assert run_cell_task.max_retries == 0


def test_the_cell_task_has_a_stable_name_and_is_registered():
    """The name is broker-visible: renaming it strands in-flight messages."""
    assert run_cell_task.name == "drift.cells.run_cell"
    # Registry lookup by name, compared by name: Celery hands back a bound proxy, not the module
    # attribute, so identity is the wrong question — reachability under the name is the right one.
    assert celery_app.tasks["drift.cells.run_cell"].name == run_cell_task.name
    assert "drift.tasks.cells" in celery_app.conf.include


# --- argument marshalling -----------------------------------------------------------------


def test_the_task_forwards_its_arguments_and_resolves_production_defaults(monkeypatch):
    """Thin wrapper: five JSON arguments in, `run_cell`'s own defaults for the two seams."""
    import drift.tasks.cells as cells_mod

    seen = {}

    def _recorder(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {
            "cell_key": ["agent", "R.md"],
            "outcome": "completed",
            "claims_emitted": 0,
            "error": None,
        }

    monkeypatch.setattr(cells_mod, "run_cell", _recorder)

    result = run_cell_task(7, "agent", "R.md", "/repo", _CONFIG)

    assert seen["args"] == (7, "agent", "R.md", "/repo", _CONFIG)
    assert seen["kwargs"] == {}, "the task must not pass a client or a session across the broker"
    assert result["outcome"] == "completed"


# --- one eager end-to-end -------------------------------------------------------------------


@pytest.fixture
def substrate_repo(tmp_path):
    return str(build_substrate_repo(tmp_path))


@pytest.fixture
def committed_run(substrate_repo):
    """A real committed `ScanRun`, plus teardown of everything the cell will commit under it."""
    session = SessionLocal()
    run = ScanRun(repo=substrate_repo, commit_sha=SUBSTRATE_COMMIT_SHA, status="running")
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()
    yield run_id
    session = SessionLocal()
    # Children first (both carry a `scan_run.id` FK), and by BOTH keys: `cell_terminal_status`
    # has no `repo` column, so a path-only sweep would leave its rows in a shared database.
    session.query(CellTerminalStatus).filter_by(run_id=run_id).delete()
    session.query(JournalRecord).filter_by(repo=substrate_repo).delete()
    session.query(Issue).filter_by(repo=substrate_repo).delete()
    session.query(ScanRun).filter_by(repo=substrate_repo).delete()
    session.commit()
    session.close()


def test_a_cell_runs_standalone_under_eager_celery(
    eager_celery, substrate_repo, committed_run, monkeypatch
):
    """`.delay()` → the task → `run_cell` → a real session, real commits, real rows.

    `anthropic.Anthropic` is monkeypatched to the substrate's scripted client rather than passed
    in, because the production default is exactly what this test exists to exercise: the task
    resolves its own client, and no argument it could be handed would prove that.
    """
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: make_substrate_client())

    async_result = run_cell_task.delay(committed_run, "agent", "README.md", substrate_repo, _CONFIG)
    outcome = async_result.get()

    assert outcome["outcome"] == "completed"
    assert outcome["cell_key"] == ["agent", "README.md"]
    assert outcome["claims_emitted"] > 0

    session = SessionLocal()
    try:
        rows = session.query(JournalRecord).filter_by(run_id=committed_run).all()
        by_type: dict[str, int] = {}
        for row in rows:
            by_type[row.record_type] = by_type.get(row.record_type, 0) + 1
        # Everything a cell owes, for one cell's scope — and nothing run-scoped. `rail_config`,
        # `frame_plan` and `run_cost` belong to the frame, so a cell writing one would be a duty
        # that crossed the boundary.
        assert by_type["cell_result"] == 1
        assert by_type["claim_inventory"] == outcome["claims_emitted"]
        assert by_type["agent_coverage"] == 1
        assert by_type["s_verdict"] >= 1
        assert {"rail_config", "frame_plan", "run_cost"} & set(by_type) == set()

        stored = session.query(CellTerminalStatus).filter_by(run_id=committed_run).one()
        assert (stored.producer, stored.unit_ref, stored.status) == (
            "agent",
            "README.md",
            "completed",
        )
    finally:
        session.close()
