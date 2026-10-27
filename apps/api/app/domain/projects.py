from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Project:
    id: UUID
    owner_id: str
    name: str
    summary: str
    created_at: datetime


class ProjectRepository(Protocol):
    async def create(self, owner_id: str, name: str, summary: str) -> Project: ...

    async def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> list[Project]: ...

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None: ...
