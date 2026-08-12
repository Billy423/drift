from drift.domain.findings import (
    Confidence,
    Evidence,
    Finding,
    IssueStatus,
    Location,
)


def _finding(**overrides):
    base = dict(
        check_id="dead_link",
        identity=("README.md", "./missing.md", "0"),
        doc_location=Location("README.md", 10, 10),
        code_anchor=None,
        summary="broken link ./missing.md",
        evidence=Evidence(doc_claim="./missing.md", code_truth="target not found"),
        confidence=Confidence.HIGH,
    )
    base.update(overrides)
    return Finding(**base)


def test_finding_is_frozen():
    f = _finding()
    try:
        f.summary = "mutated"  # type: ignore[misc]
        raise AssertionError("Finding must be immutable")
    except AttributeError:
        pass  # frozen dataclass raises on assignment


def test_dedup_key_is_stable_across_instances():
    assert _finding().dedup_key == _finding().dedup_key


def test_dedup_key_ignores_line_numbers():
    # editing a doc *above* a finding shifts its line span; identity must NOT
    # churn on that — the locus lives in `identity`, lines are display-only.
    a = _finding(doc_location=Location("README.md", 10, 10))
    b = _finding(doc_location=Location("README.md", 99, 99))
    assert a.dedup_key == b.dedup_key


def test_dedup_key_changes_with_identity():
    a = _finding(identity=("README.md", "./a.md", "0"))
    b = _finding(identity=("README.md", "./b.md", "0"))
    assert a.dedup_key != b.dedup_key


def test_dedup_key_ignores_evidence_wording():
    # evidence is the contradiction made concrete (display/payload); rewording
    # it (or the code truth drifting) must NOT create a duplicate issue.
    a = _finding(evidence=Evidence(doc_claim="./missing.md", code_truth="target not found"))
    b = _finding(evidence=Evidence(doc_claim="totally different", code_truth="other"))
    assert a.dedup_key == b.dedup_key


def test_dedup_key_ignores_summary_wording():
    # summary is human prose; it must NOT affect identity
    a = _finding(summary="broken link")
    b = _finding(summary="a completely different sentence")
    assert a.dedup_key == b.dedup_key


def test_status_and_confidence_enums_present():
    assert IssueStatus.RESOLVED == "RESOLVED"
    assert Confidence.HIGH == "HIGH"
    assert [c.value for c in Confidence] == ["HIGH"]
