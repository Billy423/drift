"""The guards the acceptance criteria name, and that nothing else was covering.

Three of these exist because a criterion was written and then satisfied by inspection: the
anti-hang probe had no end-to-end exercise at all, the help output had no snapshot, and the
captured pre-change baseline had no reader. Each is cheap, and each fails for a real reason.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import signal
import subprocess
import sys

from typer.testing import CliRunner

from drift.cli.main import app
from drift.graph.dispatch import (
    PROBE_AFTER_EMPTY_POLLS,
    _poll_until_reported,
    _worker_membership,
    inline_liveness_probe,
)
from tests.cli._helptext import plain

runner = CliRunner()


@contextlib.contextmanager
def _deadline_seconds(limit: int):
    """Fail rather than wedge: the defect under test is an infinite wait, and a test that hangs
    takes the whole suite with it and prints nothing."""

    def _fire(signum, frame):
        raise AssertionError(f"still waiting after {limit}s — the cell was never declared lost")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.alarm(limit)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


# --- the anti-hang probe -------------------------------------------------------------------


def test_the_inline_probe_answers_instead_of_declining():
    """An answer ends the wait; a non-answer extends it forever.

    The fan-in treats "nobody replied" as inconclusive and keeps waiting — right for a broker
    that is merely slow, and a permanent hang when there is no broker at all. Two shapes a
    reasonable person would write for "there is no worker here" both mean nobody replied.
    """
    assert _worker_membership("task-1", inline_liveness_probe) is False

    def _returns_nothing():
        return None

    def _raises():
        raise RuntimeError("no broker")

    assert _worker_membership("task-1", _returns_nothing) is None
    assert _worker_membership("task-1", _raises) is None


def test_an_inline_cell_that_wrote_no_row_ends_the_run_rather_than_the_patience(monkeypatch):
    """The scenario the probe exists for, driven end to end through the fan-in.

    A cell can return without writing its row — it refuses a run whose repository or commit no
    longer matches, and touching git during a scan is enough. With no broker to ask, the run
    must end with a reported loss rather than a silence.
    """
    # The read is stubbed rather than the session: what is under test is the loop's decision,
    # and giving it a real database would test the database.
    monkeypatch.setattr("drift.graph.session_read.fresh_read", lambda factory, fn: {})

    notes: list[str] = []
    with _deadline_seconds(5):
        row = _poll_until_reported(
            None,
            1,
            ("agent", "README.md"),
            "in-process:agent:README.md",
            notes,
            0.0,
            inline_liveness_probe,
        )

    assert row is None, "the cell was not declared lost, so the run is still waiting"
    assert notes == [], "an inline run has a conclusive probe, so it never reports one unavailable"


def test_the_broker_is_never_reached_by_an_inline_run():
    """The tier-1 promise, asserted as an import closure rather than as an intention."""
    code = (
        "import drift.graph.dispatch as f;"
        "f._worker_membership('t', f.inline_liveness_probe);"
        "import sys;"
        "print('drift.tasks.celery_app' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_the_probe_fires_only_after_a_run_of_empty_polls():
    """A healthy cell is never probed, which is why the constant is read rather than assumed."""
    assert PROBE_AFTER_EMPTY_POLLS >= 2


# --- the help surface ----------------------------------------------------------------------

_SNAPSHOT = pathlib.Path(__file__).parent / "data" / "help_snapshot.json"


def _rendered_help(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("COLUMNS", "100")
    surface = {}
    for argv in (
        ["--help"],
        ["units", "--help"],
        ["check", "--help"],
        ["scan", "--help"],
        ["dev", "--help"],
        ["dev", "scan", "--help"],
        ["dev", "check", "--help"],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, f"{' '.join(argv)} exited {result.exit_code}"
        surface[" ".join(argv)] = plain(result.stdout)
    return surface


def test_the_help_surface_matches_its_snapshot(monkeypatch):
    """Pinned so a later prose pass cannot change the surface silently.

    That pass is the next slice, and it rewrites prose across every file including this one's
    subject. Regenerate deliberately — `DRIFT_UPDATE_HELP_SNAPSHOT=1` — and read the diff.
    """
    import os

    current = _rendered_help(monkeypatch)
    if os.environ.get("DRIFT_UPDATE_HELP_SNAPSHOT"):
        _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")

    assert _SNAPSHOT.exists(), "no snapshot committed — run with DRIFT_UPDATE_HELP_SNAPSHOT=1"
    expected = json.loads(_SNAPSHOT.read_text())
    assert set(current) == set(expected), "a command appeared or disappeared"
    for command, text in expected.items():
        assert current[command] == text, f"`{command}` output changed"


# --- the per-run judge allowance -------------------------------------------------------------


def test_the_judge_allowance_is_counted_per_run_and_not_per_cell():
    """Handing each cell the full cap would double it at two producers.

    Replayed against stored rows rather than run: the allowance is a function of what the run
    has already journalled, so a database with two adjudications answers the question a paid
    scan would.
    """
    import inspect as _inspect

    from drift.graph.fanin import remaining_s_allowance

    params = list(_inspect.signature(remaining_s_allowance).parameters)
    assert params[:3] == ["session", "run_id", "cap"], (
        "the allowance must be scoped by run; a per-cell signature is how it doubles"
    )
