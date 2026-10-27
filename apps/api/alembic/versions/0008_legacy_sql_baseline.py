"""Mark the schema after the immutable raw SQL migrations 0001–0008.

Revision ID: legacy_0008
Revises: None
"""

from __future__ import annotations


revision = "legacy_0008"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Adopt the schema produced by the checksummed SQL migration runner."""
    # The custom migration runner applies migrations 0001–0008 first.
    pass


def downgrade() -> None:
    """Do not reverse immutable legacy schema from Alembic."""
    raise RuntimeError("legacy SQL migrations cannot be reversed through Alembic")
