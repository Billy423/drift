"""`drift scan` — the whole repository, in this process by default, through a broker on request.

The default is what a reader of this repository can actually run: a database and an API key, no
message broker and no worker to install first. The broker path stays available and honest behind a
flag, because it is the shape the system is designed to scale in.
"""

import os

from typer.testing import CliRunner

from drift.cli.main import app

runner = CliRunner()


def _repo(tmp_path):
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "GUIDE.md").write_text("hello there")
    return str(tmp_path)


def _capture_frame(monkeypatch):
    seen: list[dict] = []

    def _fake(path, **kwargs):
        seen.append({"path": path, **kwargs})
        return 7, "report text here"

    monkeypatch.setattr("drift.graph.frame.run_scan", _fake)
    return seen


def _capture_enqueue(monkeypatch):
    seen: list[tuple] = []

    def _fake(repo_ref, **knobs):
        seen.append((repo_ref, knobs))
        return "task-abc"

    monkeypatch.setattr("drift.cli.main.enqueue_scan", _fake)
    return seen


def test_a_plain_scan_runs_in_this_process(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    enqueued = _capture_enqueue(monkeypatch)

    result = runner.invoke(app, ["scan", repo])

    assert result.exit_code == 0
    assert enqueued == [], "a plain scan must not need a broker"
    (call,) = frame
    assert call["path"] == os.path.abspath(repo)
    assert call["dispatch"] is not None
    assert call["plan"].kind == "repo"
    assert call["plan"].worklist == ["GUIDE.md", "README.md"]


def test_async_submits_and_returns(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    enqueued = _capture_enqueue(monkeypatch)

    result = runner.invoke(app, ["scan", repo, "--async"])

    assert result.exit_code == 0
    assert frame == [], "the submitting process must not run the scan itself"
    assert len(enqueued) == 1
    assert "task-abc" in result.stdout


def test_a_costly_scan_asks_before_spending_when_someone_is_watching(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: True)
    monkeypatch.setattr("drift.cli._run.CONFIRM_ABOVE_USD", 0.0)

    result = runner.invoke(app, ["scan", repo], input="n\n")

    assert result.exit_code == 0
    assert frame == [], "declining the estimate must not run the scan"
    assert "estimate" in result.stdout.lower() or "estimate" in result.stderr.lower()


def test_confirming_runs_the_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: True)
    monkeypatch.setattr("drift.cli._run.CONFIRM_ABOVE_USD", 0.0)

    result = runner.invoke(app, ["scan", repo], input="y\n")

    assert result.exit_code == 0
    assert len(frame) == 1


def test_yes_skips_the_question(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: True)
    monkeypatch.setattr("drift.cli._run.CONFIRM_ABOVE_USD", 0.0)

    result = runner.invoke(app, ["scan", repo, "--yes"])

    assert result.exit_code == 0
    assert len(frame) == 1


def test_a_pipeline_is_never_asked(tmp_path, monkeypatch):
    """Nobody is there to answer, and a prompt that blocks a script is worse than a large bill."""
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: False)
    monkeypatch.setattr("drift.cli._run.CONFIRM_ABOVE_USD", 0.0)

    result = runner.invoke(app, ["scan", repo])

    assert result.exit_code == 0
    assert len(frame) == 1


def test_a_cheap_scan_is_never_questioned(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    frame = _capture_frame(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: True)

    result = runner.invoke(app, ["scan", repo])

    assert result.exit_code == 0
    assert len(frame) == 1, "two short documents cost far less than the confirmation threshold"


def test_a_submitted_scan_is_never_questioned(tmp_path, monkeypatch):
    """Submission spends nothing here, and the estimate belongs to whoever runs the work."""
    repo = _repo(tmp_path)
    _capture_enqueue(monkeypatch)
    monkeypatch.setattr("drift.cli._run._is_interactive", lambda: True)
    monkeypatch.setattr("drift.cli._run.CONFIRM_ABOVE_USD", 0.0)

    result = runner.invoke(app, ["scan", repo, "--async"])

    assert result.exit_code == 0


def test_scan_on_a_non_directory_is_a_usage_error(tmp_path):
    """It used to exit 1, which said "something broke" about a typo."""
    result = runner.invoke(app, ["scan", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "not a directory" in result.stderr
