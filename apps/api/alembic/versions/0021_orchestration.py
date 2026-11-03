"""Persist parent-run workflow plans, immutable delegations, checkpoints and leases."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "orchestration_0021"
down_revision = "office_area_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orchestration_delegations",
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_run_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("child_task_id", sa.UUID(), nullable=False),
        sa.Column("child_revision", sa.Integer(), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=False),
        sa.Column("input_payload", JSONB(), nullable=False),
        sa.Column("resource_refs", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("dependencies", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("output_schema", JSONB(), nullable=True),
        sa.Column("model_scope", JSONB(), nullable=False),
        sa.Column("tool_scope", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("limits", JSONB(), nullable=False),
        sa.Column("canonical_digest", sa.Text(), nullable=False),
        sa.Column("plan_digest", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("child_run_id", sa.UUID(), nullable=True),
        sa.Column("output", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "parent_run_id", "node_id", name="orchestration_delegation_node_key"),
        sa.ForeignKeyConstraint(["owner_id", "parent_run_id"], ["runs.owner_id", "runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id", "child_task_id"], ["tasks.owner_id", "tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id", "definition_id"], ["agent_definitions.owner_id", "agent_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id", "instance_id"], ["agent_instances.owner_id", "agent_instances.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1 AND child_revision >= 1 AND definition_version >= 1"),
        sa.CheckConstraint("char_length(workspace_path) BETWEEN 1 AND 512"),
        sa.CheckConstraint("output_schema IS NULL OR jsonb_typeof(output_schema) = 'object'"),
        sa.CheckConstraint("status IN ('planned','waiting_approval','approved','rejected','expired','queued','running','succeeded','failed','invalid','blocked','unknown')"),
    )
    op.create_index("orchestration_delegations_parent_idx", "orchestration_delegations", ["owner_id", "parent_run_id", "node_id"])
    op.execute("""
        CREATE FUNCTION prevent_orchestration_delegation_immutable_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.owner_id <> OLD.owner_id OR NEW.id <> OLD.id OR NEW.parent_run_id <> OLD.parent_run_id
               OR NEW.node_id <> OLD.node_id OR NEW.revision <> OLD.revision OR NEW.child_task_id <> OLD.child_task_id
               OR NEW.child_revision <> OLD.child_revision OR NEW.definition_id <> OLD.definition_id
               OR NEW.definition_version <> OLD.definition_version OR NEW.instance_id <> OLD.instance_id
               OR NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.workspace_path <> OLD.workspace_path
               OR NEW.input_payload <> OLD.input_payload OR NEW.resource_refs <> OLD.resource_refs
               OR NEW.dependencies <> OLD.dependencies OR NEW.output_schema IS DISTINCT FROM OLD.output_schema
               OR NEW.model_scope <> OLD.model_scope OR NEW.tool_scope <> OLD.tool_scope OR NEW.limits <> OLD.limits
               OR NEW.canonical_digest <> OLD.canonical_digest OR NEW.plan_digest <> OLD.plan_digest
               OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'orchestration delegation is immutable';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER orchestration_delegation_immutable_update
        BEFORE UPDATE ON orchestration_delegations
        FOR EACH ROW EXECUTE FUNCTION prevent_orchestration_delegation_immutable_update()
    """)
    op.create_table(
        "orchestration_checkpoints",
        sa.Column("owner_id", sa.Text(), nullable=False), sa.Column("parent_run_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("state", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("owner_id", "parent_run_id"),
        sa.ForeignKeyConstraint(["owner_id", "parent_run_id"], ["runs.owner_id", "runs.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "orchestration_leases",
        sa.Column("owner_id", sa.Text(), nullable=False), sa.Column("parent_run_id", sa.UUID(), nullable=False),
        sa.Column("holder_id", sa.UUID(), nullable=False), sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("owner_id"),
        sa.UniqueConstraint("owner_id", "parent_run_id", name="orchestration_lease_parent_key"),
        sa.ForeignKeyConstraint(["owner_id", "parent_run_id"], ["runs.owner_id", "runs.id"], ondelete="RESTRICT"),
    )
    op.drop_constraint("activity_events_event_type_check", "activity_events", type_="check")
    op.create_check_constraint(
        "activity_events_event_type_check", "activity_events",
        "event_type IN ('run.started','run.working','run.tool_started','run.reviewing','run.waiting_input','run.waiting_approval','run.waiting_children','run.blocked','run.tool_action','run.cancellation_pending','run.succeeded','run.failed','run.canceled','run.unknown','session.heartbeat','approval.requested','approval.approved','approval.rejected','approval.expired')",
    )
    op.drop_constraint("run_activity_projections_status_check", "run_activity_projections", type_="check")
    op.create_check_constraint(
        "run_activity_projections_status_check", "run_activity_projections",
        "status IN ('running','waiting_input','waiting_approval','waiting_children','blocked','succeeded','failed','canceled','unknown')",
    )


def downgrade() -> None:
    raise RuntimeError("Orchestration migration is irreversible")
