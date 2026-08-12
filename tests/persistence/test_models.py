from drift.domain.findings import Confidence, IssueStatus
from drift.persistence.models import Issue, ScanRun


def test_can_insert_and_query_scan_run(db_session):
    run = ScanRun(repo="/tmp/repo", commit_sha=None, status="running")
    db_session.add(run)
    db_session.flush()
    assert run.id is not None
    assert db_session.get(ScanRun, run.id).status == "running"


def test_issue_unique_on_repo_dedup_key(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    common = dict(
        repo="/tmp/repo",
        dedup_key="abc123",
        check_id="dead_link",
        status=IssueStatus.DISCOVERED,
        confidence=Confidence.HIGH,
        payload={"summary": "x"},
    )
    db_session.add(Issue(**common))
    db_session.flush()

    nested = db_session.begin_nested()  # SAVEPOINT — the duplicate flush aborts only this
    db_session.add(Issue(**common))
    with pytest.raises(IntegrityError):
        db_session.flush()
    nested.rollback()  # ROLLBACK TO SAVEPOINT — outer transaction stays alive for teardown


def test_issue_payload_is_jsonb_roundtrip(db_session):
    issue = Issue(
        repo="/tmp/repo",
        dedup_key="k1",
        check_id="dead_link",
        status=IssueStatus.DISCOVERED,
        confidence=Confidence.HIGH,
        payload={"doc_claim": "./x.md", "code_truth": "target not found"},
    )
    db_session.add(issue)
    db_session.flush()
    db_session.expire(issue)
    assert db_session.get(Issue, issue.id).payload["doc_claim"] == "./x.md"
