"""Add explicit Codex gateway binding and independent runner readiness.

Revision ID: gateway_0018
Revises: events_0017
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "gateway_0018"
down_revision = "events_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("model_endpoints_credential_env_check", "model_endpoints", type_="check")
    op.create_check_constraint(
        "model_endpoints_credential_env_check", "model_endpoints",
        "credential_env = 'NINEROUTER_KEY' OR "
        "(credential_env = 'OMP_GATEWAY_KEY' AND endpoint_url = 'http://localhost:14000/v1' "
        "AND default_model_id ~ '^openai-codex/[A-Za-z0-9][A-Za-z0-9._+-]*$' "
        "AND default_route IS NULL)",
    )
    op.execute(
        "ALTER TABLE runner_connections "
        "ADD COLUMN IF NOT EXISTS gateway_ready boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM model_endpoints WHERE credential_env = 'OMP_GATEWAY_KEY')"
    )):
        raise RuntimeError("Cannot remove gateway support while gateway endpoints exist.")
    op.drop_column("runner_connections", "gateway_ready")
    op.drop_constraint("model_endpoints_credential_env_check", "model_endpoints", type_="check")
    op.create_check_constraint(
        "model_endpoints_credential_env_check", "model_endpoints", "credential_env = 'NINEROUTER_KEY'",
    )
