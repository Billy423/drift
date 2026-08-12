"""The journal's write path: one chokepoint, insert-only, every row stamped."""

from __future__ import annotations

from dataclasses import dataclass

from drift.persistence.models import JournalRecord


@dataclass(frozen=True)
class Stamps:
    """The version triple every journal record carries, so a row says what produced it."""

    agent_ver: str
    judge_ver: str
    model: str


class JournalWriter:
    """The single write path into the journal: insert-only, and no record escapes the stamps."""

    def __init__(self, session, run_id: int, repo: str, commit_sha: str, stamps: Stamps) -> None:
        self._session = session
        self._run_id = run_id
        self._repo = repo
        self._sha = commit_sha
        self._stamps = stamps

    def write(self, component: str, record_type: str, payload: dict) -> None:
        """Stage one journal row; nothing written here survives a crash until `flush` commits."""
        self._session.add(
            JournalRecord(
                run_id=self._run_id,
                repo=self._repo,
                commit_sha=self._sha,
                component=component,
                record_type=record_type,
                payload=payload,
                agent_ver=self._stamps.agent_ver,
                judge_ver=self._stamps.judge_ver,
                model=self._stamps.model,
            )
        )

    def flush(self) -> None:
        """Commit the rows written so far. Called once per document unit.

        The unit boundary is the crash boundary: rows a scan has already paid API calls for are
        on disk rather than waiting for the run to end. This commits the whole session, not only
        journal rows, so anything else pending on it is committed with them.
        """
        self._session.commit()

    def rollback(self) -> None:
        """Discard the in-flight transaction so the session can be written to again.

        A session whose flush raised fails every later write the same way, so a scan that means
        to continue past a journal failure must reset it or lose every remaining unit rather than
        the one that broke. Rows earlier flushes committed are untouched.
        """
        self._session.rollback()
