"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create scan_run, issue (with UNIQUE + index), and issue_event tables."""
    op.create_table(
        "scan_run",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("commit_sha", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "issue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("dedup_key", sa.String, nullable=False),
        sa.Column("check_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("confidence", sa.String, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("first_seen_run_id", sa.Integer, sa.ForeignKey("scan_run.id"), nullable=True),
        sa.Column("last_seen_run_id", sa.Integer, sa.ForeignKey("scan_run.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repo", "dedup_key", name="uq_issue_repo_dedup_key"),
    )
    op.create_index("ix_issue_repo_status", "issue", ["repo", "status"])
    op.create_table(
        "issue_event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("issue_id", sa.Integer, sa.ForeignKey("issue.id"), nullable=False),
        sa.Column("from_status", sa.String, nullable=True),
        sa.Column("to_status", sa.String, nullable=False),
        sa.Column("trigger", sa.String, nullable=False),
        sa.Column("evidence", sa.String, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Drop all three tables in reverse dependency order."""
    op.drop_table("issue_event")
    op.drop_index("ix_issue_repo_status", table_name="issue")
    op.drop_table("issue")
    op.drop_table("scan_run")
