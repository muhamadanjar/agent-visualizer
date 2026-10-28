"""Add owner-scoped Model Endpoints and typed definition configuration.

Revision ID: registry_0011
Revises: execution_0010
"""

from __future__ import annotations

from alembic import op


revision = "registry_0011"
down_revision = "execution_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_definitions "
        "DROP CONSTRAINT IF EXISTS agent_definitions_owner_id_slug_key"
    )
    op.execute(
        "ALTER TABLE agent_definitions "
        "ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE agent_definition_versions "
        "ADD COLUMN IF NOT EXISTS configuration jsonb"
    )
    op.execute(
        """
        UPDATE agent_definitions AS definition
        SET enabled = false
        WHERE definition.enabled
          AND NOT EXISTS (
              SELECT 1
              FROM agent_definition_versions AS version
              WHERE version.definition_id = definition.id
                AND version.version = definition.current_version
                AND version.configuration IS NOT NULL
          )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_endpoints (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
            name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            adapter text NOT NULL CHECK (adapter = 'openai_compatible_chat_completions'),
            endpoint_url text NOT NULL CHECK (char_length(endpoint_url) BETWEEN 1 AND 2048),
            credential_env text NOT NULL CHECK (credential_env = 'NINEROUTER_KEY'),
            default_model_id text NOT NULL CHECK (char_length(default_model_id) BETWEEN 1 AND 200),
            default_route text CHECK (default_route IS NULL OR char_length(default_route) BETWEEN 1 AND 120),
            archived_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (owner_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS model_endpoints_owner_created_idx "
        "ON model_endpoints (owner_id, created_at, id)"
    )
    op.execute(
        """
        INSERT INTO model_endpoints (
            owner_id, name, adapter, endpoint_url, credential_env,
            default_model_id, default_route, created_at
        )
        SELECT provider.owner_id, 'Migrated endpoint', provider.adapter,
               provider.endpoint_url, 'NINEROUTER_KEY', provider.model_id,
               provider.route, provider.updated_at
        FROM provider_configs AS provider
        WHERE NOT EXISTS (
            SELECT 1
            FROM model_endpoints AS endpoint
            WHERE endpoint.owner_id = provider.owner_id
              AND endpoint.name = 'Migrated endpoint'
              AND endpoint.adapter = provider.adapter
              AND endpoint.endpoint_url = provider.endpoint_url
              AND endpoint.credential_env = 'NINEROUTER_KEY'
              AND endpoint.default_model_id = provider.model_id
              AND endpoint.default_route IS NOT DISTINCT FROM provider.route
              AND endpoint.created_at = provider.updated_at
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Agent Registry downgrade would discard Model Endpoints/configurations and may reimpose "
        "Definition name uniqueness that no longer holds; restore a database backup before "
        "manual rollback."
    )
