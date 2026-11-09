from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.domain.evidence import ActivityEventDraft
from app.domain.orchestration import (
    OrchestrationInputError,
    OrchestrationRepository,
    WorkflowLeaseBinding,
    validate_parent_runtime_checkpoint,
)
from app.domain.runs import RunRepository
from app.domain.workflow_reconciliation import (
    WorkflowReconciliationConflict,
    WorkflowReconciliationNotFound,
    WorkflowReconciliationReceipt,
    WorkflowReconciliationRepository,
)


class WorkflowReconciliation:
    def __init__(
        self,
        orchestration: OrchestrationRepository,
        runs: RunRepository,
        repository: WorkflowReconciliationRepository,
    ) -> None:
        self._orchestration = orchestration
        self._runs = runs
        self._repository = repository

    async def abandon_as_unknown(
        self, owner_id: str, parent_run_id: UUID
    ) -> WorkflowReconciliationReceipt:
        async with self._orchestration.workflow_start_lock(owner_id):
            existing = await self._repository.get(owner_id, parent_run_id)
            if existing is not None:
                return existing

            plan = await self._orchestration.get_plan(owner_id, parent_run_id)
            if plan is None:
                raise WorkflowReconciliationNotFound
            statuses = await self._orchestration.node_statuses(owner_id, parent_run_id)
            if not statuses or set(statuses) != {node.node_id for node in plan.nodes}:
                raise WorkflowReconciliationConflict("workflow state is incomplete")

            checkpoint = await self._orchestration.get_checkpoint(owner_id, parent_run_id)
            if checkpoint is not None:
                try:
                    validate_parent_runtime_checkpoint(checkpoint, plan)
                except OrchestrationInputError:
                    pass
                else:
                    raise WorkflowReconciliationConflict("checkpoint is valid")

            run = await self._runs.get_for_owner(owner_id, parent_run_id)
            if run is None:
                raise WorkflowReconciliationNotFound
            snapshot = run.configuration_snapshot
            task_snapshot = snapshot.get("task")
            instance_snapshot = snapshot.get("agent_instance")
            try:
                if not isinstance(task_snapshot, dict) or not isinstance(instance_snapshot, dict):
                    raise ValueError
                project_id = UUID(str(task_snapshot["project_id"]))
                instance_id = UUID(str(instance_snapshot["id"]))
            except (KeyError, TypeError, ValueError) as error:
                raise WorkflowReconciliationConflict("parent Run snapshot is incomplete") from error

            holder_id = uuid4()
            fencing_token = await self._orchestration.acquire_lease(
                owner_id, parent_run_id, holder_id, seconds=30,
            )
            if fencing_token is None:
                raise WorkflowReconciliationConflict("workflow lease is active")
            lease = WorkflowLeaseBinding(owner_id, parent_run_id, holder_id, fencing_token)
            event = ActivityEventDraft(
                owner_id=owner_id,
                source_id="orchestration-controller",
                event_id=uuid5(
                    NAMESPACE_URL,
                    f"agent-office:workflow-reconciliation:{owner_id}:{parent_run_id}:abandoned-unknown",
                ),
                schema_version=1,
                source_session_id=None,
                workspace_id=None,
                project_id=project_id,
                task_id=run.task_id,
                task_revision=run.task_revision,
                agent_instance_id=instance_id,
                run_id=parent_run_id,
                event_type="run.unknown",
                occurred_at=datetime.now(UTC),
                source_sequence=0,
            )
            try:
                return await self._repository.abandon_as_unknown(
                    owner_id=owner_id,
                    actor_owner_id=owner_id,
                    plan=plan,
                    checkpoint_snapshot=checkpoint,
                    lease=lease,
                    event=event,
                )
            finally:
                await self._orchestration.release_lease(
                    owner_id, parent_run_id, holder_id, fencing_token,
                )
