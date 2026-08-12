import json
import os

from typer.testing import CliRunner

from drift.cli.main import app

runner = CliRunner()


def test_only_an_asynchronous_scan_reaches_the_service_seam(tmp_path, monkeypatch):
    """What must not blur is the invocation mode, and the default has changed sides.

    A plain scan now runs the frame in this process, so a reader with a database and an API key
    can run one; submission to a worker moved behind its own flag and prints a task id. The lock
    is that submitting must never run the scan here, which is the half that would be expensive
    and silent if it broke.
    """
    import drift.cli.main as cli_mod

    called = {}

    def _fake_enqueue_scan(repo_ref, **knobs):
        called["repo_ref"] = repo_ref
        called["knobs"] = knobs
        return "run-xyz"

    def _fail_run_evolve_scan(*args, **kwargs):
        raise AssertionError("evolve path must not be taken for a plain scan")

    monkeypatch.setattr(cli_mod, "enqueue_scan", _fake_enqueue_scan)
    monkeypatch.setattr("drift.graph.frame.run_scan", _fail_run_evolve_scan)
    result = runner.invoke(app, ["scan", str(tmp_path), "--async"])
    assert result.exit_code == 0
    assert called["repo_ref"] == {"path": os.path.abspath(str(tmp_path)), "commit_sha": None}
    assert "run-xyz" in result.stdout


def test_journal_export_produces_a_file_end_to_end_through_the_cli(tmp_path, monkeypatch):
    """The seam's two halves were each tested against a fake on the other side, so nothing
    asserted that `drift scan --journal-export` actually leaves a file, or that the row-count
    line reaches the operator. This stubs the graph, so no model is called, and nothing else:
    the real scan entry point, journal writer, export and database."""
    import subprocess

    from drift.graph import dispatch
    from drift.journal.writer import JournalWriter, Stamps
    from drift.persistence.db import SessionLocal
    from drift.persistence.models import JournalRecord, ScanRun
    from tests.fixtures.frame import cell_result_payload

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=repo,
        check=True,
    )

    seen = {}

    def _stub_dispatch(run_id, producer, unit_ref, repo_root, config):
        """Stand in for one cell: bill a little, then write the row the frame polls for.

        The CLI cannot inject a `dispatch=`, so the seam is the frame's own `_celery_dispatch`,
        replaced here. An earlier version stubbed `build_graph`, which the frame no longer
        calls; the property under test is unchanged — the CLI's export lands, once, with the
        cost row in it.
        """
        seen["run_id"] = run_id
        session = SessionLocal()
        try:
            writer = JournalWriter(
                session,
                run_id,
                str(repo),
                "sha",
                Stamps("agent/0.8", "sjudge/0.4", "claude-sonnet-5"),
            )
            if producer == "agent":
                writer.write(
                    "agent",
                    "agent_coverage",
                    {
                        "unit": unit_ref,
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 1000,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                )
            writer.write("cell", "cell_result", cell_result_payload((producer, unit_ref)))
            session.commit()
        finally:
            session.close()
        return f"stub:{producer}:{unit_ref}"

    monkeypatch.setattr(dispatch, "_celery_dispatch", _stub_dispatch)
    monkeypatch.setattr(dispatch, "in_process_dispatch", lambda *a, **k: _stub_dispatch)
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())
    out = tmp_path / "run.jsonl"
    result = runner.invoke(app, ["dev", "scan", str(repo), "--journal-export", str(out)])
    try:
        assert result.exit_code == 0, result.output
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert "run_cost" in {r["record_type"] for r in rows}
        assert f"journal: {len(rows)} rows" in result.stderr
    finally:
        session = SessionLocal()
        try:
            session.query(JournalRecord).filter(
                JournalRecord.run_id == seen.get("run_id", -1)
            ).delete(synchronize_session=False)
            session.query(ScanRun).filter(ScanRun.id == seen.get("run_id", -1)).delete(
                synchronize_session=False
            )
            session.commit()
        finally:
            session.close()


def test_the_single_document_help_states_what_that_run_costs():
    """A single-document run is not a one-lane run, and the help has to say so.

    Four claims, one assertion each, read off the declared docstring rather than rendered output
    so a narrow terminal cannot make this pass or fail. Rewording the help is expected; dropping
    a claim is what must fail, so each substring is that claim's load-bearing words.

    Whitespace is collapsed first, because otherwise re-wrapping a paragraph fails a lock that is
    supposed to be about what the paragraph says.
    """
    from typer.main import get_command

    help_text = " ".join(get_command(app).commands["check"].help.split())

    assert "Both producers run" in help_text  # naming one file still runs both
    assert "$0" in help_text and "no model call" in help_text  # the docstring lane is free
    assert "whole symbol table" in help_text  # a Python target is checked in full
    assert "no docstring claims" in help_text  # a prose target skips that lane entirely
