from typer.testing import CliRunner

from drift.cli.main import app

runner = CliRunner()


def _capture_enqueue(monkeypatch) -> list[tuple[dict, dict]]:
    """Replace `enqueue_scan` with a recorder; returns the `(repo_ref, knobs)` list."""
    import drift.cli.main as cli_mod

    seen: list[tuple[dict, dict]] = []

    def _fake(repo_ref, **knobs):
        seen.append((repo_ref, knobs))
        return "run-abc-123"

    monkeypatch.setattr(cli_mod, "enqueue_scan", _fake)
    return seen


def test_a_submitted_scan_prints_the_task_id(tmp_path, monkeypatch):
    """Submission's only output is the handle for the work somebody else will do."""
    _capture_enqueue(monkeypatch)
    result = runner.invoke(app, ["scan", str(tmp_path), "--async"])
    assert result.exit_code == 0
    assert "run-abc-123" in result.stdout


def test_a_plain_scans_budget_reaches_the_service_seam(tmp_path, monkeypatch):
    """`--budget` on the plain path is money, not decoration.

    The plain path once accepted `--budget` and `--max-s-candidates` while forwarding neither,
    so `drift scan X --budget 0.5` spent to the default ceiling. Rejecting the flags was the
    other candidate fix, and it was worse: it puts a second budget semantics on the CLI itself.
    """
    seen = _capture_enqueue(monkeypatch)
    result = runner.invoke(app, ["scan", str(tmp_path), "--async", "--budget", "0.5"])
    assert result.exit_code == 0

    (_repo_ref, knobs) = seen[0]
    assert knobs == {"budget": 0.5}


def test_a_plain_scan_without_knobs_passes_no_overrides(tmp_path, monkeypatch):
    """Unset flags carry the shared defaults, so the seam is handed their values, not None."""
    from drift.runconfig import DEFAULT_BUDGET_USD

    seen = _capture_enqueue(monkeypatch)
    assert runner.invoke(app, ["scan", str(tmp_path), "--async"]).exit_code == 0

    # One knob, because the measurement options moved to their own surface: what the ordinary
    # command hands the seam is now exactly what the ordinary command exposes.
    (_repo_ref, knobs) = seen[0]
    assert knobs == {"budget": DEFAULT_BUDGET_USD}


def test_scan_rejects_missing_path():
    """CLI exits non-zero when the given path does not exist."""
    result = runner.invoke(app, ["scan", "/no/such/path/here"])
    assert result.exit_code != 0


def test_an_unreachable_broker_prints_one_error_line(tmp_path, monkeypatch):
    """The first thing a reader meets if they try the worker path without starting Redis.

    Celery reports it as a bare `RuntimeError` from its result backend rather than as a typed
    connection error, so nothing about the exception marks it as a broker problem. That is why
    the path escaped the error-line rule until a run with the broker down printed a traceback.
    """
    import drift.cli.main as cli_mod

    def _boom(*_a, **_k):
        raise RuntimeError("Retry limit exceeded while trying to reconnect")

    monkeypatch.setattr(cli_mod, "enqueue_scan", _boom)
    result = runner.invoke(app, ["scan", str(tmp_path), "--async"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "error: the broker is unreachable" in result.output
