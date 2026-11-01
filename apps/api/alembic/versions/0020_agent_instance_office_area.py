"""Add visual Office Area assignments to Agent Instances.

Revision ID: office_area_0020
Revises: activity_0019
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "office_area_0020"
down_revision = "activity_0019"
branch_labels = None
depends_on = None



def upgrade() -> None:
    op.add_column("agent_instances", sa.Column("office_area", sa.Text(), nullable=True))
    op.execute(
        """
        WITH ranked AS (
            SELECT id, (row_number() OVER (PARTITION BY owner_id ORDER BY created_at, id) - 1) % 6 AS area_index
            FROM agent_instances
            WHERE archived_at IS NULL
        )
        UPDATE agent_instances AS instance
        SET office_area = CASE ranked.area_index
            WHEN 0 THEN 'architecture'
            WHEN 1 THEN 'engineering'
            WHEN 2 THEN 'review'
            WHEN 3 THEN 'security'
            WHEN 4 THEN 'meeting'
            ELSE 'lounge'
        END
        FROM ranked
        WHERE instance.id = ranked.id
        """
    )
    op.execute(
        "UPDATE agent_instances SET office_area = 'engineering' WHERE archived_at IS NOT NULL"
    )
    op.alter_column("agent_instances", "office_area", nullable=False)
    op.create_check_constraint(
        "agent_instances_office_area_check",
        "agent_instances",
        "office_area IN ('architecture', 'engineering', 'review', 'security', 'meeting', 'lounge')",
    )


def downgrade() -> None:
    op.drop_constraint("agent_instances_office_area_check", "agent_instances", type_="check")
    op.drop_column("agent_instances", "office_area")
