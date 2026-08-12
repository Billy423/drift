"""The `(run_id, cell_key)` terminal-status store — the redelivery guard's schema.

The guard's *behaviour* is tested where it belongs, against `run_cell` (`tests/graph/test_cell.py`).
What is tested here is the thing behaviour cannot reach: the UNIQUE constraint that makes a second
terminal row for one cell key impossible at the database, and the agreement between the model the
test suite creates from metadata and the Alembic migration production creates from.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from drift.persistence.models import CellTerminalStatus, ScanRun


def _run(db_session) -> int:
    run = ScanRun(repo="/tmp/cell-store-repo", commit_sha="deadbeef", status="running")
    db_session.add(run)
    db_session.flush()
    return run.id


def _row(run_id: int, **overrides) -> CellTerminalStatus:
    fields = {
        "run_id": run_id,
        "producer": "agent",
        "unit_ref": "README.md",
        "status": "completed",
        "claims_emitted": 3,
        "error": None,
    }
    fields.update(overrides)
    return CellTerminalStatus(**fields)


def test_a_cell_key_can_only_have_one_terminal_row_per_run(db_session):
    """The constraint IS the guard: without it a redelivered cell doubles every aggregate."""
    run_id = _run(db_session)
    db_session.add(_row(run_id))
    db_session.flush()

    nested = db_session.begin_nested()  # SAVEPOINT — the duplicate aborts only this
    db_session.add(_row(run_id, status="unit_error", claims_emitted=0))
    with pytest.raises(IntegrityError):
        db_session.flush()
    nested.rollback()


def test_the_same_cell_key_in_a_different_run_is_a_different_cell(db_session):
    """`run_id` is part of the key: re-scanning a repo must not collide with the last scan."""
    first, second = _run(db_session), _run(db_session)
    db_session.add(_row(first))
    db_session.add(_row(second))
    db_session.flush()  # no IntegrityError

    # Scoped to this test's own two runs. Counting every row with that unit reference made the
    # assertion depend on whatever the database already held, which is a property of the machine
    # rather than of the key being tested.
    mine = (
        db_session.query(CellTerminalStatus)
        .filter(CellTerminalStatus.run_id.in_([first, second]))
        .filter_by(unit_ref="README.md")
        .count()
    )
    assert mine == 2


def test_both_cell_key_components_discriminate(db_session):
    """Both components discriminate: the two producers coexist on one run, as do two doc units."""
    run_id = _run(db_session)
    db_session.add(_row(run_id))
    db_session.add(_row(run_id, producer="docstrings", unit_ref="docstring_corpus"))
    db_session.add(_row(run_id, unit_ref="GUIDE.md"))
    db_session.flush()

    assert db_session.query(CellTerminalStatus).filter_by(run_id=run_id).count() == 3


def test_the_migration_and_the_model_agree_on_the_tables_shape(db_session):
    """Alembic `0003` builds production's table; `Base.metadata` builds the suite's.

    Bound, stated: `create_all` skips a table that already exists, so on a database that has been
    migrated (the dev database is at `0003`) this reflects what the MIGRATION produced and is a
    real check on it. On a database created by metadata alone it degenerates to reflecting the
    model. It is worth having anyway — the disagreement it catches is the one that only ever
    shows up in production.
    """
    columns = inspect(db_session.get_bind()).get_columns("cell_terminal_status")
    by_name = {c["name"]: c for c in columns}

    assert set(by_name) == {
        "id",
        "run_id",
        "producer",
        "unit_ref",
        "status",
        "claims_emitted",
        "error",
        "created_at",
    }
    assert by_name["error"]["nullable"] is True
    for name in ("run_id", "producer", "unit_ref", "status", "claims_emitted"):
        assert by_name[name]["nullable"] is False, name

    uniques = inspect(db_session.get_bind()).get_unique_constraints("cell_terminal_status")
    assert [(u["name"], u["column_names"]) for u in uniques] == [
        ("uq_cell_terminal_run_cell", ["run_id", "producer", "unit_ref"])
    ]
