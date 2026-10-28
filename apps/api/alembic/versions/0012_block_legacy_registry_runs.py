"""Block queued Runs without an executable Agent Registry snapshot.

Revision ID: registry_0012
Revises: registry_0011
"""

from __future__ import annotations

from alembic import op


revision = "registry_0012"
down_revision = "registry_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older queued Runs have no immutable typed Registry configuration or Model
    # Endpoint snapshot. They cannot be safely dispatched by the Registry Runner.
    # Make the disposition visible instead of leaving them permanently queued.
    op.execute(
        """
        DO $$
        DECLARE
            legacy_run record;
            event_cursor bigint;
            event_at timestamptz := now();
            migration_source constant text := 'migration-agent-registry-0012';
        BEGIN
            FOR legacy_run IN
                SELECT dispatch.owner_id, dispatch.run_id, dispatch.task_id,
                       dispatch.project_id, dispatch.instance_id, run.task_revision
                FROM run_dispatches AS dispatch
                JOIN runs AS run
                  ON run.owner_id = dispatch.owner_id AND run.id = dispatch.run_id
                CROSS JOIN LATERAL (
                    SELECT
                        run.configuration_snapshot #> '{agent_definition,configuration}' AS configuration,
                        run.configuration_snapshot -> 'model_endpoint' AS model_endpoint,
                        run.configuration_snapshot -> 'runtime' AS runtime
                ) AS registry_snapshot
                WHERE dispatch.state = 'queued'
                  AND NOT COALESCE((
                      jsonb_typeof(registry_snapshot.configuration) = 'object'
                      AND jsonb_typeof(registry_snapshot.configuration -> 'role') = 'string'
                      AND jsonb_typeof(registry_snapshot.configuration -> 'model_id') = 'string'
                      AND jsonb_typeof(
                          registry_snapshot.configuration -> 'model_endpoint_id'
                      ) = 'string'
                      AND registry_snapshot.configuration ->> 'model_endpoint_id'
                          ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                      AND jsonb_typeof(registry_snapshot.model_endpoint -> 'id') = 'string'
                      AND registry_snapshot.model_endpoint ->> 'id'
                          ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                      AND length(btrim(registry_snapshot.configuration ->> 'role')) BETWEEN 1 AND 120
                      AND length(btrim(registry_snapshot.configuration ->> 'model_id')) BETWEEN 1 AND 200
                      AND registry_snapshot.configuration ->> 'runtime_id' = 'langchain'
                      AND registry_snapshot.configuration ->> 'model_endpoint_id'
                          = registry_snapshot.model_endpoint ->> 'id'
                      AND (
                          CASE jsonb_typeof(registry_snapshot.configuration -> 'route')
                              WHEN 'null' THEN true
                              WHEN 'string' THEN length(
                                  btrim(registry_snapshot.configuration ->> 'route')
                              ) BETWEEN 1 AND 120
                              ELSE false
                          END
                      )
                      AND (
                          CASE jsonb_typeof(registry_snapshot.configuration -> 'temperature')
                              WHEN 'null' THEN true
                              WHEN 'number' THEN
                                  (registry_snapshot.configuration ->> 'temperature')::numeric
                                  BETWEEN 0 AND 2
                              ELSE false
                          END
                      )
                      AND (
                          CASE
                              WHEN jsonb_typeof(
                                  registry_snapshot.configuration -> 'max_iterations'
                              ) = 'number'
                                  AND registry_snapshot.configuration ->> 'max_iterations'
                                      ~ '^[0-9]{1,3}$'
                              THEN (registry_snapshot.configuration ->> 'max_iterations')::integer
                                   BETWEEN 1 AND 100
                              ELSE false
                          END
                      )
                      AND (
                          CASE
                              WHEN jsonb_typeof(
                                  registry_snapshot.configuration -> 'concurrency_limit'
                              ) = 'number'
                                  AND registry_snapshot.configuration ->> 'concurrency_limit'
                                      ~ '^[0-9]{1,2}$'
                              THEN (registry_snapshot.configuration ->> 'concurrency_limit')::integer
                                   BETWEEN 1 AND 20
                              ELSE false
                          END
                      )
                      AND (
                          CASE
                              WHEN jsonb_typeof(
                                  registry_snapshot.configuration -> 'execution_timeout_seconds'
                              ) = 'number'
                                  AND registry_snapshot.configuration
                                      ->> 'execution_timeout_seconds' ~ '^[0-9]{1,5}$'
                              THEN (registry_snapshot.configuration
                                    ->> 'execution_timeout_seconds')::integer
                                   BETWEEN 1 AND 86400
                              ELSE false
                          END
                      )
                      AND (
                          CASE
                              WHEN jsonb_typeof(
                                  registry_snapshot.configuration -> 'allowed_tools'
                              ) = 'array'
                              THEN jsonb_array_length(
                                  registry_snapshot.configuration -> 'allowed_tools'
                              ) <= 3
                              ELSE false
                          END
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              CASE
                                  WHEN jsonb_typeof(
                                      registry_snapshot.configuration -> 'allowed_tools'
                                  ) = 'array'
                                  THEN registry_snapshot.configuration -> 'allowed_tools'
                                  ELSE '[]'::jsonb
                              END
                          ) AS configured_tool(value)
                          WHERE jsonb_typeof(configured_tool.value) IS DISTINCT FROM 'string'
                             OR configured_tool.value #>> '{}'
                                NOT IN ('list_files', 'read_file', 'write_file')
                      )
                      AND jsonb_typeof(registry_snapshot.model_endpoint) = 'object'
                      AND registry_snapshot.model_endpoint ->> 'adapter'
                          = 'openai_compatible_chat_completions'
                      AND jsonb_typeof(
                          registry_snapshot.model_endpoint -> 'endpoint_url'
                      ) = 'string'
                      AND length(btrim(
                          registry_snapshot.model_endpoint ->> 'endpoint_url'
                      )) BETWEEN 1 AND 2048
                      AND registry_snapshot.model_endpoint ->> 'credential_env'
                          = 'NINEROUTER_KEY'
                      AND jsonb_typeof(registry_snapshot.runtime) = 'object'
                      AND registry_snapshot.runtime ->> 'id' = 'langchain'
                  ), false)
                ORDER BY dispatch.owner_id, dispatch.created_at, dispatch.run_id
            LOOP
                UPDATE run_dispatches
                SET state = 'blocked', updated_at = event_at
                WHERE owner_id = legacy_run.owner_id AND run_id = legacy_run.run_id;

                INSERT INTO owner_event_cursors (owner_id, cursor)
                VALUES (legacy_run.owner_id, 1)
                ON CONFLICT (owner_id) DO UPDATE
                SET cursor = owner_event_cursors.cursor + 1
                RETURNING cursor INTO event_cursor;

                INSERT INTO activity_events (
                    owner_id, cursor, source_id, event_id, schema_version,
                    source_session_id, workspace_id, project_id, task_id,
                    task_revision, agent_instance_id, run_id, event_type,
                    occurred_at, received_at, source_sequence, action_summary
                ) VALUES (
                    legacy_run.owner_id, event_cursor, migration_source,
                    gen_random_uuid(), 1, NULL, NULL, legacy_run.project_id,
                    legacy_run.task_id, legacy_run.task_revision,
                    legacy_run.instance_id, legacy_run.run_id, 'run.blocked',
                    event_at, event_at, NULL, NULL
                );

                INSERT INTO run_activity_projections (
                    owner_id, run_id, task_id, task_revision, project_id,
                    source_id, agent_instance_id, status, last_event_cursor,
                    last_event_type, last_event_at, last_sequence,
                    last_heartbeat_at, last_action_summary, updated_at
                ) VALUES (
                    legacy_run.owner_id, legacy_run.run_id, legacy_run.task_id,
                    legacy_run.task_revision, legacy_run.project_id,
                    migration_source, legacy_run.instance_id, 'blocked',
                    event_cursor, 'run.blocked', event_at, NULL, NULL, NULL,
                    event_at
                )
                ON CONFLICT (owner_id, run_id) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    status = 'blocked',
                    last_event_cursor = EXCLUDED.last_event_cursor,
                    last_event_type = EXCLUDED.last_event_type,
                    last_event_at = EXCLUDED.last_event_at,
                    last_sequence = NULL,
                    last_action_summary = NULL,
                    updated_at = EXCLUDED.updated_at;
            END LOOP;
        END;
        $$
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Agent Registry Run compatibility migration changes dispatch state and emits "
        "owner-visible events; restore a database backup before manual rollback."
    )
