"""Persist immutable output schemas on Task revisions."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "orchestration_0022"
down_revision = "orchestration_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_revisions", sa.Column("output_schema", JSONB(), nullable=True))
    op.create_check_constraint(
        "task_revisions_output_schema_object_check",
        "task_revisions",
        "output_schema IS NULL OR jsonb_typeof(output_schema) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("task_revisions_output_schema_object_check", "task_revisions", type_="check")
    op.drop_column("task_revisions", "output_schema")
