"""Allow unknown terminal Run states in Activity projections.

Revision ID: events_0015
Revises: events_0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "events_0015"
down_revision = "events_0014"
branch_labels = None
depends_on = None

_TABLE = "run_activity_projections"
_CONSTRAINT = "run_activity_projections_status_check"
_STATUS_CHECK = (
    "status IN ('running', 'waiting_input', 'waiting_approval', 'blocked', "
    "'succeeded', 'failed', 'canceled', 'unknown')"
)
_PREVIOUS_STATUS_CHECK = (
    "status IN ('running', 'waiting_input', 'waiting_approval', 'blocked', "
    "'succeeded', 'failed', 'canceled')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _STATUS_CHECK)


def downgrade() -> None:
    has_unknown = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM run_activity_projections WHERE status = 'unknown')")
    )
    if has_unknown:
        raise RuntimeError(
            "Cannot remove unknown Run projections during downgrade; resolve or preserve "
            "those records before downgrading."
        )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _PREVIOUS_STATUS_CHECK)
