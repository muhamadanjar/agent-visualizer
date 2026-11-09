from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.workflow_reconciliation import WorkflowReconciliation
from app.domain.orchestration import (
    DelegationSpec,
    WorkflowLeaseBinding,
    WorkflowLimits,
    WorkflowPlan,
    digest,
)

from app.domain.workflow_reconciliation import (
    WorkflowReconciliationConflict,
    WorkflowReconciliationReceipt,
)


def test_owner_reconciliation_terminalizes_corrupt_workflow_as_unknown() -> None:
    async def scenario() -> None:
        owner_id = "owner"
        parent_run_id = uuid4()
        node = DelegationSpec(
            node_id="child",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=uuid4(),
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=(),
            output_schema={"type": "object"},
            model_scope={},
            tool_scope=("list_files", "read_file"),
            limits={},
        )
        plan = WorkflowPlan(
            parent_run_id=parent_run_id,
            plan_digest=digest({"nodes": [node.canonical_digest()]}),
            revision=1,
            nodes=(node,),
            limits=WorkflowLimits(),
        )
        checkpoint = {
            "plan_digest": plan.plan_digest,
            "plan_revision": plan.revision,
            "status": "waiting_children",
            "status_sequence": -1,
            "last_emitted_status": "waiting_children",
        }
        lease = WorkflowLeaseBinding(owner_id, parent_run_id, uuid4(), 4)

        class Orchestration:
            @asynccontextmanager
            async def workflow_start_lock(self, _owner_id):
                yield

            async def get_plan(self, _owner_id, _parent_run_id):
                return plan
            async def node_statuses(self, _owner_id, _parent_run_id):
                return {"child": "waiting_approval"}

            async def get_checkpoint(self, _owner_id, _parent_run_id):
                return checkpoint

            async def acquire_lease(self, _owner_id, _parent_run_id, _holder_id, *, seconds):
                assert seconds == 30
                return lease.fencing_token

            async def release_lease(self, *_args):
                return True

        class Runs:
            async def get_for_owner(self, _owner_id, _parent_run_id):
                return SimpleNamespace(
                    id=parent_run_id,
                    task_id=uuid4(),
                    task_revision=1,
                    configuration_snapshot={
                        "task": {"project_id": str(node.project_id)},
                        "agent_instance": {"id": str(node.instance_id)},
                    },
                )

        class Reconciliations:
            call = None

            async def get(self, _owner_id, _parent_run_id):
                return None

            async def abandon_as_unknown(self, **kwargs):
                self.call = kwargs
                return WorkflowReconciliationReceipt(
                    parent_run_id, kwargs["event"].event_id, 1,
                )
        reconciliations = Reconciliations()
        service = WorkflowReconciliation(Orchestration(), Runs(), reconciliations)
        result = await service.abandon_as_unknown(owner_id, parent_run_id)

        assert result.parent_run_id == parent_run_id
        assert reconciliations.call["checkpoint_snapshot"] == checkpoint
        assert reconciliations.call["lease"].owner_id == owner_id
        assert reconciliations.call["lease"].parent_run_id == parent_run_id
        assert reconciliations.call["lease"].fencing_token == lease.fencing_token
        event = reconciliations.call["event"]
        assert event.event_type == "run.unknown"
        assert event.source_id == "orchestration-controller"
        assert event.run_id == parent_run_id
        assert event.owner_id == owner_id
        assert event.occurred_at.tzinfo == UTC

    asyncio.run(scenario())


def test_owner_reconciliation_refuses_valid_checkpoint() -> None:
    async def scenario() -> None:
        parent_run_id = uuid4()
        node = DelegationSpec(
            node_id="child",
            child_task_id=uuid4(),
            child_revision=1,
            definition_id=uuid4(),
            definition_version=1,
            instance_id=uuid4(),
            project_id=uuid4(),
            workspace_path="workspace",
            input_payload={},
            resource_refs=(),
            dependencies=(),
            output_schema={"type": "object"},
            model_scope={},
            tool_scope=("list_files", "read_file"),
            limits={},
        )
        plan = WorkflowPlan(
            parent_run_id=parent_run_id,
            plan_digest=digest({"nodes": [node.canonical_digest()]}),
            revision=1,
            nodes=(node,),
            limits=WorkflowLimits(),
        )
        checkpoint = {
            "plan_digest": plan.plan_digest,
            "plan_revision": plan.revision,
            "active_children": 0,
            "spent_child_runs": 0,
            "status": "waiting_approval",
            "status_sequence": 0,
            "last_emitted_status": "waiting_approval",
        }

        class Orchestration:
            @asynccontextmanager
            async def workflow_start_lock(self, _owner_id):
                yield

            async def get_plan(self, _owner_id, _parent_run_id):
                return plan

            async def node_statuses(self, _owner_id, _parent_run_id):
                return {"child": "waiting_approval"}

            async def get_checkpoint(self, _owner_id, _parent_run_id):
                return checkpoint

        class Reconciliations:
            async def get(self, _owner_id, _parent_run_id):
                return None

            async def abandon_as_unknown(self, **_kwargs):
                pytest.fail("valid checkpoints must not be abandoned")

        service = WorkflowReconciliation(Orchestration(), None, Reconciliations())
        with pytest.raises(WorkflowReconciliationConflict, match="checkpoint is valid"):
            await service.abandon_as_unknown("owner", parent_run_id)

    asyncio.run(scenario())
