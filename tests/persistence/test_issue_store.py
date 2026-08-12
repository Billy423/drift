import pytest

from drift.domain.findings import (
    Confidence,
    Evidence,
    Finding,
    IssueStatus,
    Location,
)
from drift.persistence.models import Issue, IssueEvent, ScanRun
from drift.persistence.store import (
    IllegalTransition,
    SqlAlchemyIssueStore,
)


def _finding(href="./missing.md", line=5):
    return Finding(
        check_id="dead_link",
        identity=("README.md", href, "0"),
        doc_location=Location("README.md", line, line),
        code_anchor=None,
        summary=f"broken {href}",
        evidence=Evidence(doc_claim=href, code_truth="target not found"),
        confidence=Confidence.HIGH,
    )


def _new_run(session, repo="/tmp/repo"):
    run = ScanRun(repo=repo, commit_sha=None, status="running")
    session.add(run)
    session.flush()
    return run


def test_reconcile_inserts_discovered(db_session):
    run = _new_run(db_session)
    store = SqlAlchemyIssueStore(db_session)
    result = store.reconcile(run.id, [_finding()])
    assert result.discovered == 1
    issue = db_session.query(Issue).one()
    assert issue.status == IssueStatus.DISCOVERED
    assert issue.first_seen_run_id == run.id


def test_reconcile_second_run_same_finding_is_seen_not_duplicated(db_session):
    store = SqlAlchemyIssueStore(db_session)
    run1 = _new_run(db_session)
    store.reconcile(run1.id, [_finding()])
    run2 = _new_run(db_session)
    result = store.reconcile(run2.id, [_finding()])
    assert result.discovered == 0 and result.seen == 1
    issue = db_session.query(Issue).one()  # still exactly one
    assert issue.last_seen_run_id == run2.id


def test_reconcile_same_link_seen_across_runs_despite_line_shift(db_session):
    """The churn guard at the layer that matters: editing whitespace/newlines
    ABOVE a dead link shifts its line span, but it is the SAME issue — must be
    SEEN, never auto-RESOLVED-then-re-DISCOVERED (which would break PR linkage)."""
    store = SqlAlchemyIssueStore(db_session)
    run1 = _new_run(db_session)
    store.reconcile(run1.id, [_finding(line=5)])  # link originally at line 5
    run2 = _new_run(db_session)
    result = store.reconcile(run2.id, [_finding(line=50)])  # newlines added above → shifted
    assert result.discovered == 0 and result.seen == 1 and result.resolved == 0
    assert db_session.query(Issue).count() == 1  # one stable issue, not flicker


def test_reconcile_vanished_finding_auto_resolves(db_session):
    store = SqlAlchemyIssueStore(db_session)
    run1 = _new_run(db_session)
    store.reconcile(run1.id, [_finding()])
    run2 = _new_run(db_session)
    result = store.reconcile(run2.id, [])  # finding gone → fixed
    assert result.resolved == 1
    assert db_session.query(Issue).one().status == IssueStatus.RESOLVED


def test_reconcile_is_idempotent_on_retry(db_session):
    """Re-running the SAME run_id with the SAME findings creates no duplicate."""
    store = SqlAlchemyIssueStore(db_session)
    run = _new_run(db_session)
    store.reconcile(run.id, [_finding()])
    store.reconcile(run.id, [_finding()])
    assert db_session.query(Issue).count() == 1


def test_illegal_transition_rejected(db_session):
    store = SqlAlchemyIssueStore(db_session)
    run = _new_run(db_session)
    store.reconcile(run.id, [_finding()])
    issue = db_session.query(Issue).one()
    with pytest.raises(IllegalTransition):
        store.transition(issue.id, IssueStatus.MERGED, trigger="test")  # DISCOVERED→MERGED illegal


def test_transition_records_evidence(db_session):
    store = SqlAlchemyIssueStore(db_session)
    run = _new_run(db_session)
    store.reconcile(run.id, [_finding()])
    issue = db_session.query(Issue).one()
    store.transition(issue.id, IssueStatus.REVIEWING, trigger="manual", evidence="PR #42")
    event = (
        db_session.query(IssueEvent)
        .filter_by(issue_id=issue.id, to_status=IssueStatus.REVIEWING)
        .one()
    )
    assert event.evidence == "PR #42"
