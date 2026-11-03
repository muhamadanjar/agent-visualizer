"""Persist an approved execution tool scope on Task revisions."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "orchestration_0023"
down_revision = "orchestration_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_revisions",
        sa.Column("tool_scope", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "task_revisions_tool_scope_check",
        "task_revisions",
        "tool_scope IS NULL OR (cardinality(tool_scope) <= 3 AND tool_scope <@ ARRAY['list_files', 'read_file', 'write_file']::text[])",
    )


def downgrade() -> None:
    op.drop_constraint("task_revisions_tool_scope_check", "task_revisions", type_="check")
    op.drop_column("task_revisions", "tool_scope")
