"""Persist workspace policies, Runner health, and idempotent Run dispatch.

Revision ID: execution_0009
Revises: legacy_0008
"""

from __future__ import annotations

from alembic import op


revision = "execution_0009"
down_revision = "legacy_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_execution_policies (
            owner_id text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
            project_id uuid NOT NULL,
            workspace_path text NOT NULL CHECK (char_length(workspace_path) BETWEEN 1 AND 512),
            allowed_tools text[] NOT NULL
                CHECK (cardinality(allowed_tools) BETWEEN 1 AND 3)
                CHECK (allowed_tools <@ ARRAY['list_files', 'read_file', 'write_file']::text[]),
            max_duration_seconds integer NOT NULL CHECK (max_duration_seconds BETWEEN 1 AND 3600),
            max_inference_turns integer NOT NULL CHECK (max_inference_turns BETWEEN 1 AND 12),
            max_tool_calls integer NOT NULL CHECK (max_tool_calls BETWEEN 1 AND 50),
            enabled boolean NOT NULL DEFAULT true,
            revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (owner_id, project_id),
            FOREIGN KEY (owner_id, project_id)
                REFERENCES projects (owner_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE runner_connections (
            runner_id uuid PRIMARY KEY,
            provider_ready boolean NOT NULL,
            last_seen_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE run_dispatches (
            owner_id text NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            project_id uuid NOT NULL,
            instance_id uuid NOT NULL,
            policy_revision integer NOT NULL CHECK (policy_revision >= 1),
            state text NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'leased', 'succeeded', 'failed', 'canceled', 'unknown', 'blocked')),
            runner_id uuid,
            lease_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (owner_id, run_id),
            FOREIGN KEY (owner_id, run_id)
                REFERENCES runs (owner_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (owner_id, task_id)
                REFERENCES tasks (owner_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (owner_id, project_id)
                REFERENCES projects (owner_id, id) ON DELETE RESTRICT,
            FOREIGN KEY (owner_id, instance_id)
                REFERENCES agent_instances (owner_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX runner_connections_last_seen_idx ON runner_connections (last_seen_at)"
    )
    op.execute(
        "CREATE INDEX run_dispatches_queued_idx ON run_dispatches (created_at) WHERE state = 'queued'"
    )
    op.execute(
        "CREATE UNIQUE INDEX run_dispatches_active_instance_idx "
        "ON run_dispatches (owner_id, instance_id) WHERE state = 'leased'"
    )
    op.execute("ALTER TABLE activity_events ADD COLUMN action_summary text")
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
        """
        ALTER TABLE activity_events ADD CONSTRAINT activity_events_action_summary_check
        CHECK (
            (event_type = 'run.tool_action' AND action_summary IS NOT NULL
             AND char_length(action_summary) BETWEEN 1 AND 240)
            OR
            (event_type <> 'run.tool_action' AND action_summary IS NULL)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE run_activity_projections
        ADD COLUMN last_action_summary text
            CONSTRAINT run_activity_projections_last_action_summary_check
            CHECK (last_action_summary IS NULL OR char_length(last_action_summary) BETWEEN 1 AND 240)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE run_dispatches")
    op.execute("DROP TABLE runner_connections")
    op.execute(
        "ALTER TABLE run_activity_projections DROP CONSTRAINT run_activity_projections_last_action_summary_check"
    )
    op.execute("ALTER TABLE run_activity_projections DROP COLUMN last_action_summary")
    op.execute(
        "ALTER TABLE activity_events DROP CONSTRAINT activity_events_action_summary_check"
    )
    op.execute("ALTER TABLE activity_events DROP COLUMN action_summary")
    op.execute(
        "ALTER TABLE activity_events DROP CONSTRAINT activity_events_event_type_check"
    )
    op.execute(
        """
        ALTER TABLE activity_events ADD CONSTRAINT activity_events_event_type_check
        CHECK (event_type IN (
            'run.started', 'run.waiting_input', 'run.waiting_approval', 'run.blocked',
            'run.succeeded', 'run.failed', 'run.canceled', 'session.heartbeat'
        ))
        """
    )
    op.execute("DROP TABLE project_execution_policies")
