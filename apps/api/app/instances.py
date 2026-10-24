from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg


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


_INSTANCE_SELECT = """
SELECT instance.id, instance.owner_id, instance.display_name,
       instance.definition_id, version.name AS definition_name,
       instance.project_id, project.name AS project_name,
       instance.archived_at, instance.created_at, instance.updated_at
FROM agent_instances AS instance
JOIN agent_definitions AS definition
  ON definition.owner_id = instance.owner_id AND definition.id = instance.definition_id
JOIN agent_definition_versions AS version
  ON version.definition_id = definition.id AND version.version = definition.current_version
LEFT JOIN projects AS project
  ON project.owner_id = instance.owner_id AND project.id = instance.project_id
"""


def _instance(row: asyncpg.Record) -> AgentInstance:
    return AgentInstance(
        id=row["id"],
        owner_id=row["owner_id"],
        display_name=row["display_name"],
        definition_id=row["definition_id"],
        definition_name=row["definition_name"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        archived_at=row["archived_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresAgentInstanceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _get(
        self, connection: asyncpg.Connection, owner_id: str, instance_id: UUID
    ) -> AgentInstance | None:
        row = await connection.fetchrow(
            _INSTANCE_SELECT + " WHERE instance.owner_id = $1 AND instance.id = $2",
            owner_id,
            instance_id,
        )
        return _instance(row) if row is not None else None

    async def _check_definition(
        self, connection: asyncpg.Connection, owner_id: str, definition_id: UUID
    ) -> None:
        row = await connection.fetchrow(
            """
            SELECT archived_at FROM agent_definitions
            WHERE owner_id = $1 AND id = $2
            FOR SHARE
            """,
            owner_id,
            definition_id,
        )
        if row is None or row["archived_at"] is not None:
            raise AgentInstanceInputError("definition is unavailable")

    async def _check_project(
        self, connection: asyncpg.Connection, owner_id: str, project_id: UUID | None
    ) -> None:
        if project_id is None:
            return
        row = await connection.fetchrow(
            "SELECT id FROM projects WHERE owner_id = $1 AND id = $2",
            owner_id,
            project_id,
        )
        if row is None:
            raise AgentInstanceInputError("project is unavailable")

    async def create(
        self,
        owner_id: str,
        display_name: str,
        definition_id: UUID,
        project_id: UUID | None,
    ) -> AgentInstance:
        instance_id = uuid4()
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await self._check_definition(connection, owner_id, definition_id)
                await self._check_project(connection, owner_id, project_id)
                await connection.execute(
                    """
                    INSERT INTO agent_instances
                        (id, owner_id, display_name, definition_id, project_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    instance_id,
                    owner_id,
                    display_name,
                    definition_id,
                    project_id,
                )
                result = await self._get(connection, owner_id, instance_id)
                if result is None:
                    raise RuntimeError("created agent instance could not be read")
                return result

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
        project_id: UUID | None = None,
    ) -> list[AgentInstance]:
        rows = await self._pool.fetch(
            _INSTANCE_SELECT
            + " WHERE instance.owner_id = $1"
            + " AND ($2::boolean OR instance.archived_at IS NULL)"
            + " AND ($3::uuid IS NULL OR instance.project_id = $3)"
            + " ORDER BY instance.created_at, instance.id LIMIT $4 OFFSET $5",
            owner_id,
            include_archived,
            project_id,
            limit,
            offset,
        )
        return [_instance(row) for row in rows]

    async def get_for_owner(self, owner_id: str, instance_id: UUID) -> AgentInstance | None:
        async with self._pool.acquire() as connection:
            return await self._get(connection, owner_id, instance_id)

    async def update_for_owner(
        self, owner_id: str, instance_id: UUID, changes: dict[str, object]
    ) -> AgentInstance | None:
        allowed = {"display_name", "definition_id", "project_id"}
        if not changes or changes.keys() - allowed:
            raise ValueError("invalid agent-instance update fields")
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT definition_id FROM agent_instances
                    WHERE owner_id = $1 AND id = $2 AND archived_at IS NULL
                    FOR UPDATE
                    """,
                    owner_id,
                    instance_id,
                )
                if current is None:
                    return None
                if (
                    "definition_id" in changes
                    and changes["definition_id"] != current["definition_id"]
                ):
                    await self._check_definition(
                        connection, owner_id, changes["definition_id"]  # type: ignore[arg-type]
                    )
                if "project_id" in changes:
                    await self._check_project(
                        connection, owner_id, changes["project_id"]  # type: ignore[arg-type]
                    )
                assignments = [
                    f"{field} = ${index}" for index, field in enumerate(changes, start=3)
                ]
                values: list[object] = [owner_id, instance_id, *changes.values()]
                row = await connection.fetchrow(
                    "UPDATE agent_instances SET "
                    + ", ".join(assignments)
                    + ", updated_at = now() WHERE owner_id = $1 AND id = $2"
                    + " AND archived_at IS NULL RETURNING id",
                    *values,
                )
                return await self._get(connection, owner_id, instance_id) if row else None

    async def archive_for_owner(
        self, owner_id: str, instance_id: UUID
    ) -> AgentInstance | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE agent_instances
                    SET archived_at = COALESCE(archived_at, now()),
                        updated_at = CASE WHEN archived_at IS NULL THEN now() ELSE updated_at END
                    WHERE owner_id = $1 AND id = $2
                    """,
                    owner_id,
                    instance_id,
                )
                return await self._get(connection, owner_id, instance_id)
