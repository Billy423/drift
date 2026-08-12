"""`drift check <repo> <doc>` — scan one document, in this process, without a broker."""

import os

import pytest
from typer.testing import CliRunner

from drift.cli.main import app
from drift.graph.planning import DocumentNotResolvable, ScanPlan

runner = CliRunner()


def _repo(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "docs" / "guide.md").write_text("hello there")
    return str(tmp_path)


def _capture_frame(monkeypatch):
    """Replace the frame with a recorder; returns the list of keyword arguments it was handed."""
    seen: list[dict] = []

    def _fake(path, **kwargs):
        seen.append({"path": path, **kwargs})
        return 42, "report text here"

    monkeypatch.setattr("drift.graph.frame.run_scan", _fake)
    return seen


def test_check_runs_the_frame_on_the_named_document(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    result = runner.invoke(app, ["check", repo, "docs/guide.md"])

    assert result.exit_code == 0
    assert "report text here" in result.stdout
    assert "run_id: 42" in result.stderr
    (call,) = seen
    assert call["path"] == os.path.abspath(repo)
    assert call["plan"].doc_filter == "docs/guide.md"


def test_check_dispatches_in_process_so_no_broker_is_needed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    runner.invoke(app, ["check", repo, "README.md"])

    (call,) = seen
    assert call["dispatch"] is not None, "the frame would fall back to the broker"
    assert call["inspect_factory"] is not None, "the liveness probe would reach for the broker"


def test_check_accepts_every_spelling_of_the_same_document(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)
    monkeypatch.chdir(tmp_path)

    for spelling in ("docs/guide.md", "./docs/guide.md", os.path.join(repo, "docs", "guide.md")):
        runner.invoke(app, ["check", repo, spelling])

    assert [c["plan"].doc_filter for c in seen] == ["docs/guide.md"] * 3


def test_check_refuses_an_unresolvable_document_and_says_what_to_run(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _capture_frame(monkeypatch)

    result = runner.invoke(app, ["check", repo, "docs/guid.md"])

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ")
    assert "docs/guid.md" in result.stderr
    assert "2 doc unit(s)" in result.stderr
    assert "drift units" in result.stderr
    assert "docs/guide.md" in result.stderr  # the near miss
    assert "Traceback" not in result.stderr


def test_check_reports_a_document_enumeration_skipped_with_its_reason(tmp_path, monkeypatch):
    """A path inside the repo that enumeration refuses to treat as a unit, with the reason.

    A repo-escaping symlink cannot reach this branch: containment resolves it and drops the
    candidate first, which is a stricter answer arriving earlier. A non-regular file stays
    inside and is classified rather than contained away.
    """
    repo = _repo(tmp_path)
    os.mkfifo(os.path.join(repo, "pipe.md"))
    _capture_frame(monkeypatch)

    result = runner.invoke(app, ["check", repo, "pipe.md"])

    assert result.exit_code == 2
    assert "not-regular" in result.stderr
    assert "enumeration skipped it" in result.stderr


def test_check_refuses_a_document_outside_the_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("x")
    _capture_frame(monkeypatch)

    result = runner.invoke(app, ["check", repo, str(outside)])

    assert result.exit_code == 2
    assert "outside" in result.stderr.lower() or "not a scannable unit" in result.stderr


def test_check_on_a_non_directory_is_a_usage_error(tmp_path):
    result = runner.invoke(app, ["check", str(tmp_path / "nope"), "README.md"])
    assert result.exit_code == 2
    assert "not a directory" in result.stderr


def test_check_passes_the_budget_through(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    runner.invoke(app, ["check", repo, "README.md", "--budget", "0.25"])

    assert seen[0]["budget"] == 0.25


def test_check_defaults_to_a_smaller_budget_than_a_whole_repo_scan(tmp_path, monkeypatch):
    """One document is one document; inheriting a whole-repository ceiling would be surprising."""
    from drift.runconfig import DEFAULT_BUDGET_USD, DEFAULT_CHECK_BUDGET_USD

    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    runner.invoke(app, ["check", repo, "README.md"])

    assert seen[0]["budget"] == DEFAULT_CHECK_BUDGET_USD < DEFAULT_BUDGET_USD


def test_the_plan_the_cli_builds_is_what_the_frame_runs(tmp_path, monkeypatch):
    """The frame must not re-resolve: two resolutions can disagree, and one of them would win
    silently. Handing the plan down is what makes that impossible."""
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    runner.invoke(app, ["check", repo, "README.md"])

    assert isinstance(seen[0]["plan"], ScanPlan)
    assert seen[0].get("doc_filter") in (None, "README.md")


def test_a_refusal_never_reaches_the_frame(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = _capture_frame(monkeypatch)

    runner.invoke(app, ["check", repo, "nothing/here.md"])

    assert seen == []


def test_document_not_resolvable_is_the_only_way_the_cli_learns_of_a_bad_name():
    """A guard on the type the CLI catches, so a rename cannot silently turn the branch off."""
    with pytest.raises(TypeError):
        DocumentNotResolvable("x")  # the five fields are required, none of them optional
