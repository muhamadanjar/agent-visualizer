"""Raise the per-Run inference-turn ceiling for bounded workspace policies.

Revision ID: execution_0016
Revises: events_0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "execution_0016"
down_revision = "events_0015"
branch_labels = None
depends_on = None

_TABLE = "project_execution_policies"
_CONSTRAINT = "project_execution_policies_max_inference_turns_check"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "max_inference_turns BETWEEN 1 AND 24",
    )


def downgrade() -> None:
    has_extended_limit = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM project_execution_policies "
            "WHERE max_inference_turns > 12)"
        )
    )
    if has_extended_limit:
        raise RuntimeError(
            "Cannot lower the workspace inference-turn ceiling while policies exceed 12."
        )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "max_inference_turns BETWEEN 1 AND 12",
    )
