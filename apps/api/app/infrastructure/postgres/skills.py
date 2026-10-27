from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.skills import Skill, SkillContent, SkillSummary, TextResource, _slug
from app.infrastructure.postgres.errors import translate_unique_violation
from app.infrastructure.postgres.models import SkillTable, SkillVersionTable


def _skill(stable: SkillTable, version: SkillVersionTable) -> Skill:
    return Skill(
        id=stable.id,
        owner_id=stable.owner_id,
        version=version.version,
        name=version.name,
        description=version.description,
        instructions=version.instructions,
        dependencies=tuple(version.dependencies),
        resources=tuple(TextResource(item["path"], item["content"]) for item in version.resources),
        archived_at=stable.archived_at,
        created_at=version.created_at,
    )


def _summary(stable: SkillTable, version: SkillVersionTable) -> SkillSummary:
    return SkillSummary(
        id=stable.id,
        owner_id=stable.owner_id,
        version=version.version,
        name=version.name,
        description=version.description,
        dependencies=tuple(version.dependencies),
        archived_at=stable.archived_at,
        created_at=version.created_at,
    )


def _version_values(content: SkillContent) -> dict[str, object]:
    return {
        "name": content.name,
        "description": content.description,
        "instructions": content.instructions,
        "dependencies": list(content.dependencies),
        "resources": [
            {"path": item.path, "content": item.content} for item in content.resources
        ],
    }


class PostgresSkillRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @translate_unique_violation
    async def create(self, owner_id: str, content: SkillContent) -> Skill:
        skill_id = uuid4()
        stable = SkillTable(id=skill_id, owner_id=owner_id, slug=_slug(content.name))
        version = SkillVersionTable(
            owner_id=owner_id,
            skill_id=skill_id,
            version=1,
            **_version_values(content),
        )
        async with self._sessions.begin() as session:
            session.add_all([stable, version])
            await session.flush()
        return _skill(stable, version)

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[SkillSummary]:
        statement = (
            select(SkillTable, SkillVersionTable)
            .join(
                SkillVersionTable,
                (SkillVersionTable.owner_id == SkillTable.owner_id)
                & (SkillVersionTable.skill_id == SkillTable.id)
                & (SkillVersionTable.version == SkillTable.current_version),
            )
            .where(SkillTable.owner_id == owner_id)
            .order_by(SkillTable.created_at, SkillTable.id)
            .limit(limit)
            .offset(offset)
        )
        if not include_archived:
            statement = statement.where(SkillTable.archived_at.is_(None))
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return [_summary(stable, version) for stable, version in rows]

    async def get_for_owner(
        self, owner_id: str, skill_id: UUID, *, version: int | None = None
    ) -> Skill | None:
        statement = (
            select(SkillTable, SkillVersionTable)
            .join(
                SkillVersionTable,
                (SkillVersionTable.owner_id == SkillTable.owner_id)
                & (SkillVersionTable.skill_id == SkillTable.id),
            )
            .where(SkillTable.owner_id == owner_id, SkillTable.id == skill_id)
        )
        if version is None:
            statement = statement.where(SkillVersionTable.version == SkillTable.current_version)
        else:
            statement = statement.where(SkillVersionTable.version == version)
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
            return None if row is None else _skill(row[0], row[1])

    @translate_unique_violation
    async def update_for_owner(
        self, owner_id: str, skill_id: UUID, content: SkillContent
    ) -> Skill | None:
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(SkillTable)
                .where(SkillTable.owner_id == owner_id, SkillTable.id == skill_id)
                .with_for_update()
            )
            if stable is None or stable.archived_at is not None:
                return None
            current = await session.scalar(
                select(SkillVersionTable).where(
                    SkillVersionTable.owner_id == owner_id,
                    SkillVersionTable.skill_id == skill_id,
                    SkillVersionTable.version == stable.current_version,
                )
            )
            if current is None:
                raise RuntimeError("current skill version is missing")
            values = _version_values(content)
            unchanged = all(
                getattr(current, field) == value for field, value in values.items()
            )
            if unchanged:
                return _skill(stable, current)
            version = SkillVersionTable(
                owner_id=owner_id,
                skill_id=skill_id,
                version=stable.current_version + 1,
                **values,
            )
            session.add(version)
            stable.current_version = version.version
            stable.slug = _slug(content.name)
            stable.updated_at = func.now()
            await session.flush()
            return _skill(stable, version)

    async def archive_for_owner(self, owner_id: str, skill_id: UUID) -> Skill | None:
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(SkillTable).where(
                    SkillTable.owner_id == owner_id,
                    SkillTable.id == skill_id,
                )
            )
            if stable is None:
                return None
            if stable.archived_at is None:
                now = func.now()
                stable.archived_at = now
                stable.updated_at = now
            current = await session.scalar(
                select(SkillVersionTable).where(
                    SkillVersionTable.owner_id == owner_id,
                    SkillVersionTable.skill_id == skill_id,
                    SkillVersionTable.version == stable.current_version,
                )
            )
            if current is None:
                raise RuntimeError("current skill version is missing")
            await session.flush()
            return _skill(stable, current)
