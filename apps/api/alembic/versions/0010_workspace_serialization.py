"""Serialize active Runs sharing a workspace directory.

Revision ID: execution_0010
Revises: execution_0009
"""

from __future__ import annotations

from alembic import op


revision = "execution_0010"
down_revision = "execution_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE run_dispatches ADD COLUMN workspace_path text")
    op.execute(
        """
        UPDATE run_dispatches AS dispatch
        SET workspace_path = policy.workspace_path
        FROM project_execution_policies AS policy
        WHERE policy.owner_id = dispatch.owner_id
          AND policy.project_id = dispatch.project_id
        """
    )
    op.execute(
        "ALTER TABLE run_dispatches ALTER COLUMN workspace_path SET NOT NULL"
    )
    # Preserve only the most recently renewed lease for each shared workspace.
    # Other active attempts are uncertain and require owner-visible recovery.
    op.execute(
        """
        DO $$
        DECLARE
            lease_row record;
            event_cursor bigint;
            event_at timestamptz := now();
            migration_source constant text := 'migration-execution-0010';
        BEGIN
            FOR lease_row IN
                SELECT dispatch.owner_id, dispatch.run_id, dispatch.task_id,
                       dispatch.project_id, dispatch.instance_id, run.task_revision,
                       dispatch.workspace_path,
                       row_number() OVER (
                           PARTITION BY dispatch.owner_id, dispatch.workspace_path
                           ORDER BY dispatch.lease_expires_at DESC NULLS LAST,
                                    dispatch.updated_at DESC, dispatch.run_id
                       ) AS workspace_rank
                FROM run_dispatches AS dispatch
                JOIN runs AS run
                  ON run.owner_id = dispatch.owner_id AND run.id = dispatch.run_id
                WHERE dispatch.state = 'leased'
                ORDER BY dispatch.owner_id, dispatch.workspace_path, dispatch.run_id
            LOOP
                IF lease_row.workspace_rank > 1 THEN
                    UPDATE run_dispatches
                    SET state = 'unknown', lease_expires_at = NULL, updated_at = event_at
                    WHERE owner_id = lease_row.owner_id AND run_id = lease_row.run_id;

                    INSERT INTO owner_event_cursors (owner_id, cursor)
                    VALUES (lease_row.owner_id, 1)
                    ON CONFLICT (owner_id) DO UPDATE
                    SET cursor = owner_event_cursors.cursor + 1
                    RETURNING cursor INTO event_cursor;

                    INSERT INTO activity_events (
                        owner_id, cursor, source_id, event_id, schema_version,
                        source_session_id, workspace_id, project_id, task_id,
                        task_revision, agent_instance_id, run_id, event_type,
                        occurred_at, received_at, source_sequence, action_summary
                    ) VALUES (
                        lease_row.owner_id, event_cursor, migration_source,
                        gen_random_uuid(), 1, NULL, NULL, lease_row.project_id,
                        lease_row.task_id, lease_row.task_revision,
                        lease_row.instance_id, lease_row.run_id, 'run.blocked',
                        event_at, event_at, NULL, NULL
                    );

                    INSERT INTO run_activity_projections (
                        owner_id, run_id, task_id, task_revision, project_id,
                        source_id, agent_instance_id, status, last_event_cursor,
                        last_event_type, last_event_at, last_sequence,
                        last_heartbeat_at, last_action_summary, updated_at
                    ) VALUES (
                        lease_row.owner_id, lease_row.run_id, lease_row.task_id,
                        lease_row.task_revision, lease_row.project_id,
                        migration_source, lease_row.instance_id, 'blocked',
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
                END IF;
            END LOOP;
        END;
        $$
        """
    )
    op.execute("DROP INDEX run_dispatches_active_instance_idx")
    op.execute(
        "CREATE UNIQUE INDEX run_dispatches_active_workspace_idx "
        "ON run_dispatches (owner_id, workspace_path) WHERE state = 'leased'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX run_dispatches_active_workspace_idx")
    op.execute(
        "CREATE UNIQUE INDEX run_dispatches_active_instance_idx "
        "ON run_dispatches (owner_id, instance_id) WHERE state = 'leased'"
    )
    op.execute("ALTER TABLE run_dispatches DROP COLUMN workspace_path")
