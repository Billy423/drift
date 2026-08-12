"""Pin end-to-end dispositions for a fixed nine-claim regression corpus.

The production pipeline runs with scripted model responses: the real gate certifies six claims,
the scripted judge marks them live, and the gate classifies three as `base-ambiguous`. The corpus
commit is verified before the frame runs, so a changed tree cannot masquerade as a regression.
"""

from __future__ import annotations

import subprocess

import pytest

from drift.graph.frame import run_scan
from drift.graph.read_model import build_read_model
from drift.kernels.registry import predicate_registry
from drift.persistence.models import JournalRecord
from tests.fixtures.g3_pin import (
    CORPUS_ROOT,
    G3Client,
    corpus_sha_from_manifest,
    corpus_skip_reason,
    load_pin,
)

_PIN = load_pin()
_SKIP = corpus_skip_reason()

#: The skip reason names the missing corpus or manifest; a silent skip would hide an unchecked pin.
requires_corpus = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


def _doc_text() -> str:
    """Read the document named by the regression pin."""
    return (CORPUS_ROOT / _PIN["doc_path"]).read_text(encoding="utf-8")


def _corpus_head() -> str:
    """Return the checked-out corpus commit."""
    return subprocess.run(
        ["git", "-C", str(CORPUS_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def g3_run(db_session):
    """Run the full frame over the pinned document with scripted model responses.

    The document filter excludes units for which the scripted client has no inventory. The commit
    check precedes the scan because every expected disposition belongs to one repository tree.
    """
    assert _corpus_head() == corpus_sha_from_manifest() == _PIN["corpus"]["commit_sha"], (
        "the corpus is not at the sha the pin was measured at; re-checkout "
        f"{CORPUS_ROOT} at {_PIN['corpus']['commit_sha']} (MANIFEST.tsv) before reading anything "
        "into a pass or a failure here"
    )
    run_id, report = run_scan(
        str(CORPUS_ROOT),
        doc_filter=_PIN["doc_path"],
        client=G3Client(_PIN, _doc_text()),
        session_factory=lambda: db_session,
        poll_interval=0,
    )
    model = build_read_model(db_session, run_id)
    rows = (
        db_session.query(JournalRecord)
        .filter_by(run_id=run_id)  # scoped: never asserts over an unscoped table
        .order_by(JournalRecord.id)
        .all()
    )
    streams: dict[str, list[dict]] = {}
    for row in rows:
        streams.setdefault(row.record_type, []).append(row.payload)
    return model, report, streams


@requires_corpus
def test_the_six_g3_true_positives_still_emit_as_high(g3_run):
    """Identity-for-identity, not a count: six is also what five plus one wrong claim looks like."""
    model, _report, _streams = g3_run

    minted = {(f.check_id, f.identity) for f in model.findings}
    expected = {
        (row["predicate"], (row["doc_path"], *row["normalized_args"]))
        for row in _PIN["true_positives"]
    }
    assert minted == expected


@requires_corpus
def test_the_six_true_positives_reach_the_rendered_report(g3_run):
    """Render all six high-confidence findings in the user-facing report."""
    _model, report, _streams = g3_run

    high_section = report.split("## Ranked tier")[0]
    assert "## Verified findings — 6" in high_section
    for row in _PIN["true_positives"]:
        assert f"doc claims: {row['literal']}" in high_section


@requires_corpus
def test_the_six_are_m_certified_by_the_real_gate_and_s_passed_above_threshold(g3_run):
    """Verify the gate and judge inputs that support all six emitted findings.

    All pinned confidences exceed the threshold, so this test cannot prove that the read model
    applies the threshold. A separate below-threshold test covers that branch.
    """
    _model, _report, streams = g3_run

    certified = {
        (p["literal"], tuple(p["normalized_args"]))
        for p in streams["gate_outcome"]
        if p["outcome"] == "M_CERTIFIED"
    }
    assert certified == {
        (row["literal"], tuple(row["normalized_args"])) for row in _PIN["true_positives"]
    }

    from drift.judge.semantic_judge import S_THRESHOLD

    for verdict in streams["s_verdict"]:
        assert verdict["live"] is True
        assert verdict["confidence"] >= S_THRESHOLD


@requires_corpus
def test_base_ambiguous_still_shields_the_three_unresolvable_claims(g3_run):
    """Verify the real gate classifies exactly three unresolvable claims as `base-ambiguous`."""
    _model, _report, streams = g3_run

    shielded = {p["literal"] for p in streams["gate_ungateable"] if p["reason"] == "base-ambiguous"}
    assert shielded == {row["literal"] for row in _PIN["base_ambiguous"]}


@requires_corpus
def test_the_shielded_three_are_journal_only(g3_run):
    """Keep all three ambiguous claims out of findings, ranking, and adjudication.

    `base-ambiguous` is not a comment reason, so these claims remain journal-only. The scripted
    inventory contains only the six findings and these three claims, making an empty ranked tier
    deterministic.
    """
    model, report, streams = g3_run

    shielded = {row["literal"] for row in _PIN["base_ambiguous"]}
    assert shielded & {f.evidence.doc_claim for f in model.findings} == set()
    assert shielded & {e.claim.anchor.literal for e in model.ranked_entries} == set()
    assert model.ranked_entries == []
    assert shielded & {p["literal"] for p in streams["s_verdict"]} == set()
    for literal in shielded:
        assert f"doc claims: {literal}" not in report


def test_the_pin_records_both_of_a3s_sources_and_neither_is_assumed():
    """Preserve the distinct evidence sources for findings and ambiguous claims.

    Five findings share one report source and the sixth belongs to two sibling reports. Ambiguous
    claims have no report source because `base-ambiguous` is journal-only.
    """
    tps = _PIN["true_positives"]
    assert len(tps) == 6
    assert len(_PIN["base_ambiguous"]) == 3

    in_run70 = [row for row in tps if "report-run70.md" in row["report_sources"]]
    assert len(in_run70) == 5, "no single report carries all six — that is the point"
    only_siblings = [
        row for row in tps if row["report_sources"] == ["report-run71.md", "report-run72.md"]
    ]
    assert [row["literal"] for row in only_siblings] == ["babel_nbjs.cfg"]

    for row in _PIN["base_ambiguous"]:
        assert row["report_sources"] == []
        assert row["attested_in_runs"], "a shielded claim with no attesting run pins nothing"


def test_the_pinned_normalized_args_are_what_the_production_normalizer_derives():
    """Re-derive every pinned identity with the production normalizer.

    Normalized arguments form part of claim identity, so a normalization change invalidates the
    committed rows even when corpus-dependent tests skip.
    """
    for row in _PIN["true_positives"] + _PIN["base_ambiguous"]:
        derived = predicate_registry[row["predicate"]].normalize(
            row["literal"], row["doc_path"], None
        )
        assert derived is not None, f"{row['literal']!r} no longer binds at all"
        normalization, args = derived
        assert list(args) == row["normalized_args"]
        assert normalization == row["normalization"]
