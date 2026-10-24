from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg


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


_TASK_SELECT = """
SELECT task.id, task.owner_id, task.current_revision AS revision,
       'Backlog'::text AS stage, revision.project_id, project.name AS project_name,
       revision.agent_instance_id, instance.display_name AS agent_instance_name,
       revision.title, revision.goal, revision.input_text,
       revision.resource_refs, revision.acceptance_criteria,
       ARRAY(
           SELECT dependency.blocked_by_task_id
           FROM task_revision_dependencies AS dependency
           WHERE dependency.owner_id = revision.owner_id
             AND dependency.task_id = revision.task_id
             AND dependency.revision = revision.revision
           ORDER BY dependency.blocked_by_task_id
       ) AS blocked_by_task_ids,
       revision.max_duration_minutes, revision.max_child_runs,
       task.created_at, task.updated_at, revision.created_at AS revision_created_at
FROM tasks AS task
JOIN task_revisions AS revision
  ON revision.task_id = task.id AND revision.revision = task.current_revision
JOIN projects AS project
  ON project.owner_id = revision.owner_id AND project.id = revision.project_id
LEFT JOIN agent_instances AS instance
  ON instance.owner_id = revision.owner_id AND instance.id = revision.agent_instance_id
"""

_REVISION_SELECT = """
SELECT task.id, task.owner_id, revision.revision,
       'Backlog'::text AS stage, revision.project_id, project.name AS project_name,
       revision.agent_instance_id, instance.display_name AS agent_instance_name,
       revision.title, revision.goal, revision.input_text,
       revision.resource_refs, revision.acceptance_criteria,
       ARRAY(
           SELECT dependency.blocked_by_task_id
           FROM task_revision_dependencies AS dependency
           WHERE dependency.owner_id = revision.owner_id
             AND dependency.task_id = revision.task_id
             AND dependency.revision = revision.revision
           ORDER BY dependency.blocked_by_task_id
       ) AS blocked_by_task_ids,
       revision.max_duration_minutes, revision.max_child_runs,
       task.created_at, task.updated_at, revision.created_at AS revision_created_at
FROM task_revisions AS revision
JOIN tasks AS task ON task.owner_id = revision.owner_id AND task.id = revision.task_id
JOIN projects AS project ON project.owner_id = revision.owner_id AND project.id = revision.project_id
LEFT JOIN agent_instances AS instance
  ON instance.owner_id = revision.owner_id AND instance.id = revision.agent_instance_id
"""


