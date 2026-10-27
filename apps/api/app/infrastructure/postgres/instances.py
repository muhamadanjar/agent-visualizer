from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.instances import (
    AgentInstance,
    AgentInstanceInputError,
    AgentInstanceRepository,
)
from app.infrastructure.postgres.models import (
    AgentDefinitionTable,
    AgentDefinitionVersionTable,
    AgentInstanceTable,
    ProjectTable,
)


def _instance(row) -> AgentInstance:
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


def _instance_query():
    return (
        select(
            AgentInstanceTable.id.label("id"),
            AgentInstanceTable.owner_id.label("owner_id"),
            AgentInstanceTable.display_name.label("display_name"),
            AgentInstanceTable.definition_id.label("definition_id"),
            AgentDefinitionVersionTable.name.label("definition_name"),
            AgentInstanceTable.project_id.label("project_id"),
            ProjectTable.name.label("project_name"),
            AgentInstanceTable.archived_at.label("archived_at"),
            AgentInstanceTable.created_at.label("created_at"),
            AgentInstanceTable.updated_at.label("updated_at"),
        )
        .select_from(AgentInstanceTable)
        .join(
            AgentDefinitionTable,
            (AgentDefinitionTable.owner_id == AgentInstanceTable.owner_id)
            & (AgentDefinitionTable.id == AgentInstanceTable.definition_id),
        )
        .join(
            AgentDefinitionVersionTable,
            (AgentDefinitionVersionTable.definition_id == AgentDefinitionTable.id)
            & (AgentDefinitionVersionTable.version == AgentDefinitionTable.current_version),
        )
        .outerjoin(
            ProjectTable,
            (ProjectTable.owner_id == AgentInstanceTable.owner_id)
            & (ProjectTable.id == AgentInstanceTable.project_id),
        )
    )


class PostgresAgentInstanceRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _get(
        self, session: AsyncSession, owner_id: str, instance_id: UUID
    ) -> AgentInstance | None:
        row = (
            await session.execute(
                _instance_query().where(
                    AgentInstanceTable.owner_id == owner_id,
                    AgentInstanceTable.id == instance_id,
                )
            )
        ).mappings().one_or_none()
        return _instance(row) if row is not None else None

    async def _check_definition(
        self, session: AsyncSession, owner_id: str, definition_id: UUID
    ) -> None:
        query = select(AgentDefinitionTable).where(
            AgentDefinitionTable.owner_id == owner_id,
            AgentDefinitionTable.id == definition_id,
        ).with_for_update(read=True)
        definition = await session.scalar(query)
        if definition is None or definition.archived_at is not None:
            raise AgentInstanceInputError("definition is unavailable")

    async def _check_project(
        self, session: AsyncSession, owner_id: str, project_id: UUID | None
    ) -> None:
        if project_id is None:
            return
        project = await session.scalar(
            select(ProjectTable.id).where(
                ProjectTable.owner_id == owner_id,
                ProjectTable.id == project_id,
            )
        )
        if project is None:
            raise AgentInstanceInputError("project is unavailable")

    async def create(
        self,
        owner_id: str,
        display_name: str,
        definition_id: UUID,
        project_id: UUID | None,
    ) -> AgentInstance:
        instance_id = uuid4()
        async with self._sessions.begin() as session:
            await self._check_definition(session, owner_id, definition_id)
            await self._check_project(session, owner_id, project_id)
            session.add(
                AgentInstanceTable(
                    id=instance_id,
                    owner_id=owner_id,
                    display_name=display_name,
                    definition_id=definition_id,
                    project_id=project_id,
                )
            )
            await session.flush()
            result = await self._get(session, owner_id, instance_id)
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
        statement = _instance_query().where(AgentInstanceTable.owner_id == owner_id)
        if not include_archived:
            statement = statement.where(AgentInstanceTable.archived_at.is_(None))
        if project_id is not None:
            statement = statement.where(AgentInstanceTable.project_id == project_id)
        statement = statement.order_by(AgentInstanceTable.created_at, AgentInstanceTable.id)
        statement = statement.limit(limit).offset(offset)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
            return [_instance(row) for row in rows]

    async def get_for_owner(self, owner_id: str, instance_id: UUID) -> AgentInstance | None:
        async with self._sessions() as session:
            return await self._get(session, owner_id, instance_id)

    async def update_for_owner(
        self, owner_id: str, instance_id: UUID, changes: dict[str, object]
    ) -> AgentInstance | None:
        allowed = {"display_name", "definition_id", "project_id"}
        if not changes or changes.keys() - allowed:
            raise ValueError("invalid agent-instance update fields")
        async with self._sessions.begin() as session:
            instance = await session.scalar(
                select(AgentInstanceTable)
                .where(
                    AgentInstanceTable.owner_id == owner_id,
                    AgentInstanceTable.id == instance_id,
                    AgentInstanceTable.archived_at.is_(None),
                )
                .with_for_update()
            )
            if instance is None:
                return None
            if (
                "definition_id" in changes
                and changes["definition_id"] != instance.definition_id
            ):
                await self._check_definition(
                    session, owner_id, changes["definition_id"]  # type: ignore[arg-type]
                )
            if "project_id" in changes:
                await self._check_project(
                    session, owner_id, changes["project_id"]  # type: ignore[arg-type]
                )
            for field, value in changes.items():
                setattr(instance, field, value)
            instance.updated_at = func.now()
            await session.flush()
            return await self._get(session, owner_id, instance_id)

    async def archive_for_owner(
        self, owner_id: str, instance_id: UUID
    ) -> AgentInstance | None:
        async with self._sessions.begin() as session:
            instance = await session.scalar(
                select(AgentInstanceTable).where(
                    AgentInstanceTable.owner_id == owner_id,
                    AgentInstanceTable.id == instance_id,
                )
            )
            if instance is None:
                return None
            if instance.archived_at is None:
                now = func.now()
                instance.archived_at = now
                instance.updated_at = now
                await session.flush()
            return await self._get(session, owner_id, instance_id)
