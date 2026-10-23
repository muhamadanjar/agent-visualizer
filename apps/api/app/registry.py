from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg
import yaml

MAX_SOURCE_BYTES = 1_048_576
MAX_YAML_ALIASES = 50
FRONT_MATTER = re.compile(r"\A\ufeff?---\r?\n(?P<metadata>[\s\S]*?)\r?\n---(?:\r?\n|\Z)")


class LimitedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._alias_count = 0

    def compose_node(self, parent: object, index: object) -> object:
        if self.check_event(yaml.AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAX_YAML_ALIASES:
                raise yaml.YAMLError("front matter contains too many YAML aliases")
        return super().compose_node(parent, index)


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    id: UUID
    owner_id: str
    version: int
    name: str

    description: str
    source_markdown: str
    unmapped_metadata: dict[str, object]
    archived_at: datetime | None
    created_at: datetime

class AgentDefinitionInputError(ValueError):
    """Definition source or metadata is unsafe or invalid."""


def _json_value(value: object, active: set[int] | None = None) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        raise AgentDefinitionInputError("front matter numbers must be finite")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        active = active if active is not None else set()
        identity = id(value)
        if identity in active:
            raise AgentDefinitionInputError("front matter cannot contain recursive aliases")
        active.add(identity)
        try:
            return [_json_value(item, active) for item in value]
        finally:
            active.remove(identity)
    if isinstance(value, dict):
        active = active if active is not None else set()
        identity = id(value)
        if identity in active:
            raise AgentDefinitionInputError("front matter cannot contain recursive aliases")
        active.add(identity)
        try:
            if any(not isinstance(key, str) for key in value):
                raise AgentDefinitionInputError("front matter keys must be strings")
            return {key: _json_value(item, active) for key, item in value.items()}
        finally:
            active.remove(identity)
    raise AgentDefinitionInputError("front matter contains a value that cannot be preserved as JSON data")


def parse_agent_markdown(
    source_markdown: str,
    *,
    name: str | None = None,
    description: str | None = None,
    fallback_name: str | None = None,
    fallback_description: str = "",
) -> tuple[str, str, dict[str, object]]:
    if not source_markdown or "\x00" in source_markdown:
        raise AgentDefinitionInputError("source Markdown must be non-empty text without NUL bytes")
    if len(source_markdown.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise AgentDefinitionInputError("source Markdown exceeds the 1 MiB limit")
    front_matter = FRONT_MATTER.match(source_markdown)
    metadata: dict[str, object] = {}
    parsed_name: str | None = None
    parsed_description: str | None = None
    if front_matter:
        try:
            parsed = yaml.load(front_matter.group("metadata"), Loader=LimitedSafeLoader)
        except yaml.YAMLError as error:
            raise AgentDefinitionInputError("front matter must be valid safe YAML") from error
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
            raise AgentDefinitionInputError("front matter must be a mapping with string keys")
        parsed_name = parsed.pop("name", None)
        parsed_description = parsed.pop("description", None)
        if parsed_name is not None and not isinstance(parsed_name, str):
            raise AgentDefinitionInputError("front matter name must be text")
        if parsed_description is not None and not isinstance(parsed_description, str):
            raise AgentDefinitionInputError("front matter description must be text")
        metadata = _json_value(parsed)  # type: ignore[assignment]
        if not isinstance(metadata, dict):
            raise AgentDefinitionInputError("front matter metadata must be a mapping")

    selected_name = name if name is not None else parsed_name or fallback_name
    selected_description = (
        description if description is not None else parsed_description or fallback_description
    )
    if selected_name is None or not selected_name.strip():
        raise AgentDefinitionInputError("name is required when source Markdown has no name")
    selected_name = selected_name.strip()
    selected_description = selected_description.strip()
    if len(selected_name) > 120:
        raise AgentDefinitionInputError("name must be at most 120 characters")
    if len(selected_description) > 500:
        raise AgentDefinitionInputError("description must be at most 500 characters")
    return selected_name, selected_description, metadata


def _slug(name: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return result or "definition"


def _metadata_value(value: object) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("stored definition metadata is not an object")
    return value


def _definition(row: asyncpg.Record) -> AgentDefinition:
    return AgentDefinition(
        id=row["id"],
        owner_id=row["owner_id"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        source_markdown=row["source_markdown"],
        unmapped_metadata=_metadata_value(row["unmapped_metadata"]),
        archived_at=row["archived_at"],
        created_at=row["created_at"],
    )


class AgentRegistryRepository(Protocol):
    async def create(
        self,
        owner_id: str,
        name: str,
        description: str,
        source_markdown: str,
        unmapped_metadata: dict[str, object],
    ) -> AgentDefinition: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[AgentDefinition]: ...

    async def get_for_owner(
        self, owner_id: str, definition_id: UUID, *, version: int | None = None
    ) -> AgentDefinition | None: ...

    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> AgentDefinition | None: ...

    async def archive_for_owner(
        self, owner_id: str, definition_id: UUID
    ) -> AgentDefinition | None: ...


class PostgresAgentRegistryRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self,
        owner_id: str,
        name: str,
        description: str,
        source_markdown: str,
        unmapped_metadata: dict[str, object],
    ) -> AgentDefinition:
        definition_id = uuid4()
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO agent_definitions (id, owner_id, slug) VALUES ($1, $2, $3)",
                    definition_id,
                    owner_id,
                    _slug(name),
                )
                row = await connection.fetchrow(
                    """
                    INSERT INTO agent_definition_versions
                        (definition_id, version, name, description, source_markdown, unmapped_metadata)
                    VALUES ($1, 1, $2, $3, $4, $5::jsonb)
                    RETURNING definition_id AS id, 1 AS version, name, description,
                              source_markdown, unmapped_metadata, created_at
                    """,
                    definition_id,
                    name,
                    description,
                    source_markdown,
                    json.dumps(unmapped_metadata, ensure_ascii=False, allow_nan=False),
                )
        if row is None:
            raise RuntimeError("agent definition insert returned no row")
        return AgentDefinition(
            id=row["id"],
            owner_id=owner_id,
            version=row["version"],
            name=row["name"],
            description=row["description"],
            source_markdown=row["source_markdown"],
            unmapped_metadata=_metadata_value(row["unmapped_metadata"]),
            archived_at=None,
            created_at=row["created_at"],
        )

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[AgentDefinition]:
        rows = await self._pool.fetch(
            """
            SELECT d.id, d.owner_id, d.archived_at, v.version, v.name, v.description,
                   ''::text AS source_markdown, '{}'::jsonb AS unmapped_metadata, v.created_at
            FROM agent_definitions d
            JOIN agent_definition_versions v
              ON v.definition_id = d.id AND v.version = d.current_version
            WHERE d.owner_id = $1 AND ($2 OR d.archived_at IS NULL)
            ORDER BY d.created_at, d.id
            LIMIT $3 OFFSET $4
            """,
            owner_id,
            include_archived,
            limit,
            offset,
        )
        return [_definition(row) for row in rows]

    async def get_for_owner(
        self, owner_id: str, definition_id: UUID, *, version: int | None = None
    ) -> AgentDefinition | None:
        row = await self._pool.fetchrow(
            """
            SELECT d.id, d.owner_id, d.archived_at, v.version, v.name, v.description,
                   v.source_markdown, v.unmapped_metadata, v.created_at
            FROM agent_definitions d
            JOIN agent_definition_versions v ON v.definition_id = d.id
            WHERE d.owner_id = $1 AND d.id = $2
              AND (($3::integer IS NULL AND v.version = d.current_version) OR v.version = $3)
            """,
            owner_id,
            definition_id,
            version,
        )
        return _definition(row) if row is not None else None

    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> AgentDefinition | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT d.current_version, d.archived_at, v.name, v.description,
                           v.source_markdown, v.unmapped_metadata
                    FROM agent_definitions d
                    JOIN agent_definition_versions v
                      ON v.definition_id = d.id AND v.version = d.current_version
                    WHERE d.owner_id = $1 AND d.id = $2
                    FOR UPDATE OF d
                    """,
                    owner_id,
                    definition_id,
                )
                if current is None or current["archived_at"] is not None:
                    return None
                version = current["current_version"] + 1
                values = {
                    "name": changes.get("name", current["name"]),
                    "description": changes.get("description", current["description"]),
                    "source_markdown": changes.get("source_markdown", current["source_markdown"]),
                    "unmapped_metadata": changes.get(
                        "unmapped_metadata", _metadata_value(current["unmapped_metadata"])
                    ),
                }
                await connection.execute(
                    """
                    INSERT INTO agent_definition_versions
                        (definition_id, version, name, description, source_markdown, unmapped_metadata)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                    """,
                    definition_id,
                    version,
                    values["name"],
                    values["description"],
                    values["source_markdown"],
                    json.dumps(values["unmapped_metadata"], ensure_ascii=False, allow_nan=False),
                )
                await connection.execute(
                    """
                    UPDATE agent_definitions
                    SET current_version = $3, slug = $4, updated_at = now()
                    WHERE owner_id = $1 AND id = $2
                    """,
                    owner_id,
                    definition_id,
                    version,
                    _slug(str(values["name"])),
                )
        return await self.get_for_owner(owner_id, definition_id)

    async def archive_for_owner(
        self, owner_id: str, definition_id: UUID
    ) -> AgentDefinition | None:
        await self._pool.execute(
            "UPDATE agent_definitions SET archived_at = COALESCE(archived_at, now()), updated_at = now() WHERE owner_id = $1 AND id = $2",
            owner_id,
            definition_id,
        )
        return await self.get_for_owner(owner_id, definition_id)
