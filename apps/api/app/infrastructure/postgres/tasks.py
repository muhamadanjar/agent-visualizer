from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.tasks import Task, TaskInputError, TaskRepository, TaskSpec
from app.infrastructure.postgres.models import (
    AgentInstanceTable,
    ProjectTable,
    TaskRevisionDependencyTable,
    TaskRevisionTable,
    TaskTable,
)
from app.infrastructure.postgres.raw_sql import RawSQLExecutor


def _task_query(*, current_only: bool):
    revision_join = (
        (TaskRevisionTable.owner_id == TaskTable.owner_id)
        & (TaskRevisionTable.task_id == TaskTable.id)
    )
    if current_only:
        revision_join &= TaskRevisionTable.revision == TaskTable.current_revision
    return (
        select(
            TaskTable,
            TaskRevisionTable,
            ProjectTable.name.label("project_name"),
            AgentInstanceTable.display_name.label("agent_instance_name"),
        )
        .select_from(TaskTable)
        .join(TaskRevisionTable, revision_join)
        .join(
            ProjectTable,
            (ProjectTable.owner_id == TaskRevisionTable.owner_id)
            & (ProjectTable.id == TaskRevisionTable.project_id),
        )
        .outerjoin(
            AgentInstanceTable,
            (AgentInstanceTable.owner_id == TaskRevisionTable.owner_id)
            & (AgentInstanceTable.id == TaskRevisionTable.agent_instance_id),
        )
    )


async def _dependency_map(
    session: AsyncSession,
    owner_id: str,
    rows: list[tuple[TaskTable, TaskRevisionTable, str, str | None]],
) -> dict[tuple[UUID, int], list[UUID]]:
    keys = {(revision.task_id, revision.revision) for _, revision, _, _ in rows}
    if not keys:
        return {}
    dependencies = await session.execute(
        select(
            TaskRevisionDependencyTable.task_id,
            TaskRevisionDependencyTable.revision,
            TaskRevisionDependencyTable.blocked_by_task_id,
        )
        .where(
            TaskRevisionDependencyTable.owner_id == owner_id,
            tuple_(
                TaskRevisionDependencyTable.task_id,
                TaskRevisionDependencyTable.revision,
            ).in_(list(keys)),
        )
        .order_by(TaskRevisionDependencyTable.blocked_by_task_id)
    )
    result: dict[tuple[UUID, int], list[UUID]] = {}
    for row in dependencies:
        result.setdefault((row.task_id, row.revision), []).append(row.blocked_by_task_id)
    return result


def _domain_task(
    row: tuple[TaskTable, TaskRevisionTable, str, str | None],
    blocked_by_task_ids: list[UUID],
) -> Task:
    task, revision, project_name, instance_name = row
    return Task(
        id=task.id,
        owner_id=task.owner_id,
        revision=revision.revision,
        stage="Backlog",
        project_id=revision.project_id,
        project_name=project_name,
        agent_instance_id=revision.agent_instance_id,
        agent_instance_name=instance_name,
        title=revision.title,
        goal=revision.goal,
        input_text=revision.input_text,
        resource_refs=tuple(revision.resource_refs),
        acceptance_criteria=tuple(revision.acceptance_criteria),
        blocked_by_task_ids=tuple(blocked_by_task_ids),
        max_duration_minutes=revision.max_duration_minutes,
        max_child_runs=revision.max_child_runs,
        created_at=task.created_at,
        updated_at=task.updated_at,
        revision_created_at=revision.created_at,
    )


async def _map_rows(
    session: AsyncSession,
    owner_id: str,
    rows: list[tuple[TaskTable, TaskRevisionTable, str, str | None]],
) -> list[Task]:
    dependencies = await _dependency_map(session, owner_id, rows)
    return [
        _domain_task(row, dependencies.get((row[1].task_id, row[1].revision), []))
        for row in rows
    ]


class PostgresTaskRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _lock_owner(self, connection: RawSQLExecutor, owner_id: str) -> None:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", owner_id
        )

    async def _check_spec(
        self, session: AsyncSession, owner_id: str, spec: TaskSpec
    ) -> None:
        project_id = await session.scalar(
            select(ProjectTable.id).where(
                ProjectTable.owner_id == owner_id,
                ProjectTable.id == spec.project_id,
            )
        )
        if project_id is None:
            raise TaskInputError("reference")
        if spec.agent_instance_id is not None:
            instance_id = await session.scalar(
                select(AgentInstanceTable.id)
                .where(
                    AgentInstanceTable.owner_id == owner_id,
                    AgentInstanceTable.id == spec.agent_instance_id,
                    AgentInstanceTable.archived_at.is_(None),
                )
                .with_for_update(read=True)
            )
            if instance_id is None:
                raise TaskInputError("reference")
        if spec.blocked_by_task_ids:
            count = await session.scalar(
                select(func.count())
                .select_from(TaskTable)
                .where(
                    TaskTable.owner_id == owner_id,
                    TaskTable.id.in_(spec.blocked_by_task_ids),
                )
            )
            if count != len(spec.blocked_by_task_ids):
                raise TaskInputError("reference")

    async def _check_cycle(
        self,
        connection: RawSQLExecutor,
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
        session: AsyncSession,
        owner_id: str,
        task_id: UUID,
        revision: int,
        spec: TaskSpec,
    ) -> None:
        session.add(
            TaskRevisionTable(
                owner_id=owner_id,
                task_id=task_id,
                revision=revision,
                project_id=spec.project_id,
                agent_instance_id=spec.agent_instance_id,
                title=spec.title,
                goal=spec.goal,
                input_text=spec.input_text,
                resource_refs=list(spec.resource_refs),
                acceptance_criteria=list(spec.acceptance_criteria),
                max_duration_minutes=spec.max_duration_minutes,
                max_child_runs=spec.max_child_runs,
            )
        )
        await session.flush()
        session.add_all(
            TaskRevisionDependencyTable(
                owner_id=owner_id,
                task_id=task_id,
                revision=revision,
                blocked_by_task_id=dependency_id,
            )
            for dependency_id in spec.blocked_by_task_ids
        )

    async def _get(
        self, session: AsyncSession, owner_id: str, task_id: UUID
    ) -> Task | None:
        rows = (
            await session.execute(
                _task_query(current_only=True).where(
                    TaskTable.owner_id == owner_id,
                    TaskTable.id == task_id,
                )
            )
        ).all()
        mapped = await _map_rows(session, owner_id, rows)
        return mapped[0] if mapped else None

    async def create(self, owner_id: str, spec: TaskSpec) -> Task:
        task_id = uuid4()
        async with self._sessions.begin() as session:
            raw = RawSQLExecutor(session)
            await self._lock_owner(raw, owner_id)
            await self._check_spec(session, owner_id, spec)
            session.add(
                TaskTable(
                    id=task_id,
                    owner_id=owner_id,
                    current_revision=1,
                )
            )
            await session.flush()
            await self._insert_revision(session, owner_id, task_id, 1, spec)
            await session.flush()
            task = await self._get(session, owner_id, task_id)
            if task is None:
                raise RuntimeError("created task could not be read")
            return task

    async def list_for_owner(
        self, owner_id: str, *, project_id: UUID | None, limit: int, offset: int
    ) -> list[Task]:
        statement = (
            _task_query(current_only=True)
            .where(TaskTable.owner_id == owner_id)
            .order_by(TaskTable.created_at.desc(), TaskTable.id)
            .limit(limit)
            .offset(offset)
        )
        if project_id is not None:
            statement = statement.where(TaskRevisionTable.project_id == project_id)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return await _map_rows(session, owner_id, rows)

    async def get_for_owner(self, owner_id: str, task_id: UUID) -> Task | None:
        async with self._sessions() as session:
            return await self._get(session, owner_id, task_id)

    async def list_revisions_for_owner(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Task] | None:
        async with self._sessions() as session:
            exists = await session.scalar(
                select(TaskTable.id).where(
                    TaskTable.owner_id == owner_id,
                    TaskTable.id == task_id,
                )
            )
            if exists is None:
                return None
            rows = (
                await session.execute(
                    _task_query(current_only=False)
                    .where(
                        TaskTable.owner_id == owner_id,
                        TaskTable.id == task_id,
                    )
                    .order_by(TaskRevisionTable.revision)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return await _map_rows(session, owner_id, rows)

    async def update_for_owner(
        self, owner_id: str, task_id: UUID, spec: TaskSpec
    ) -> Task | None:
        async with self._sessions.begin() as session:
            raw = RawSQLExecutor(session)
            await self._lock_owner(raw, owner_id)
            current = await self._get(session, owner_id, task_id)
            if current is None:
                return None
            await self._check_spec(session, owner_id, spec)
            await self._check_cycle(raw, owner_id, task_id, spec.blocked_by_task_ids)
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
            stable = await session.scalar(
                select(TaskTable)
                .where(TaskTable.owner_id == owner_id, TaskTable.id == task_id)
                .with_for_update()
            )
            if stable is None:
                return None
            next_revision = current.revision + 1
            stable.current_revision = next_revision
            stable.updated_at = func.now()
            await self._insert_revision(session, owner_id, task_id, next_revision, spec)
            await session.flush()
            updated = await self._get(session, owner_id, task_id)
            if updated is None:
                raise RuntimeError("updated task could not be read")
            return updated
