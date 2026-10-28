"""Add a transactional PostgreSQL outbox for Redis event fan-out.

Revision ID: events_0014
Revises: runtime_0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "events_0014"
down_revision = "runtime_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_event_outbox",
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="activity_event_outbox_attempt_count_check"),
        sa.CheckConstraint(
            "(claim_id IS NULL AND lease_expires_at IS NULL) OR "
            "(claim_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="activity_event_outbox_claim_check",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "cursor"],
            ["activity_events.owner_id", "activity_events.cursor"],
            name="activity_event_outbox_event_fk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "cursor", name="activity_event_outbox_pkey"),
    )
    op.create_index(
        "activity_event_outbox_pending_idx",
        "activity_event_outbox",
        ["owner_id", "cursor"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("activity_event_outbox_pending_idx", table_name="activity_event_outbox")
    op.drop_table("activity_event_outbox")
