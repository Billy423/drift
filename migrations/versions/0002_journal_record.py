"""journal_record — the append-only record every published number is computed from

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_record",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("scan_run.id"), nullable=False),
        sa.Column("repo", sa.String, nullable=False),
        sa.Column("commit_sha", sa.String, nullable=False),
        sa.Column("component", sa.String, nullable=False),
        sa.Column("record_type", sa.String, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("agent_ver", sa.String, nullable=False),
        sa.Column("judge_ver", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_journal_run_type", "journal_record", ["run_id", "record_type"])


def downgrade() -> None:
    op.drop_index("ix_journal_run_type", table_name="journal_record")
    op.drop_table("journal_record")
