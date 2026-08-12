"""`enqueue_scan` → Celery → the pipeline completes a scan.

**Both Celery layers run eager here, which is what makes this test the one worth having.** The
frame is itself a task, and it dispatches cell tasks from inside its own eager execution — a
nesting no other test produces. The cell's eager tolerance is tested on its own; this proves it
holds one level up, where the two nest.

Offline and $0: the cell resolves its production client through `anthropic.Anthropic()`, so the
scripted substrate client is injected there — the one seam that reaches inside a task nobody can
pass arguments to. The run commits for real (the frame and its cells own their own sessions), so
every test here deletes its own rows in a `finally`.
"""

from pathlib import Path

import pytest

from drift.app.service import enqueue_scan
from drift.cli.main import app
from drift.persistence.db import SessionLocal
from drift.persistence.models import (
    CellTerminalStatus,
    Issue,
    JournalRecord,
    ScanRun,
)
from drift.tasks.celery_app import celery_app
from tests.fixtures.step2_substrate import build_substrate_repo, make_substrate_client


@pytest.fixture
def eager_celery():
    """Run Celery tasks synchronously in-process (no live worker / broker)."""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture
def scripted_cells(monkeypatch):
    """Put the substrate's scripted client inside every cell — `run_cell`'s only client seam.

    A cell resolves its own `anthropic.Anthropic()` because `client=` cannot cross a broker
    message (`graph/cell.py`). Under eager Celery there is no message, but there is also no
    argument the frame would pass, so the constructor is where an offline run gets its client.
    """
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: make_substrate_client())


@pytest.fixture
def substrate(tmp_path):
    """The substrate repo, with its rows removed afterwards — this path commits for real."""
    repo = str(build_substrate_repo(tmp_path))
    _clean(repo)
    yield repo
    _clean(repo)


def _clean(repo: str) -> None:
    session = SessionLocal()
    try:
        runs = [r.id for r in session.query(ScanRun).filter_by(repo=repo)]
        if runs:
            # FK to `scan_run`: the terminal-status rows must go before the run rows.
            session.query(CellTerminalStatus).filter(CellTerminalStatus.run_id.in_(runs)).delete(
                synchronize_session=False
            )
        session.query(JournalRecord).filter_by(repo=repo).delete()
        session.query(Issue).filter_by(repo=repo).delete()
        session.query(ScanRun).filter_by(repo=repo).delete()
        session.commit()
    finally:
        session.close()


def _rows(session, run_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in session.query(JournalRecord).filter_by(run_id=run_id):  # scoped to this run
        counts[row.record_type] = counts.get(row.record_type, 0) + 1
    return counts


def test_enqueue_scan_runs_the_frame_end_to_end(
    eager_celery, scripted_cells, substrate, monkeypatch
):
    """The whole path: enqueue_scan → Celery → the pipeline completes a scan.

    The dispatch spy is what keeps this honest: `run_scan` picks `in_process_dispatch`
    whenever it is handed a `client`, so a test that only counted `cell_result` rows would look
    identical whether the cells crossed Celery or never touched it. They must cross it here —
    that is the nesting (an eager frame task dispatching eager cell tasks) this file exists for.
    """
    from drift.tasks import cells as cells_mod

    published: list[tuple] = []
    real_apply = cells_mod.run_cell_task.apply_async

    def _spy(*args, **kwargs):
        published.append((args, kwargs))
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(cells_mod.run_cell_task, "apply_async", _spy)

    result = enqueue_scan({"path": substrate, "commit_sha": None})

    assert published, "no cell was published through Celery — the frame took an offline seam"

    assert isinstance(result, str)  # the AsyncResult id, not a run_id — the seam's contract
    verify = SessionLocal()
    try:
        runs = verify.query(ScanRun).filter_by(repo=substrate).all()
        assert len(runs) == 1  # the FRAME owns the ScanRun, and creates exactly one
        run_id = runs[0].id
        assert runs[0].status == "done"

        counts = _rows(verify, run_id)
        # The split landed on this path, not just on the CLI's: cells reported, and the frame's
        # own `finally` ran.
        assert counts.get("cell_result", 0) >= 1
        assert counts.get("frame_plan", 0) >= 1
        assert counts.get("run_cost", 0) == 1
        assert counts.get("claim_inventory", 0) >= 1
        # The discovery producer specifically reached this path. Both producers write
        # `claim_inventory`, so the count above is satisfied by the docstring producer's rows
        # alone: with discovery entirely dead the cells still complete and this test stays
        # green. Confirmed by neutering the substrate's discovery emit to `{"claims": []}`.
        lanes = {
            r.payload["lane"]
            for r in verify.query(JournalRecord).filter_by(
                run_id=run_id, record_type="claim_inventory"
            )
        }
        assert lanes == {"agent", "docstrings"}
    finally:
        verify.close()


def test_the_task_returns_the_run_id_the_frame_created(eager_celery, scripted_cells, substrate):
    """The task's return value is the `run_id` — the key to the report, not the report."""
    from drift.tasks.scan import scan_repo

    returned = scan_repo.delay({"path": substrate, "commit_sha": None}).get()

    verify = SessionLocal()
    try:
        run = verify.query(ScanRun).filter_by(repo=substrate).one()
        assert returned == run.id
    finally:
        verify.close()


def test_the_submitting_path_reaches_the_frame_through_the_service_seam(
    eager_celery, scripted_cells, substrate
):
    """Submission goes through the seam; a plain scan does not go anywhere near it.

    Both halves matter and only one of them used to be asserted. The default changed sides: a
    plain scan runs the frame in this process, and submission moved behind its own flag. A test
    that asserted only "exit 0 and one finished run" would pass on either path, which is how a
    test comes to certify the opposite of its name.
    """
    from typer.testing import CliRunner

    from drift.app import service

    submitted = []
    real_enqueue = service.enqueue_scan

    def _watch(repo_ref, **knobs):
        submitted.append(repo_ref)
        return real_enqueue(repo_ref, **knobs)

    import drift.cli.main as cli_mod

    cli_mod.enqueue_scan = _watch
    try:
        result = CliRunner().invoke(app, ["scan", substrate, "--async"])
        assert result.exit_code == 0
        assert submitted, "submission did not reach the service seam"
    finally:
        cli_mod.enqueue_scan = real_enqueue

    verify = SessionLocal()
    try:
        runs = verify.query(ScanRun).filter_by(repo=substrate).all()
        assert len(runs) == 1 and runs[0].status == "done"
    finally:
        verify.close()


def test_the_frame_refuses_a_pinned_commit_it_cannot_honour():
    """`repo_ref["commit_sha"]` is refused before any work — the frame checks nothing out.

    Deliberately no `eager_celery`: the refusal precedes any dispatch, and requesting a fixture
    the test cannot exercise misdescribes what it depends on.
    """
    from drift.tasks.scan import scan_repo

    with pytest.raises(ValueError, match="commit_sha"):
        scan_repo({"path": str(Path.cwd()), "commit_sha": "deadbeef"})
