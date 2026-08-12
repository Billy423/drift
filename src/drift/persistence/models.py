"""The ORM models: scan runs, the issue lifecycle, the cell redelivery guard, the journal."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from drift.domain.findings import Confidence, IssueStatus
from drift.persistence.db import Base


class ScanRun(Base):
    """One scan of one repository, from queued through running to done or failed."""

    __tablename__ = "scan_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # queued/running/done/failed
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Issue(Base):
    """A deduplicated finding persisted across runs — the stateful counterpart to `Finding`."""

    __tablename__ = "issue"
    __table_args__ = (
        UniqueConstraint("repo", "dedup_key", name="uq_issue_repo_dedup_key"),
        Index("ix_issue_repo_status", "repo", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)
    check_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[IssueStatus] = mapped_column(String, nullable=False)
    confidence: Mapped[Confidence] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_run.id"), nullable=True)
    last_seen_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_run.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list["IssueEvent"]] = relationship(back_populates="issue")


class IssueEvent(Base):
    """Immutable audit record of every status transition on an Issue."""

    __tablename__ = "issue_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issue.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issue: Mapped[Issue] = relationship(back_populates="events")


class CellTerminalStatus(Base):
    """One cell's terminal outcome, keyed by run and cell: the broker-redelivery guard.

    A redelivered cell finds its own row and returns the stored outcome instead of paying twice.
    The lookup is check-then-act, so the guarantee holds only while dispatch stays serial.
    """

    __tablename__ = "cell_terminal_status"
    __table_args__ = (
        UniqueConstraint("run_id", "producer", "unit_ref", name="uq_cell_terminal_run_cell"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_run.id"), nullable=False)
    producer: Mapped[str] = mapped_column(String, nullable=False)
    unit_ref: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # CELL_RESULT_STATUSES
    claims_emitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalRecord(Base):
    """One journaled observation: run-scoped, version-stamped, inserted and never updated."""

    __tablename__ = "journal_record"
    __table_args__ = (Index("ix_journal_run_type", "run_id", "record_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scan_run.id"), nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    component: Mapped[str] = mapped_column(String, nullable=False)
    record_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    agent_ver: Mapped[str] = mapped_column(String, nullable=False)
    judge_ver: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
