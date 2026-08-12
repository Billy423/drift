"""Test that the semantic-candidate cap is shared across all cells in a run."""

from __future__ import annotations

import pytest

from drift.graph.fanin import remaining_s_allowance
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.models import JournalRecord, ScanRun

_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")


def _run_with(db_session, **counts):
    """Create a run with the requested verdict and skip counts."""
    run = ScanRun(repo="/tmp/s-allowance", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, run.repo, "abc", _STAMPS)
    for record_type, n in counts.items():
        for _ in range(n):
            writer.write("semantic_judge", record_type, {"live": True})
    db_session.flush()
    return run.id


def test_a_run_that_has_adjudicated_nothing_gets_its_whole_cap(db_session):
    """A run with no adjudications retains its full allowance."""
    run_id = _run_with(db_session)
    assert remaining_s_allowance(db_session, run_id, 50) == 50


def test_verdicts_and_skips_both_spend_the_allowance(db_session):
    """Both answered and rail-skipped candidates spend the allowance."""
    run_id = _run_with(db_session, s_verdict=4, s_judge_skipped=3)
    assert remaining_s_allowance(db_session, run_id, 50) == 43


def test_the_allowance_is_scoped_to_this_run(db_session):
    """Verdicts from another run do not reduce this run's allowance."""
    neighbour = _run_with(db_session, s_verdict=10)
    mine = _run_with(db_session, s_verdict=1)
    assert neighbour != mine
    assert remaining_s_allowance(db_session, mine, 50) == 49


@pytest.mark.parametrize("already", [50, 51, 90])
def test_an_exhausted_cap_floors_at_zero_and_never_goes_negative(db_session, already):
    """An exhausted allowance floors at zero to prevent negative slicing."""
    run_id = _run_with(db_session, s_verdict=already)
    assert remaining_s_allowance(db_session, run_id, 50) == 0


def test_the_frame_threads_the_remainder_not_the_raw_cap(tmp_path, db_session, monkeypatch):
    """Every cell receives the remaining allowance, while run config keeps the original cap."""
    from drift.graph import fanin
    from tests.fixtures.frame import frame_repo, frame_run, stub_dispatch

    repo = frame_repo(tmp_path)
    captured: list[dict] = []

    monkeypatch.setattr(fanin, "remaining_s_allowance", lambda *a, **k: 7)

    run_id, _ = frame_run(
        repo,
        db_session,
        monkeypatch,
        dispatch=stub_dispatch(
            db_session,
            hook=lambda run_id, producer, unit_ref, repo_root, config: captured.append(config),
        ),
        max_s_candidates=40,
    )

    assert [c["max_s_candidates"] for c in captured] == [7, 7]
    config = [
        r.payload
        for r in db_session.query(JournalRecord)
        .filter_by(run_id=run_id, record_type="rail_config")
        .all()
    ]
    assert config[0]["max_s_candidates"] == 40
