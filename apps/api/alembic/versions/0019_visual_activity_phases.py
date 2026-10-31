"""Allow normalized Activity Events for visual agent activity phases.

Revision ID: activity_0019
Revises: gateway_0018
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "activity_0019"
down_revision = "gateway_0018"
branch_labels = None
depends_on = None

_PHASE_EVENT_TYPES = "'run.working', 'run.tool_started', 'run.reviewing'"
_EVENT_TYPES = (
    "'run.started', 'run.working', 'run.tool_started', 'run.reviewing', "
    "'run.waiting_input', 'run.waiting_approval', 'run.blocked', 'run.tool_action', "
    "'run.cancellation_pending', 'run.succeeded', 'run.failed', 'run.canceled', "
    "'run.unknown', 'session.heartbeat', 'approval.requested', 'approval.approved', "
    "'approval.rejected'"
)
_PREVIOUS_EVENT_TYPES = (
    "'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked', "
    "'run.tool_action', 'run.cancellation_pending', 'run.succeeded', 'run.failed', "
    "'run.canceled', 'run.unknown', 'session.heartbeat', 'approval.requested', "
    "'approval.approved', 'approval.rejected'"
)


def upgrade() -> None:
    op.drop_constraint("activity_events_event_type_check", "activity_events", type_="check")
    op.create_check_constraint(
        "activity_events_event_type_check",
        "activity_events",
        f"event_type IN ({_EVENT_TYPES})",
    )


def downgrade() -> None:
    has_phase_events = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM activity_events "
            f"WHERE event_type IN ({_PHASE_EVENT_TYPES}))"
        )
    )
    if has_phase_events:
        raise RuntimeError("Cannot remove persisted visual activity phase events during downgrade.")
    op.drop_constraint("activity_events_event_type_check", "activity_events", type_="check")
    op.create_check_constraint(
        "activity_events_event_type_check",
        "activity_events",
        f"event_type IN ({_PREVIOUS_EVENT_TYPES})",
    )
