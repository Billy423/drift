"""Tests that exported rows preserve database aggregates and include every record type."""

from __future__ import annotations

import json

from drift.graph.cell import LANE_A_PRODUCER, LANE_B_PRODUCER, LANE_B_UNIT_REF, run_cell
from drift.journal.export import export_run
from drift.journal.writer import JournalWriter, Stamps
from drift.persistence.models import JournalRecord, ScanRun
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
_STAMPS = Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5")


def _run_two_cells(db_session, repo_root: str) -> int:
    """Run one discovery cell and one docstring-corpus cell."""
    run = ScanRun(repo=repo_root, commit_sha=SUBSTRATE_COMMIT_SHA, status="running")
    db_session.add(run)
    db_session.commit()
    # Read the id before `run_cell` closes the shared test session and expunges its instances.
    run_id = run.id
    for producer, unit_ref in (
        (LANE_B_PRODUCER, LANE_B_UNIT_REF),
        (LANE_A_PRODUCER, "README.md"),
    ):
        run_cell(
            run_id,
            producer,
            unit_ref,
            repo_root,
            _CONFIG,
            client=make_substrate_client(),
            session_factory=lambda: db_session,
        )
    return run_id


def _from_file(path) -> list[dict]:
    """Read every non-empty JSONL row from an exported artifact."""
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _aggregates(rows: list[dict]) -> dict:
    """Compute funnel, promoted-claim, and cell-disposition aggregates from rows."""
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row["record_type"]] = by_type.get(row["record_type"], 0) + 1

    def payloads(record_type: str) -> list[dict]:
        """Return payloads for one journal record type."""
        return [r["payload"] for r in rows if r["record_type"] == record_type]

    certified = {
        (p["doc_path"], p["literal"])
        for p in payloads("gate_outcome")
        if p["outcome"] == "M_CERTIFIED"
    }
    live = {
        (p["doc_path"], p["literal"])
        for p in payloads("s_verdict")
        if p["live"] is True and p["error"] is not True
    }
    return {
        "counts": by_type,
        "high": sorted(certified & live),
        "cells": sorted(
            (tuple(p["cell_key"]), p["status"], p["claims_emitted"])
            for p in payloads("cell_result")
        ),
        "claims": len(payloads("claim_inventory")),
    }


def _from_db(db_session, run_id: int) -> list[dict]:
    """Read one run's journal rows in insertion order."""
    return [
        {"record_type": r.record_type, "payload": r.payload}
        for r in db_session.query(JournalRecord)
        .filter_by(run_id=run_id)
        .order_by(JournalRecord.id)
        .all()
    ]


def test_the_exported_artifact_yields_the_same_aggregates_as_the_database(tmp_path, db_session):
    """Exported rows produce the same non-empty aggregates as database rows."""
    repo_root = str(build_substrate_repo(tmp_path))
    run_id = _run_two_cells(db_session, repo_root)
    path = tmp_path / "run.jsonl"

    written = export_run(db_session, run_id, str(path))
    file_rows = _from_file(path)

    assert written == len(file_rows)
    assert _aggregates(file_rows) == _aggregates(_from_db(db_session, run_id))
    assert _aggregates(file_rows)["high"], "no HIGH survived — the comparison would be vacuous"


def test_the_new_record_type_is_in_the_exports_composition(tmp_path, db_session):
    """Each cell exports one stamped result with its key, status, and counts intact."""
    repo_root = str(build_substrate_repo(tmp_path))
    run_id = _run_two_cells(db_session, repo_root)
    path = tmp_path / "run.jsonl"
    export_run(db_session, run_id, str(path))

    cells = [r for r in _from_file(path) if r["record_type"] == "cell_result"]

    assert len(cells) == 2
    assert sorted(tuple(r["payload"]["cell_key"]) for r in cells) == [
        (LANE_A_PRODUCER, "README.md"),
        (LANE_B_PRODUCER, LANE_B_UNIT_REF),
    ]
    assert {r["payload"]["status"] for r in cells} == {"completed"}
    assert all(set(r["payload"]["counts"]) for r in cells)
    # Export preserves the producer-identifying version triple on each `cell_result` row.
    assert all(r["agent_ver"] and r["judge_ver"] and r["model"] for r in cells)


def test_the_export_names_no_record_type_at_all(tmp_path, db_session):
    """A record type invented by the test exports without an allowlist change."""
    run = ScanRun(repo="r", commit_sha="abc", status="running")
    db_session.add(run)
    db_session.flush()
    writer = JournalWriter(db_session, run.id, "r", "abc", _STAMPS)
    writer.write("cell", "cell_result", {"cell_key": ["agent", "A.md"], "status": "completed"})
    writer.write("router", "a_stream_that_did_not_exist_yesterday", {"unit": "B.md"})
    writer.flush()
    path = tmp_path / "invented.jsonl"

    export_run(db_session, run.id, str(path))

    assert {r["record_type"] for r in _from_file(path)} == {
        "cell_result",
        "a_stream_that_did_not_exist_yesterday",
    }
