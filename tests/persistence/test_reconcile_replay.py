"""Tests for SqlAlchemyIssueStore.reconcile_with_replay — the flap-proof resolution leg.

An issue closes by replaying its own stored check, never by a producer failing to re-emit the
claim, so a scan that simply did not look at a document cannot resolve what it found before.
"""

from drift.domain.findings import (
    Confidence,
    Evidence,
    Finding,
    IssueStatus,
    Location,
)
from drift.persistence.models import Issue, IssueEvent, ScanRun
from drift.persistence.store import SqlAlchemyIssueStore


def _finding(href="./missing.md", line=5, check=None):
    return Finding(
        check_id="dead_link",
        identity=("README.md", href, "0"),
        doc_location=Location("README.md", line, line),
        code_anchor=None,
        summary=f"broken {href}",
        evidence=Evidence(doc_claim=href, code_truth="target not found"),
        confidence=Confidence.HIGH,
        check=check,
    )


def _new_run(session, repo="/tmp/repo"):
    run = ScanRun(repo=repo, commit_sha=None, status="running")
    session.add(run)
    session.flush()
    return run


class _RecordingReplay:
    """A still_drifting fake that records every check it was called with (spy)."""

    def __init__(self, result: bool):
        self._result = result
        self.calls: list[dict] = []

    def __call__(self, check: dict) -> bool:
        self.calls.append(check)
        return self._result


def test_discovery_leg_carries_check_into_payload(db_session):
    run = _new_run(db_session)
    store = SqlAlchemyIssueStore(db_session)
    check = {"predicate": "dead_link", "path": "./missing.md"}
    result = store.reconcile_with_replay(
        run.id, [_finding(check=check)], still_drifting=lambda c: True
    )
    assert result.discovered == 1
    issue = db_session.query(Issue).one()
    assert issue.status == IssueStatus.DISCOVERED
    assert issue.payload["check"] == check


def test_resolution_leg_closes_when_replay_says_repaired(db_session):
    store = SqlAlchemyIssueStore(db_session)
    check = {"predicate": "dead_link", "path": "./missing.md"}
    run1 = _new_run(db_session)
    store.reconcile_with_replay(run1.id, [_finding(check=check)], still_drifting=lambda c: True)

    run2 = _new_run(db_session)
    replay = _RecordingReplay(result=False)  # repo repaired: replay says no longer drifting
    result = store.reconcile_with_replay(run2.id, [], still_drifting=replay)

    assert result.resolved == 1
    issue = db_session.query(Issue).one()
    assert issue.status == IssueStatus.RESOLVED
    event = (
        db_session.query(IssueEvent)
        .filter_by(issue_id=issue.id, to_status=IssueStatus.RESOLVED)
        .one()
    )
    assert event.trigger == "reconcile_replay"
    assert replay.calls == [check]


def test_guardian_scout_absence_alone_never_closes_an_issue(db_session):
    """THE invariant: an empty producer output must NOT resolve an issue whose stored
    check still replays as drifting — contrasted against the OLD absence-driven `reconcile`,
    which (correctly, for its own contract) DOES close on absence."""
    store = SqlAlchemyIssueStore(db_session)
    check = {"predicate": "dead_link", "path": "./missing.md"}

    # -- replay-gated issue: survives scout silence because still_drifting says yes --
    run1 = _new_run(db_session, repo="/tmp/repo-a")
    store.reconcile_with_replay(run1.id, [_finding(check=check)], still_drifting=lambda c: True)
    run2 = _new_run(db_session, repo="/tmp/repo-a")
    result = store.reconcile_with_replay(run2.id, [], still_drifting=lambda c: True)
    assert result.resolved == 0
    issue = db_session.query(Issue).filter_by(repo="/tmp/repo-a").one()
    assert issue.status == IssueStatus.DISCOVERED  # stayed open

    # -- contrast: the OLD absence-driven reconcile closes an equivalent issue on absence --
    run3 = _new_run(db_session, repo="/tmp/repo-b")
    store.reconcile(run3.id, [_finding(check=check)])
    run4 = _new_run(db_session, repo="/tmp/repo-b")
    old_result = store.reconcile(run4.id, [])
    assert old_result.resolved == 1
    old_issue = db_session.query(Issue).filter_by(repo="/tmp/repo-b").one()
    assert old_issue.status == IssueStatus.RESOLVED  # closed on absence alone


def test_legacy_checkless_issue_stays_open_and_replay_never_invoked(db_session):
    store = SqlAlchemyIssueStore(db_session)
    run1 = _new_run(db_session)
    store.reconcile(run1.id, [_finding()])  # old reconcile: payload has no "check" key

    run2 = _new_run(db_session)
    replay = _RecordingReplay(result=True)
    result = store.reconcile_with_replay(run2.id, [], still_drifting=replay)

    assert result.resolved == 0
    issue = db_session.query(Issue).one()
    assert issue.status == IssueStatus.DISCOVERED  # stayed open
    assert replay.calls == []  # never consulted for a checkless legacy issue


def test_seen_again_this_run_increments_seen_and_skips_replay(db_session):
    store = SqlAlchemyIssueStore(db_session)
    check = {"predicate": "dead_link", "path": "./missing.md"}
    run1 = _new_run(db_session)
    store.reconcile_with_replay(run1.id, [_finding(check=check)], still_drifting=lambda c: True)

    run2 = _new_run(db_session)
    replay = _RecordingReplay(result=False)
    result = store.reconcile_with_replay(run2.id, [_finding(check=check)], still_drifting=replay)

    assert result.seen == 1
    assert result.resolved == 0
    assert replay.calls == []  # seen this run → replay not invoked
    issue = db_session.query(Issue).one()
    assert issue.status == IssueStatus.DISCOVERED
    assert issue.last_seen_run_id == run2.id