def _task(row: asyncpg.Record) -> Task:
    return Task(
        id=row["id"],
        owner_id=row["owner_id"],
        revision=row["revision"],
        stage=row["stage"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        agent_instance_id=row["agent_instance_id"],
        agent_instance_name=row["agent_instance_name"],
        title=row["title"],
        goal=row["goal"],
        input_text=row["input_text"],
        resource_refs=tuple(row["resource_refs"]),
        acceptance_criteria=tuple(row["acceptance_criteria"]),
        blocked_by_task_ids=tuple(row["blocked_by_task_ids"]),
        max_duration_minutes=row["max_duration_minutes"],
        max_child_runs=row["max_child_runs"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        revision_created_at=row["revision_created_at"],
    )


class PostgresTaskRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _lock_owner(self, connection: asyncpg.Connection, owner_id: str) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", owner_id
        )

    async def _check_spec(self, connection: asyncpg.Connection, owner_id: str, spec: TaskSpec) -> None:
        project = await connection.fetchval(
            "SELECT id FROM projects WHERE owner_id = $1 AND id = $2",
            owner_id,
            spec.project_id,
        )
        if project is None:
            raise TaskInputError("reference")
        if spec.agent_instance_id is not None:
            instance = await connection.fetchval(
                "SELECT id FROM agent_instances "
                "WHERE owner_id = $1 AND id = $2 AND archived_at IS NULL "
                "FOR SHARE",
                owner_id,
                spec.agent_instance_id,
            )
            if instance is None:
                raise TaskInputError("reference")
        if spec.blocked_by_task_ids:
            count = await connection.fetchval(
                "SELECT count(*) FROM tasks WHERE owner_id = $1 AND id = ANY($2::uuid[])",
                owner_id,
                list(spec.blocked_by_task_ids),
            )
            if count != len(spec.blocked_by_task_ids):
                raise TaskInputError("reference")

    async def _check_cycle(
        self,
        connection: asyncpg.Connection,
        owner_id: str,
        task_id: UUID,
        blocked_by_task_ids: tuple[UUID, ...],
    ) -> None:
        if not blocked_by_task_ids:
            return
        creates_cycle = await connection.fetchval(
            """
            WITH RECURSIVE current_edges(task_id, blocked_by_task_id) AS (
                SELECT dependency.task_id, dependency.blocked_by_task_id
                FROM task_revision_dependencies AS dependency
                JOIN tasks AS task
                  ON task.owner_id = dependency.owner_id
                 AND task.id = dependency.task_id
                 AND task.current_revision = dependency.revision
                WHERE dependency.owner_id = $1
            ), reachable(task_id) AS (
                SELECT unnest($2::uuid[])
                UNION
                SELECT edge.blocked_by_task_id
                FROM current_edges AS edge
                JOIN reachable ON reachable.task_id = edge.task_id
            )
            SELECT EXISTS (SELECT 1 FROM reachable WHERE task_id = $3)
            """,
            owner_id,
            list(blocked_by_task_ids),
            task_id,
        )
        if creates_cycle:
            raise TaskInputError("cycle")

    async def _insert_revision(
        self,
        connection: asyncpg.Connection,
        owner_id: str,
        task_id: UUID,
        revision: int,
        spec: TaskSpec,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO task_revisions (
                owner_id, task_id, revision, project_id, agent_instance_id,
                title, goal, input_text, resource_refs, acceptance_criteria,
                max_duration_minutes, max_child_runs
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            owner_id,
            task_id,
            revision,
            spec.project_id,
            spec.agent_instance_id,
            spec.title,
            spec.goal,
            spec.input_text,
            list(spec.resource_refs),
            list(spec.acceptance_criteria),
            spec.max_duration_minutes,
            spec.max_child_runs,
        )
        if spec.blocked_by_task_ids:
            await connection.executemany(
                """
                INSERT INTO task_revision_dependencies
                    (owner_id, task_id, revision, blocked_by_task_id)
                VALUES ($1, $2, $3, $4)
                """,
                [
                    (owner_id, task_id, revision, dependency_id)
                    for dependency_id in spec.blocked_by_task_ids
                ],
            )

    async def _get(
        self, connection: asyncpg.Connection, owner_id: str, task_id: UUID
    ) -> Task | None:
        row = await connection.fetchrow(
            _TASK_SELECT + " WHERE task.owner_id = $1 AND task.id = $2",
            owner_id,
            task_id,
        )
        return _task(row) if row is not None else None

    async def create(self, owner_id: str, spec: TaskSpec) -> Task:
        task_id = uuid4()
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_owner(connection, owner_id)
                await self._check_spec(connection, owner_id, spec)
                await connection.execute(
                    "INSERT INTO tasks (id, owner_id, current_revision) VALUES ($1, $2, 1)",
                    task_id,
                    owner_id,
                )
                await self._insert_revision(connection, owner_id, task_id, 1, spec)
                task = await self._get(connection, owner_id, task_id)
                if task is None:
                    raise RuntimeError("created task could not be read")
                return task

    async def list_for_owner(
        self, owner_id: str, *, project_id: UUID | None, limit: int, offset: int
    ) -> list[Task]:
        rows = await self._pool.fetch(
            _TASK_SELECT
            + " WHERE task.owner_id = $1 AND ($2::uuid IS NULL OR revision.project_id = $2)"
            + " ORDER BY task.created_at DESC, task.id LIMIT $3 OFFSET $4",
            owner_id,
            project_id,
            limit,
            offset,
        )
        return [_task(row) for row in rows]

    async def get_for_owner(self, owner_id: str, task_id: UUID) -> Task | None:
        async with self._pool.acquire() as connection:
            return await self._get(connection, owner_id, task_id)

    async def list_revisions_for_owner(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Task] | None:
        async with self._pool.acquire() as connection:
            if await connection.fetchval(
                "SELECT 1 FROM tasks WHERE owner_id = $1 AND id = $2", owner_id, task_id
            ) is None:
                return None
            rows = await connection.fetch(
                _REVISION_SELECT
                + " WHERE task.owner_id = $1 AND task.id = $2"
                + " ORDER BY revision.revision LIMIT $3 OFFSET $4",
                owner_id,
                task_id,
                limit,
                offset,
            )
        return [_task(row) for row in rows]

    async def update_for_owner(self, owner_id: str, task_id: UUID, spec: TaskSpec) -> Task | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await self._lock_owner(connection, owner_id)
                current = await self._get(connection, owner_id, task_id)
                if current is None:
                    return None
                await self._check_spec(connection, owner_id, spec)
                if spec.blocked_by_task_ids:
                    await self._check_cycle(connection, owner_id, task_id, spec.blocked_by_task_ids)
                current_spec = TaskSpec(
                    project_id=current.project_id,
                    agent_instance_id=current.agent_instance_id,
                    title=current.title,
                    goal=current.goal,
                    input_text=current.input_text,
                    resource_refs=current.resource_refs,
                    acceptance_criteria=current.acceptance_criteria,
                    blocked_by_task_ids=current.blocked_by_task_ids,
                    max_duration_minutes=current.max_duration_minutes,
                    max_child_runs=current.max_child_runs,
                )
                if current_spec == spec:
                    return current
                next_revision = current.revision + 1
                await connection.execute(
                    "UPDATE tasks SET current_revision = $3, updated_at = now() "
                    "WHERE owner_id = $1 AND id = $2",
                    owner_id,
                    task_id,
                    next_revision,
                )
                await self._insert_revision(connection, owner_id, task_id, next_revision, spec)
                updated = await self._get(connection, owner_id, task_id)
                if updated is None:
                    raise RuntimeError("updated task could not be read")
                return updated
