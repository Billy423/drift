"""Test pre-flight cost estimation and its warning-only contract."""

from __future__ import annotations

import pytest

from drift.fsguard import B_DOC
from drift.graph import planning
from drift.persistence.models import ScanRun
from tests.fixtures.frame import frame_repo, frame_run, stub_dispatch


def test_the_constant_is_the_sum_over_sum_form_not_the_median_of_ratios():
    """The constant uses aggregate cost divided by aggregate characters."""
    assert planning.DOLLARS_PER_CHAR == 1.344560e-05
    # The printed cost is rounded, so the derivation cannot support exact equality.
    assert 34.5849 / 2_572_205 == pytest.approx(planning.DOLLARS_PER_CHAR, rel=1e-5)
    runs_form = 34.5849 / 2_595_343
    assert runs_form != pytest.approx(planning.DOLLARS_PER_CHAR, rel=1e-3)
    assert planning.DOLLARS_PER_CHAR / runs_form == pytest.approx(1.0090, rel=1e-3)
    assert 8.013001e-05 / planning.DOLLARS_PER_CHAR > 5


def test_the_estimate_counts_characters_and_stops_at_the_input_bound(tmp_path):
    """The estimate counts characters but never exceeds the input bound per unit."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "small.md").write_text("x" * 1000)
    (repo / "huge.md").write_text("x" * (B_DOC + 500_000))

    small = planning._estimate_usd(str(repo), ["small.md"])
    huge = planning._estimate_usd(str(repo), ["huge.md"])
    both = planning._estimate_usd(str(repo), ["small.md", "huge.md"])

    assert small == 1000 * planning.DOLLARS_PER_CHAR
    assert huge == B_DOC * planning.DOLLARS_PER_CHAR
    # The two equivalent multiplication orders may differ by one floating-point unit.
    assert both == pytest.approx(small + huge)
    assert planning._estimate_usd(str(repo), ["gone.md"]) == 0.0


def test_a_run_whose_estimate_dwarfs_its_budget_still_runs(
    tmp_path, db_session, monkeypatch, capsys
):
    """An estimate above budget warns without refusing the run."""
    repo = frame_repo(tmp_path, files={"A.md": "x" * 200_000})

    run_id, report = frame_run(
        repo,
        db_session,
        monkeypatch,
        budget=0.01,
        dispatch=stub_dispatch(db_session),
    )

    err = capsys.readouterr().err
    assert "pre-flight estimate" in err
    assert "a WARNING, not a limit" in err
    assert "spearman = 0.6785" in err and "WEAK" in err
    assert report
    assert run_id


def test_a_zero_unit_run_prints_no_estimate(tmp_path, db_session, monkeypatch, capsys):
    """A run with no document units omits the estimate."""
    repo = frame_repo(tmp_path, files={"Makefile": "all:\n\t@true\n"})

    frame_run(repo, db_session, monkeypatch, dispatch=stub_dispatch(db_session))

    assert "pre-flight estimate" not in capsys.readouterr().err


def test_a_document_that_matches_nothing_is_refused_before_a_run_exists(
    tmp_path, db_session, monkeypatch
):
    """An unresolvable document filter fails before a run row is created."""
    from drift.graph.planning import DocumentNotResolvable

    repo = frame_repo(tmp_path, files={"README.md": "x"})
    before = db_session.query(ScanRun).count()

    with pytest.raises(DocumentNotResolvable) as exc:
        frame_run(
            repo,
            db_session,
            monkeypatch,
            dispatch=stub_dispatch(db_session),
            doc_filter="./README.md",
        )

    assert exc.value.spelling == "./README.md"
    assert "README.md" in exc.value.near_misses
    assert db_session.query(ScanRun).count() == before
