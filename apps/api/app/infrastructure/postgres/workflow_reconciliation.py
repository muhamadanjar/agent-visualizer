from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.evidence import ActivityEventDraft
from app.domain.orchestration import (
    OrchestrationInputError,
    WorkflowLeaseBinding,
    WorkflowPlan,
    validate_parent_runtime_checkpoint,
)
from app.domain.workflow_reconciliation import (
    WorkflowReconciliationConflict,
    WorkflowReconciliationReceipt,
)
from app.infrastructure.postgres.evidence import PostgresActivityEventRepository
from app.infrastructure.postgres.models import (
    ActivityEventTable,
    OrchestrationCheckpointTable,
    OrchestrationDelegationTable,
    RunActivityProjectionTable,
    RunDispatchTable,
    WorkflowReconciliationAuditTable,
)
from app.infrastructure.postgres.raw_sql import RawSQLExecutor, require_workflow_lease


_TERMINAL_DELEGATIONS = {
    "succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown",
}
_TERMINAL_DISPATCHES = {"succeeded", "failed", "canceled", "blocked"}
_TERMINAL_RUNS = {"succeeded", "failed", "canceled", "unknown"}


class PostgresWorkflowReconciliationRepository:
    def __init__(
        self,
        sessions: async_sessionmaker,
        activity_events: PostgresActivityEventRepository,
    ) -> None:
        self._sessions = sessions
        self._activity_events = activity_events

    async def get(
        self, owner_id: str, parent_run_id: UUID
    ) -> WorkflowReconciliationReceipt | None:
        async with self._sessions() as session:
            audit = await session.scalar(
                select(WorkflowReconciliationAuditTable).where(
                    WorkflowReconciliationAuditTable.owner_id == owner_id,
                    WorkflowReconciliationAuditTable.parent_run_id == parent_run_id,
                )
            )
            return None if audit is None else await self._receipt(session, audit)

    async def abandon_as_unknown(
        self,
        owner_id: str,
        actor_owner_id: str,
        plan: WorkflowPlan,
        checkpoint_snapshot: object | None,
        lease: WorkflowLeaseBinding,
        event: ActivityEventDraft,
    ) -> WorkflowReconciliationReceipt:
        if owner_id != actor_owner_id or lease.owner_id != owner_id:
            raise WorkflowReconciliationConflict("owner scope mismatch")
        if event.owner_id != owner_id or event.run_id != plan.parent_run_id:
            raise WorkflowReconciliationConflict("event scope mismatch")

        async with self._sessions.begin() as session:
            connection = RawSQLExecutor(session)
            await require_workflow_lease(
                connection, lease, owner_id=owner_id, parent_run_id=plan.parent_run_id,
            )
            existing = await session.scalar(
                select(WorkflowReconciliationAuditTable)
                .where(
                    WorkflowReconciliationAuditTable.owner_id == owner_id,
                    WorkflowReconciliationAuditTable.parent_run_id == plan.parent_run_id,
                )
            )
            if existing is not None:
                return await self._receipt(session, existing)

            checkpoint = await session.scalar(
                select(OrchestrationCheckpointTable)
                .where(
                    OrchestrationCheckpointTable.owner_id == owner_id,
                    OrchestrationCheckpointTable.parent_run_id == plan.parent_run_id,
                )
                .with_for_update()
            )
            current_checkpoint = None if checkpoint is None else checkpoint.state
            if current_checkpoint != checkpoint_snapshot:
                raise WorkflowReconciliationConflict("checkpoint changed")
            if checkpoint is not None:
                try:
                    validate_parent_runtime_checkpoint(checkpoint.state, plan)
                except OrchestrationInputError:
                    pass
                else:
                    raise WorkflowReconciliationConflict("checkpoint is valid")

            projection = await session.scalar(
                select(RunActivityProjectionTable)
                .where(
                    RunActivityProjectionTable.owner_id == owner_id,
                    RunActivityProjectionTable.run_id == plan.parent_run_id,
                )
                .with_for_update()
            )
            if projection is not None and projection.status in _TERMINAL_RUNS:
                raise WorkflowReconciliationConflict("parent Run is already terminal")
            if projection is not None and projection.last_sequence is not None:
                event = replace(event, source_sequence=projection.last_sequence + 1)

            delegations = list(await session.scalars(
                select(OrchestrationDelegationTable)
                .where(
                    OrchestrationDelegationTable.owner_id == owner_id,
                    OrchestrationDelegationTable.parent_run_id == plan.parent_run_id,
                )
                .order_by(OrchestrationDelegationTable.node_id)
                .with_for_update()
            ))
            nodes = {node.node_id: node for node in plan.nodes}
            if len(delegations) != len(nodes) or any(
                row.node_id not in nodes
                or row.plan_digest != plan.plan_digest
                or row.revision != plan.revision
                or row.canonical_digest != nodes[row.node_id].canonical_digest()
                for row in delegations
            ):
                raise WorkflowReconciliationConflict("workflow plan changed")

            child_run_ids = [row.child_run_id for row in delegations if row.child_run_id is not None]
            dispatches = list(await session.scalars(
                select(RunDispatchTable)
                .where(
                    RunDispatchTable.owner_id == owner_id,
                    RunDispatchTable.run_id.in_(child_run_ids),
                )
                .order_by(RunDispatchTable.run_id)
                .with_for_update()
            )) if child_run_ids else []
            dispatch_by_run = {row.run_id: row for row in dispatches}
            now = datetime.now(UTC)
            if (
                len(set(child_run_ids)) != len(child_run_ids)
                or set(dispatch_by_run) != set(child_run_ids)
                or any(
                    row.state not in _TERMINAL_DISPATCHES
                    or row.lease_expires_at is not None and row.lease_expires_at > now
                    for row in dispatches
                )
            ):
                raise WorkflowReconciliationConflict("child Run may still be active")

            event_record, _created = await self._activity_events.append_manual_reconciliation_event_in_transaction(
                session, event, workflow_lease=lease,
            )
            session.add(WorkflowReconciliationAuditTable(
                owner_id=owner_id,
                parent_run_id=plan.parent_run_id,
                actor_owner_id=actor_owner_id,
                outcome="abandoned_unknown",
                checkpoint_version=None if checkpoint is None else checkpoint.version,
                checkpoint_snapshot=current_checkpoint,
                delegations_snapshot=[
                    {
                        "node_id": row.node_id,
                        "status": row.status,
                        "approval_id": None if row.approval_id is None else str(row.approval_id),
                        "child_run_id": None if row.child_run_id is None else str(row.child_run_id),
                    }
                    for row in delegations
                ],
                event_cursor=event_record.cursor,
            ))
            for row in delegations:
                if row.status not in _TERMINAL_DELEGATIONS:
                    row.status = "unknown"
                    row.updated_at = now
            if checkpoint is not None:
                await session.delete(checkpoint)
            await session.flush()
            return WorkflowReconciliationReceipt(
                plan.parent_run_id, event_record.event_id, event_record.cursor,
            )

    @staticmethod
    async def _receipt(session, audit: WorkflowReconciliationAuditTable) -> WorkflowReconciliationReceipt:
        event = await session.scalar(
            select(ActivityEventTable).where(
                ActivityEventTable.owner_id == audit.owner_id,
                ActivityEventTable.cursor == audit.event_cursor,
            )
        )
        if event is None:
            raise RuntimeError("workflow reconciliation audit event is missing")
        return WorkflowReconciliationReceipt(
            audit.parent_run_id, event.event_id, audit.event_cursor,
        )
