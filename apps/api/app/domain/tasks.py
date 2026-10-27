from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TaskSpec:
    project_id: UUID
    agent_instance_id: UUID | None
    title: str
    goal: str
    input_text: str
    resource_refs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    blocked_by_task_ids: tuple[UUID, ...]
    max_duration_minutes: int | None
    max_child_runs: int | None


@dataclass(frozen=True, slots=True)
class Task:
    id: UUID
    owner_id: str
    revision: int
    stage: str
    project_id: UUID
    project_name: str
    agent_instance_id: UUID | None
    agent_instance_name: str | None
    title: str
    goal: str
    input_text: str
    resource_refs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    blocked_by_task_ids: tuple[UUID, ...]
    max_duration_minutes: int | None
    max_child_runs: int | None
    created_at: datetime
    updated_at: datetime
    revision_created_at: datetime


class TaskInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TaskRepository(Protocol):
    async def create(self, owner_id: str, spec: TaskSpec) -> Task: ...

    async def list_for_owner(
        self, owner_id: str, *, project_id: UUID | None, limit: int, offset: int
    ) -> list[Task]: ...

    async def get_for_owner(self, owner_id: str, task_id: UUID) -> Task | None: ...

    async def list_revisions_for_owner(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Task] | None: ...

    async def update_for_owner(self, owner_id: str, task_id: UUID, spec: TaskSpec) -> Task | None: ...
