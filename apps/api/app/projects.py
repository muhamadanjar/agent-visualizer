from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg


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


class PostgresProjectRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, owner_id: str, name: str, summary: str) -> Project:
        project_id = uuid4()
        row = await self._pool.fetchrow(
            """
            INSERT INTO projects (id, owner_id, name, summary)
            VALUES ($1, $2, $3, $4)
            RETURNING id, owner_id, name, summary, created_at
            """,
            project_id,
            owner_id,
            name,
            summary,
        )
        if row is None:
            raise RuntimeError("project insert returned no row")
        return Project(**dict(row))

    async def list_for_owner(
        self, owner_id: str, *, limit: int, offset: int
    ) -> list[Project]:
        rows = await self._pool.fetch(
            """
            SELECT id, owner_id, name, summary, created_at
            FROM projects
            WHERE owner_id = $1
            ORDER BY created_at, id
            LIMIT $2 OFFSET $3
            """,
            owner_id,
            limit,
            offset,
        )
        return [Project(**dict(row)) for row in rows]

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, owner_id, name, summary, created_at
            FROM projects
            WHERE owner_id = $1 AND id = $2
            """,
            owner_id,
            project_id,
        )
        return Project(**dict(row)) if row is not None else None
