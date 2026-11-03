from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.domain.orchestration import WorkflowLeaseBinding

@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    owner_id: str
    task_id: UUID
    task_revision: int
    attempt_number: int
    request_id: UUID
    status: str
    configuration_snapshot: dict[str, object]
    created_at: datetime


class RunInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkflowDelegationBinding:
    parent_run_id: UUID
    node_id: str
    canonical_digest: str


class RunRepository(Protocol):
    async def create_for_task(
        self,
        owner_id: str,
        task_id: UUID,
        request_id: UUID,
        *,
        execution_policy_revision: int | None = None,
        expected_instance_id: UUID | None = None,
        expected_definition_id: UUID | None = None,
        expected_definition_version: int | None = None,
        expected_task_revision: int | None = None,
        expected_model_scope: Mapping[str, object] | None = None,
        dependency_outputs: Mapping[str, object] | None = None,
        workflow_lease: WorkflowLeaseBinding | None = None,
    ) -> tuple[Run, bool]: ...
    async def get_for_owner(self, owner_id: str, run_id: UUID) -> Run | None: ...
    async def get_for_request(self, owner_id: str, request_id: UUID) -> Run | None: ...

    async def list_for_task(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Run] | None: ...
