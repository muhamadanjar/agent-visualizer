from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentInstance:
    id: UUID
    owner_id: str
    display_name: str
    definition_id: UUID
    definition_name: str
    project_id: UUID | None
    project_name: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentInstanceInputError(ValueError):
    """A requested instance link is missing, foreign, or no longer available."""


class AgentInstanceRepository(Protocol):
    async def create(
        self,
        owner_id: str,
        display_name: str,
        definition_id: UUID,
        project_id: UUID | None,
    ) -> AgentInstance: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
        project_id: UUID | None = None,
    ) -> list[AgentInstance]: ...

    async def get_for_owner(self, owner_id: str, instance_id: UUID) -> AgentInstance | None: ...

    async def update_for_owner(
        self, owner_id: str, instance_id: UUID, changes: dict[str, object]
    ) -> AgentInstance | None: ...

    async def archive_for_owner(
        self, owner_id: str, instance_id: UUID
    ) -> AgentInstance | None: ...
