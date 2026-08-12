"""cell_terminal_status — the (run_id, cell_key) redelivery guard

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cell_terminal_status",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("scan_run.id"), nullable=False),
        sa.Column("producer", sa.String, nullable=False),
        sa.Column("unit_ref", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("claims_emitted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "producer", "unit_ref", name="uq_cell_terminal_run_cell"),
    )


def downgrade() -> None:
    op.drop_table("cell_terminal_status")
