"""The issue store: findings become issues, and issues move through a legal state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from drift.domain.findings import Finding, IssueStatus
from drift.persistence.models import Issue, IssueEvent, ScanRun

# The whole state machine. Nothing that produces findings knows an issue has states at all.
LEGAL_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.DISCOVERED: {IssueStatus.REVIEWING, IssueStatus.RESOLVED},
    IssueStatus.REVIEWING: {IssueStatus.SUBMITTED, IssueStatus.REJECTED, IssueStatus.RESOLVED},
    IssueStatus.SUBMITTED: {IssueStatus.MERGED, IssueStatus.REJECTED, IssueStatus.RESOLVED},
    IssueStatus.MERGED: set(),
    IssueStatus.REJECTED: set(),
    IssueStatus.RESOLVED: set(),
}
_TERMINAL = {IssueStatus.MERGED, IssueStatus.REJECTED, IssueStatus.RESOLVED}


class IllegalTransition(Exception):
    """Raised when a requested state transition is not permitted by the legal-transition table."""


@dataclass(frozen=True)
class ReconcileResult:
    """Summary counts from a single reconcile pass: discovered, resolved, and seen issue tallies."""

    discovered: int
    resolved: int
    seen: int


class IssueStore(Protocol):
    """Storage seam: bridges pure findings to the stateful issues kept across runs."""

    def reconcile(self, run_id: int, findings: list[Finding]) -> ReconcileResult: ...
    def transition(
        self, issue_id: int, to: IssueStatus, trigger: str, evidence: str | None = None
    ) -> Issue: ...
    def open_issues(self, repo: str) -> list[Issue]: ...


class SqlAlchemyIssueStore:
    """SQLAlchemy-backed issue store: persists and reconciles issues against a Postgres session."""

    def __init__(self, session: Session) -> None:
        """Hold the caller's session; this store never opens or closes one of its own."""
        self._session = session

    def reconcile(self, run_id: int, findings: list[Finding]) -> ReconcileResult:
        """Fold one run's findings into the stored issues, closing any that no longer appear.

        Absence closes an issue here. That is only safe for a producer that enumerates
        exhaustively; anything that may simply have failed to look should use
        `reconcile_with_replay`.

        Raises:
            ValueError: If `run_id` names no stored run.
        """
        run = self._session.get(ScanRun, run_id)
        if run is None:
            raise ValueError(f"ScanRun {run_id} not found")
        repo = run.repo
        existing = {i.dedup_key: i for i in self.open_issues(repo)}
        seen_keys = set()
        discovered = seen = 0

        for finding in findings:
            key = finding.dedup_key
            if key in seen_keys:
                continue  # same finding twice in one run -> ignore the dup
            seen_keys.add(key)
            issue = existing.get(key)
            if issue is None:
                self._session.add(self._to_issue(repo, run_id, finding))
                discovered += 1
            else:
                issue.last_seen_run_id = run_id
                seen += 1

        resolved = 0
        for key, issue in existing.items():
            if key not in seen_keys:  # was open, absent now -> fixed
                self._do_transition(issue, IssueStatus.RESOLVED, trigger="reconcile")
                resolved += 1

        self._session.flush()
        return ReconcileResult(discovered=discovered, resolved=resolved, seen=seen)

    def reconcile_with_replay(
        self,
        run_id: int,
        findings: list[Finding],
        still_drifting: Callable[[dict], bool],
    ) -> ReconcileResult:
        """Reconcile where closure is decided by replaying an issue's own stored check.

        A producer that finds nothing this run can never, by itself, close an issue: an issue is
        resolved only when the check stored with it replays clean. That is what stops a finding
        flapping open and shut as a nondeterministic producer happens to notice it or not.

        Args:
            still_drifting: Replays one issue's stored check and says whether it still fails.
                An issue stored without a check is never closed here.

        Raises:
            ValueError: If `run_id` names no stored run.
        """
        run = self._session.get(ScanRun, run_id)
        if run is None:
            raise ValueError(f"ScanRun {run_id} not found")
        repo = run.repo
        existing = {i.dedup_key: i for i in self.open_issues(repo)}
        seen_keys = set()
        discovered = seen = 0

        for finding in findings:
            key = finding.dedup_key
            if key in seen_keys:
                continue  # same finding twice in one run -> ignore the dup
            seen_keys.add(key)
            issue = existing.get(key)
            if issue is None:
                new_issue = self._to_issue(repo, run_id, finding)
                new_issue.payload = {**new_issue.payload, "check": finding.check}
                self._session.add(new_issue)
                discovered += 1
            else:
                issue.last_seen_run_id = run_id
                seen += 1

        resolved = 0
        for key, issue in existing.items():
            if key in seen_keys:
                continue  # seen this run -> producer confirms it, no replay needed
            check = issue.payload.get("check")
            if check is None:
                continue  # legacy issue, no stored check -> never auto-resolved by replay
            if not still_drifting(check):
                self._do_transition(issue, IssueStatus.RESOLVED, trigger="reconcile_replay")
                resolved += 1
            # else: check still replays as drifting -> stays open, producer absence ignored

        self._session.flush()
        return ReconcileResult(discovered=discovered, resolved=resolved, seen=seen)

    def transition(
        self, issue_id: int, to: IssueStatus, trigger: str, evidence: str | None = None
    ) -> Issue:
        """Move one issue to a new status, recording the transition as an event.

        Raises:
            IllegalTransition: If the move is not in `LEGAL_TRANSITIONS` for the current status.
        """
        issue = self._session.get(Issue, issue_id)
        self._do_transition(issue, to, trigger, evidence)
        self._session.flush()
        return issue

    def open_issues(self, repo: str) -> list[Issue]:
        """Every issue for `repo` still in play — that is, not yet in a terminal status.

        Terminal is a property of the transition table: a status with no legal move out of it,
        which today means merged, rejected or resolved.
        """
        stmt = select(Issue).where(Issue.repo == repo, Issue.status.notin_(list(_TERMINAL)))
        return list(self._session.scalars(stmt))

    def _to_issue(self, repo: str, run_id: int, finding: Finding) -> Issue:
        """Build a freshly discovered issue from a finding."""
        return Issue(
            repo=repo,
            dedup_key=finding.dedup_key,
            check_id=finding.check_id,
            status=IssueStatus.DISCOVERED,
            confidence=finding.confidence,
            payload={
                "summary": finding.summary,
                "doc_location": {
                    "file": finding.doc_location.file,
                    "start_line": finding.doc_location.start_line,
                    "end_line": finding.doc_location.end_line,
                },
                "evidence": {
                    "doc_claim": finding.evidence.doc_claim,
                    "code_truth": finding.evidence.code_truth,
                },
            },
            first_seen_run_id=run_id,
            last_seen_run_id=run_id,
        )

    def _do_transition(
        self, issue: Issue, to: IssueStatus, trigger: str, evidence: str | None = None
    ) -> None:
        """Apply a transition after checking it against the table, and journal it as an event.

        Raises:
            IllegalTransition: If the table does not permit the move from the current status.
        """
        current = IssueStatus(issue.status)
        if to not in LEGAL_TRANSITIONS[current]:
            raise IllegalTransition(f"{current} → {to} is not a legal transition")
        self._session.add(
            IssueEvent(
                issue_id=issue.id,
                from_status=current,
                to_status=to,
                trigger=trigger,
                evidence=evidence,
            )
        )
        issue.status = to
