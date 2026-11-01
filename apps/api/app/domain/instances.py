from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Protocol
from uuid import UUID


OfficeArea = Literal["architecture", "engineering", "review", "security", "meeting", "lounge"]
OFFICE_AREAS: tuple[OfficeArea, ...] = (
    "architecture",
    "engineering",
    "review",
    "security",
    "meeting",
    "lounge",
)


def least_populated_office_area(counts: Mapping[str, int]) -> OfficeArea:
    return min(OFFICE_AREAS, key=lambda area: counts.get(area, 0))


@dataclass(frozen=True, slots=True)
class AgentInstance:
    id: UUID
    owner_id: str
    display_name: str
    definition_id: UUID
    definition_name: str
    project_id: UUID | None
    project_name: str | None
    office_area: OfficeArea
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
        office_area: OfficeArea | None = None,
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


