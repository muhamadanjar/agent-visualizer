from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.projects import Project
from app.infrastructure.postgres.models import ProjectTable


class PostgresProjectRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _project(row: ProjectTable) -> Project:
        return Project(
            id=row.id,
            owner_id=row.owner_id,
            name=row.name,
            summary=row.summary,
            created_at=row.created_at,
        )

    async def create(self, owner_id: str, name: str, summary: str) -> Project:
        project = ProjectTable(id=uuid4(), owner_id=owner_id, name=name, summary=summary)
        async with self._sessions.begin() as session:
            session.add(project)
            await session.flush()
        return self._project(project)

    async def list_for_owner(
        self, owner_id: str, *, limit: int, offset: int
    ) -> list[Project]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ProjectTable)
                .where(ProjectTable.owner_id == owner_id)
                .order_by(ProjectTable.created_at, ProjectTable.id)
                .limit(limit)
                .offset(offset)
            )
            return [self._project(row) for row in rows]

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProjectTable).where(
                    ProjectTable.owner_id == owner_id,
                    ProjectTable.id == project_id,
                )
            )
            return self._project(row) if row is not None else None

