"""Persist approval transitions in the Activity Event ledger.

Revision ID: events_0017
Revises: execution_0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "events_0017"
down_revision = "execution_0016"
branch_labels = None
depends_on = None

_EVENT_TYPES = (
    "'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked', "
    "'run.tool_action', 'run.cancellation_pending', 'run.succeeded', 'run.failed', "
    "'run.canceled', 'run.unknown', 'session.heartbeat', 'approval.requested', "
    "'approval.approved', 'approval.rejected'"
)
_APPROVAL_TYPES = "'approval.requested', 'approval.approved', 'approval.rejected'"


def upgrade() -> None:
    op.add_column(
        "activity_events",
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint("activity_events_event_type_check", "activity_events", type_="check")
    op.create_check_constraint(
        "activity_events_event_type_check",
        "activity_events",
        f"event_type IN ({_EVENT_TYPES})",
    )
    op.create_check_constraint(
        "activity_events_approval_id_check",
        "activity_events",
        f"(event_type IN ({_APPROVAL_TYPES}) AND approval_id IS NOT NULL) OR "
        f"(event_type NOT IN ({_APPROVAL_TYPES}) AND approval_id IS NULL)",
    )
    op.create_foreign_key(
        "activity_events_approval_fk",
        "activity_events",
        "approval_requests",
        ["owner_id", "approval_id"],
        ["owner_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    has_approval_events = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM activity_events "
            "WHERE event_type IN ('approval.requested', 'approval.approved', 'approval.rejected'))"
        )
    )
    if has_approval_events:
        raise RuntimeError("Cannot remove persisted approval Activity Events during downgrade.")
    op.drop_constraint("activity_events_approval_fk", "activity_events", type_="foreignkey")
    op.drop_constraint("activity_events_approval_id_check", "activity_events", type_="check")
    op.drop_constraint("activity_events_event_type_check", "activity_events", type_="check")
    op.create_check_constraint(
        "activity_events_event_type_check",
        "activity_events",
        "event_type IN ('run.started', 'run.waiting_input', 'run.waiting_approval', "
        "'run.blocked', 'run.tool_action', 'run.cancellation_pending', "
        "'run.succeeded', 'run.failed', 'run.canceled', 'run.unknown', 'session.heartbeat')",
    )
    op.drop_column("activity_events", "approval_id")
