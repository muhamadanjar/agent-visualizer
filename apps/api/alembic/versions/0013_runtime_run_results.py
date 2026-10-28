"""Persist Run cancellation, lease limits, and owner-scoped results.

Revision ID: runtime_0013
Revises: registry_0012
"""

from __future__ import annotations

from alembic import op


revision = "runtime_0013"
down_revision = "registry_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE activity_events DROP CONSTRAINT activity_events_event_type_check"
    )
    op.execute(
        """
        ALTER TABLE activity_events ADD CONSTRAINT activity_events_event_type_check
        CHECK (event_type IN (
            'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked',
            'run.tool_action', 'run.cancellation_pending', 'run.succeeded',
            'run.failed', 'run.canceled', 'run.unknown', 'session.heartbeat'
        ))
        """
    )
    op.execute(
        "ALTER TABLE run_dispatches "
        "ADD COLUMN max_duration_seconds integer, "
        "ADD COLUMN cancellation_requested_at timestamptz"
    )
    op.execute(
        """
        UPDATE run_dispatches AS dispatch
        SET max_duration_seconds = policy.max_duration_seconds
        FROM project_execution_policies AS policy
        WHERE policy.owner_id = dispatch.owner_id
          AND policy.project_id = dispatch.project_id
          AND policy.revision = dispatch.policy_revision
        """
    )
    op.execute(
        "ALTER TABLE run_dispatches ADD CONSTRAINT run_dispatches_max_duration_check "
        "CHECK (max_duration_seconds IS NULL OR max_duration_seconds BETWEEN 1 AND 3600)"
    )
    op.execute(
        """
        CREATE TABLE run_results (
            owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
            run_id uuid NOT NULL,
            result_text text NOT NULL,
            byte_length integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (owner_id, run_id),
            CHECK (byte_length BETWEEN 1 AND 1048576),
            CHECK (byte_length = octet_length(result_text)),
            FOREIGN KEY (owner_id, run_id)
                REFERENCES runs(owner_id, id) ON DELETE RESTRICT
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE run_results")
    op.execute(
        "ALTER TABLE run_dispatches DROP CONSTRAINT run_dispatches_max_duration_check"
    )
    op.execute(
        "ALTER TABLE activity_events DROP CONSTRAINT activity_events_event_type_check"
    )
    op.execute(
        """
        ALTER TABLE activity_events ADD CONSTRAINT activity_events_event_type_check
        CHECK (event_type IN (
            'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked',
            'run.tool_action', 'run.succeeded', 'run.failed', 'run.canceled',
            'session.heartbeat'
        ))
        """
    )
    op.execute(
        "ALTER TABLE run_dispatches "
        "DROP COLUMN cancellation_requested_at, DROP COLUMN max_duration_seconds"
    )
