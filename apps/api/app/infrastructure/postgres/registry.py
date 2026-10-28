from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent_configuration import AgentDefinitionConfiguration
from app.domain.common import SkillPin, SkillReference
from app.domain.registry import (
    AgentDefinition,
    AgentDefinitionInputError,
    AgentRegistryConflictError,
    _slug,
)
from app.infrastructure.postgres.errors import translate_unique_violation
from app.infrastructure.postgres.models import (
    AgentDefinitionTable,
    AgentDefinitionVersionSkillTable,
    AgentDefinitionVersionTable,
    ModelEndpointTable,
    SkillTable,
    SkillVersionTable,
)


def _definition(
    stable: AgentDefinitionTable,
    version: AgentDefinitionVersionTable,
    skills: tuple[SkillReference, ...] = (),
    *,
    summary: bool = False,
) -> AgentDefinition:
    return AgentDefinition(
        id=stable.id,
        owner_id=stable.owner_id,
        version=version.version,
        name=version.name,
        description=version.description,
        source_markdown="" if summary else version.source_markdown,
        unmapped_metadata={} if summary else dict(version.unmapped_metadata),
        configuration=AgentDefinitionConfiguration.from_json(version.configuration),
        enabled=stable.enabled,
        archived_at=stable.archived_at,
        created_at=version.created_at,
        skills=skills,
    )


class PostgresAgentRegistryRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _lock_active_endpoint(
        self, session: AsyncSession, owner_id: str, endpoint_id: UUID
    ) -> None:
        endpoint = await session.scalar(
            select(ModelEndpointTable)
            .where(
                ModelEndpointTable.owner_id == owner_id,
                ModelEndpointTable.id == endpoint_id,
                ModelEndpointTable.archived_at.is_(None),
            )
            .with_for_update(read=True)
        )
        if endpoint is None:
            raise AgentRegistryConflictError(
                "Configure an active Model Endpoint before saving this Definition"
            )

    async def _get_for_owner(
        self,
        session: AsyncSession,
        owner_id: str,
        definition_id: UUID,
        version: int | None = None,
    ) -> AgentDefinition | None:
        statement = (
            select(AgentDefinitionTable, AgentDefinitionVersionTable)
            .join(
                AgentDefinitionVersionTable,
                (AgentDefinitionVersionTable.definition_id == AgentDefinitionTable.id)
                & (
                    AgentDefinitionVersionTable.version
                    == (
                        AgentDefinitionTable.current_version
                        if version is None
                        else version
                    )
                ),
            )
            .where(
                AgentDefinitionTable.owner_id == owner_id,
                AgentDefinitionTable.id == definition_id,
            )
        )
        result = (await session.execute(statement)).one_or_none()
        if result is None:
            return None
        stable, definition_version = result
        pins = await session.execute(
            select(
                AgentDefinitionVersionSkillTable.skill_id,
                AgentDefinitionVersionSkillTable.skill_version,
                SkillVersionTable.name,
            )
            .join(
                SkillVersionTable,
                (SkillVersionTable.owner_id == AgentDefinitionVersionSkillTable.owner_id)
                & (SkillVersionTable.skill_id == AgentDefinitionVersionSkillTable.skill_id)
                & (SkillVersionTable.version == AgentDefinitionVersionSkillTable.skill_version),
            )
            .where(
                AgentDefinitionVersionSkillTable.owner_id == owner_id,
                AgentDefinitionVersionSkillTable.definition_id == definition_id,
                AgentDefinitionVersionSkillTable.definition_version == definition_version.version,
            )
            .order_by(AgentDefinitionVersionSkillTable.position)
        )
        skill_refs = tuple(
            SkillReference(row.skill_id, row.skill_version, row.name) for row in pins
        )
        return _definition(stable, definition_version, skill_refs)

    @translate_unique_violation
    async def create(
        self,
        owner_id: str,
        name: str,
        description: str,
        source_markdown: str,
        unmapped_metadata: dict[str, object],
        configuration: AgentDefinitionConfiguration | None = None,
    ) -> AgentDefinition:
        definition_id = uuid4()
        stable = AgentDefinitionTable(
            id=definition_id,
            owner_id=owner_id,
            slug=_slug(name),
            current_version=1,
            enabled=configuration is not None,
        )
        version = AgentDefinitionVersionTable(
            owner_id=owner_id,
            definition_id=definition_id,
            version=1,
            name=name,
            description=description,
            source_markdown=source_markdown,
            unmapped_metadata=unmapped_metadata,
            configuration=None if configuration is None else configuration.to_json(),
        )
        async with self._sessions.begin() as session:
            if configuration is not None:
                await self._lock_active_endpoint(
                    session, owner_id, configuration.model_endpoint_id
                )
            session.add_all([stable, version])
            await session.flush()
            return _definition(stable, version)

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[AgentDefinition]:
        statement = (
            select(AgentDefinitionTable, AgentDefinitionVersionTable)
            .join(
                AgentDefinitionVersionTable,
                (AgentDefinitionVersionTable.definition_id == AgentDefinitionTable.id)
                & (AgentDefinitionVersionTable.version == AgentDefinitionTable.current_version),
            )
            .where(AgentDefinitionTable.owner_id == owner_id)
            .order_by(AgentDefinitionTable.created_at, AgentDefinitionTable.id)
            .limit(limit)
            .offset(offset)
        )
        if not include_archived:
            statement = statement.where(AgentDefinitionTable.archived_at.is_(None))
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return [
                _definition(stable, version, summary=True)
                for stable, version in rows
            ]

    async def get_for_owner(
        self, owner_id: str, definition_id: UUID, *, version: int | None = None
    ) -> AgentDefinition | None:
        async with self._sessions() as session:
            return await self._get_for_owner(session, owner_id, definition_id, version)

    @translate_unique_violation
    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> AgentDefinition | None:
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(AgentDefinitionTable)
                .where(
                    AgentDefinitionTable.owner_id == owner_id,
                    AgentDefinitionTable.id == definition_id,
                )
                .with_for_update()
            )
            if stable is None or stable.archived_at is not None:
                return None
            configuration = changes.get("configuration")
            if isinstance(configuration, AgentDefinitionConfiguration):
                await self._lock_active_endpoint(
                    session, owner_id, configuration.model_endpoint_id
                )
            current = await session.scalar(
                select(AgentDefinitionVersionTable).where(
                    AgentDefinitionVersionTable.owner_id == owner_id,
                    AgentDefinitionVersionTable.definition_id == definition_id,
                    AgentDefinitionVersionTable.version == stable.current_version,
                )
            )
            if current is None:
                raise RuntimeError("current agent-definition version is missing")
            links = (
                await session.scalars(
                    select(AgentDefinitionVersionSkillTable).where(
                        AgentDefinitionVersionSkillTable.owner_id == owner_id,
                        AgentDefinitionVersionSkillTable.definition_id == definition_id,
                        AgentDefinitionVersionSkillTable.definition_version == current.version,
                    ).order_by(AgentDefinitionVersionSkillTable.position)
                )
            ).all()
            values = {
                "name": changes.get("name", current.name),
                "description": changes.get("description", current.description),
                "source_markdown": changes.get("source_markdown", current.source_markdown),
                "unmapped_metadata": changes.get(
                    "unmapped_metadata", current.unmapped_metadata
                ),
                "configuration": (
                    changes["configuration"].to_json()
                    if "configuration" in changes
                    and isinstance(changes["configuration"], AgentDefinitionConfiguration)
                    else current.configuration
                ),
            }
            next_version = AgentDefinitionVersionTable(
                owner_id=owner_id,
                definition_id=definition_id,
                version=stable.current_version + 1,
                **values,
            )
            session.add(next_version)
            await session.flush()
            for link in links:
                session.add(
                    AgentDefinitionVersionSkillTable(
                        owner_id=owner_id,
                        definition_id=definition_id,
                        definition_version=next_version.version,
                        skill_id=link.skill_id,
                        skill_version=link.skill_version,
                        position=link.position,
                    )
                )
            stable.current_version = next_version.version
            stable.slug = _slug(str(values["name"]))
            stable.updated_at = func.now()
            await session.flush()
            return await self._get_for_owner(session, owner_id, definition_id)

    async def set_enabled_for_owner(
        self, owner_id: str, definition_id: UUID, enabled: bool
    ) -> AgentDefinition | None:
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(AgentDefinitionTable)
                .where(
                    AgentDefinitionTable.owner_id == owner_id,
                    AgentDefinitionTable.id == definition_id,
                )
                .with_for_update()
            )
            if stable is None or stable.archived_at is not None:
                return None
            if enabled:
                current_version = await session.scalar(
                    select(AgentDefinitionVersionTable).where(
                        AgentDefinitionVersionTable.owner_id == owner_id,
                        AgentDefinitionVersionTable.definition_id == definition_id,
                        AgentDefinitionVersionTable.version == stable.current_version,
                    )
                )
                configuration = (
                    None
                    if current_version is None
                    else AgentDefinitionConfiguration.from_json(
                        current_version.configuration
                    )
                )
                if configuration is None:
                    raise AgentDefinitionInputError(
                        "Configure the Agent Definition before enabling it"
                    )
                await self._lock_active_endpoint(
                    session, owner_id, configuration.model_endpoint_id
                )
            if stable.enabled != enabled:
                stable.enabled = enabled
                stable.updated_at = func.now()
                await session.flush()
            return await self._get_for_owner(session, owner_id, definition_id)

    async def set_skills_for_owner(
        self, owner_id: str, definition_id: UUID, pins: list[SkillPin]
    ) -> AgentDefinition | None:
        if len(pins) > 100 or len({pin.skill_id for pin in pins}) != len(pins):
            raise AgentDefinitionInputError("a definition can pin up to 100 distinct skills")
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(AgentDefinitionTable)
                .where(
                    AgentDefinitionTable.owner_id == owner_id,
                    AgentDefinitionTable.id == definition_id,
                )
                .with_for_update()
            )
            if stable is None or stable.archived_at is not None:
                return None
            current = await session.scalar(
                select(AgentDefinitionVersionTable).where(
                    AgentDefinitionVersionTable.owner_id == owner_id,
                    AgentDefinitionVersionTable.definition_id == definition_id,
                    AgentDefinitionVersionTable.version == stable.current_version,
                )
            )
            if current is None:
                raise RuntimeError("current agent-definition version is missing")
            current_rows = (
                await session.scalars(
                    select(AgentDefinitionVersionSkillTable)
                    .where(
                        AgentDefinitionVersionSkillTable.owner_id == owner_id,
                        AgentDefinitionVersionSkillTable.definition_id == definition_id,
                        AgentDefinitionVersionSkillTable.definition_version == current.version,
                    )
                    .order_by(AgentDefinitionVersionSkillTable.position)
                )
            ).all()
            current_pins = [
                SkillPin(row.skill_id, row.skill_version) for row in current_rows
            ]
            if current_pins == pins:
                return await self._get_for_owner(session, owner_id, definition_id)

            if pins:
                requested_pairs = [(pin.skill_id, pin.version) for pin in pins]
                valid = (
                    await session.execute(
                        select(
                            SkillTable.id, SkillVersionTable.version, SkillVersionTable.name
                        )
                        .join(
                            SkillVersionTable,
                            (SkillVersionTable.owner_id == SkillTable.owner_id)
                            & (SkillVersionTable.skill_id == SkillTable.id),
                        )
                        .where(
                            SkillTable.owner_id == owner_id,
                            SkillTable.archived_at.is_(None),
                            tuple_(SkillTable.id, SkillVersionTable.version).in_(requested_pairs),
                        )
                        .with_for_update(read=True, of=SkillTable)
                    )
                ).all()
                valid_skill_names = {
                    (row.id, row.version): row.name for row in valid
                }
                if len(valid_skill_names) != len(pins):
                    raise AgentDefinitionInputError(
                        "pinned skills must be active versions owned by this user"
                    )

            else:
                valid_skill_names = {}

            next_version = AgentDefinitionVersionTable(
                owner_id=owner_id,
                definition_id=definition_id,
                version=stable.current_version + 1,
                name=current.name,
                description=current.description,
                source_markdown=current.source_markdown,
                unmapped_metadata=current.unmapped_metadata,
                configuration=current.configuration,
            )
            session.add(next_version)
            await session.flush()
            for position, pin in enumerate(pins):
                if (pin.skill_id, pin.version) not in valid_skill_names:
                    raise AgentDefinitionInputError(
                        "pinned skills must be active versions owned by this user"
                    )
                session.add(
                    AgentDefinitionVersionSkillTable(
                        owner_id=owner_id,
                        definition_id=definition_id,
                        definition_version=next_version.version,
                        skill_id=pin.skill_id,
                        skill_version=pin.version,
                        position=position,
                    )
                )
            stable.current_version = next_version.version
            stable.updated_at = func.now()
            await session.flush()
            return await self._get_for_owner(session, owner_id, definition_id)

    async def archive_for_owner(
        self, owner_id: str, definition_id: UUID
    ) -> AgentDefinition | None:
        async with self._sessions.begin() as session:
            stable = await session.scalar(
                select(AgentDefinitionTable).where(
                    AgentDefinitionTable.owner_id == owner_id,
                    AgentDefinitionTable.id == definition_id,
                )
            )
            if stable is None:
                return None
            now = func.now()
            if stable.archived_at is None:
                stable.archived_at = now
            stable.updated_at = now
            await session.flush()
            return await self._get_for_owner(session, owner_id, definition_id)
