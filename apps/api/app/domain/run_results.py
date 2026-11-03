from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


from app.domain.orchestration import WorkflowLeaseBinding
@dataclass(frozen=True, slots=True)
class RunResult:
    owner_id: str
    run_id: UUID
    text: str
    byte_length: int
    created_at: datetime


class RunResultConflictError(ValueError):
    pass


class RunResultRepository(Protocol):
    async def create_for_run(
        self, owner_id: str, run_id: UUID, text: str, *,
        workflow_lease: WorkflowLeaseBinding | None = None,
    ) -> tuple[RunResult, bool]: ...

    async def get_for_owner(self, owner_id: str, run_id: UUID) -> RunResult | None: ...
