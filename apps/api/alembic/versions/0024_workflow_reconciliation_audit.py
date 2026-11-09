"""Record Owner decisions before abandoning corrupt workflows."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "orchestration_0024"
down_revision = "orchestration_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_reconciliation_audits",
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("parent_run_id", sa.UUID(), nullable=False),
        sa.Column("actor_owner_id", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("checkpoint_version", sa.Integer(), nullable=True),
        sa.Column("checkpoint_snapshot", JSONB(), nullable=True),
        sa.Column("delegations_snapshot", JSONB(), nullable=False),
        sa.Column("event_cursor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("owner_id", "parent_run_id"),
        sa.ForeignKeyConstraint(
            ["owner_id", "parent_run_id"],
            ["runs.owner_id", "runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "event_cursor"],
            ["activity_events.owner_id", "activity_events.cursor"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("char_length(owner_id) BETWEEN 1 AND 128"),
        sa.CheckConstraint("actor_owner_id = owner_id"),
        sa.CheckConstraint("outcome = 'abandoned_unknown'"),
        sa.CheckConstraint("checkpoint_version IS NULL OR checkpoint_version >= 1"),
        sa.CheckConstraint("jsonb_typeof(delegations_snapshot) = 'array'"),
        sa.CheckConstraint("event_cursor >= 1"),
    )


def downgrade() -> None:
    raise RuntimeError("Workflow reconciliation audit records are append-only")
