"""`drift dev` — the measurement surface, kept off the path a first-time reader walks.

These options exist to reproduce published measurements. Deleting them would make those numbers
unreproducible; leaving them on the main surface would present them as ordinary, and every visible
option reads as supported. So they move rather than disappear, and the published numbers document
the command that reproduces them.
"""

import os

from typer.testing import CliRunner

from drift.cli.main import app
from tests.cli._helptext import plain

runner = CliRunner()


def _repo(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    return str(tmp_path)


def _capture_frame(monkeypatch):
    seen: list[dict] = []

    def _fake(path, **kwargs):
        seen.append({"path": path, **kwargs})
        return 11, "report text here"

    monkeypatch.setattr("drift.graph.frame.run_scan", _fake)
    return seen


def test_the_ordinary_scan_exposes_exactly_its_public_options(tmp_path):
    """Stated as an exact set rather than as a list of absences.

    An absence test only rules out the options someone remembered to name; an exact set rules out
    the next one too, including anything a later change adds by accident.
    """
    import inspect

    from drift.cli.main import scan as ordinary_scan

    assert set(inspect.signature(ordinary_scan).parameters) == {
        "path",
        "budget",
        "run_async",
        "yes",
    }


def test_check_keeps_only_its_budget(tmp_path):
    help_text = plain(runner.invoke(app, ["check", "--help"]).stdout)
    assert "--budget" in help_text
    for option in ("--strict-measurement", "--journal-export", "--max-s-candidates"):
        assert option not in help_text


def test_the_development_surface_carries_them(tmp_path):
    help_text = plain(runner.invoke(app, ["dev", "scan", "--help"]).stdout)
    for option in ("--strict-measurement", "--journal-export", "--max-s-candidates", "--budget"):
        assert option in help_text


def test_the_development_surface_has_a_single_document_form(tmp_path, monkeypatch):
    """Every published figure came from a single-document run under strict measurement.

    Without this form the numbers are not reproducible by the published tool, which would make
    them assertions rather than measurements.
    """
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    result = runner.invoke(
        app, ["dev", "check", repo, "README.md", "--strict-measurement", "--budget", "0.2"]
    )

    assert result.exit_code == 0
    (call,) = seen
    assert call["plan"].doc_filter == "README.md"
    assert call["strict_measurement"] is True
    assert call["budget"] == 0.2


def test_the_development_scan_threads_every_knob(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)
    export = os.path.join(str(tmp_path), "rows.jsonl")

    result = runner.invoke(
        app,
        [
            "dev",
            "scan",
            repo,
            "--strict-measurement",
            "--max-s-candidates",
            "100",
            "--journal-export",
            export,
            "--budget",
            "3",
        ],
    )

    assert result.exit_code == 0
    (call,) = seen
    assert call["strict_measurement"] is True
    assert call["max_s_candidates"] == 100
    assert call["journal_export"] == export
    assert call["budget"] == 3.0


def test_a_truncated_measurement_exits_three(tmp_path, monkeypatch):
    """The code a harness reads to tell "the run was truncated" from "the scan crashed"."""
    from drift.graph.nodes.rails import StrictMeasurementAbort

    repo = _repo(tmp_path)

    def _abort(path, **kwargs):
        raise StrictMeasurementAbort("rail 'budget_cap:dollars' fired in lane 'discover'")

    monkeypatch.setattr("drift.graph.frame.run_scan", _abort)

    result = runner.invoke(app, ["dev", "scan", repo, "--strict-measurement"])

    assert result.exit_code == 3
    assert result.stderr.startswith("error: ")
    assert "report text" not in result.stdout


def test_an_unwritable_export_path_is_caught_before_the_run(tmp_path, monkeypatch):
    """Discovering it afterwards leaves the artifact recoverable only by a database query."""
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    result = runner.invoke(
        app, ["dev", "scan", repo, "--journal-export", os.path.join(repo, "nope", "x.jsonl")]
    )

    assert result.exit_code == 2
    assert seen == [], "the run must not start when its artifact cannot be written"


def test_the_ordinary_surface_does_not_mention_the_development_one(tmp_path):
    """Discoverable by anyone who needs it, invisible to everyone who does not."""
    top = runner.invoke(app, ["--help"]).stdout
    assert "dev" in top  # a sub-app is listed; its options are not
    assert "--strict-measurement" not in top
