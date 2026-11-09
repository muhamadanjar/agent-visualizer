from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.domain.evidence import ActivityEventDraft
from app.domain.orchestration import WorkflowLeaseBinding, WorkflowPlan


class WorkflowReconciliationError(RuntimeError):
    pass


class WorkflowReconciliationNotFound(WorkflowReconciliationError):
    pass


class WorkflowReconciliationConflict(WorkflowReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowReconciliationReceipt:
    parent_run_id: UUID
    event_id: UUID
    event_cursor: int


class WorkflowReconciliationRepository(Protocol):
    async def get(
        self, owner_id: str, parent_run_id: UUID
    ) -> WorkflowReconciliationReceipt | None: ...

    async def abandon_as_unknown(
        self,
        owner_id: str,
        actor_owner_id: str,
        plan: WorkflowPlan,
        checkpoint_snapshot: Any | None,
        lease: WorkflowLeaseBinding,
        event: ActivityEventDraft,
    ) -> WorkflowReconciliationReceipt: ...
