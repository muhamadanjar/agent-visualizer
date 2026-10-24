from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg

MAX_SKILL_BYTES = 1_048_576
MAX_RESOURCES = 10
MAX_RESOURCE_BYTES = 100_000
MAX_DEPENDENCIES = 100
MAX_DEPENDENCY_LENGTH = 200
MAX_RESOURCE_PATH_LENGTH = 256


class SkillInputError(ValueError):
    """Skill content is invalid or exceeds the supported text-only format."""


@dataclass(frozen=True, slots=True)
class TextResource:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class SkillContent:
    name: str
    description: str
    instructions: str
    dependencies: tuple[str, ...]
    resources: tuple[TextResource, ...]


@dataclass(frozen=True, slots=True)
class Skill:
    id: UUID
    owner_id: str
    version: int
    name: str
    description: str
    instructions: str
    dependencies: tuple[str, ...]
    resources: tuple[TextResource, ...]
    archived_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SkillSummary:
    id: UUID
    owner_id: str
    version: int
    name: str
    description: str
    dependencies: tuple[str, ...]
    archived_at: datetime | None
    created_at: datetime


class SkillRepository(Protocol):
    async def create(self, owner_id: str, content: SkillContent) -> Skill: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[SkillSummary]: ...

    async def get_for_owner(
        self, owner_id: str, skill_id: UUID, *, version: int | None = None
    ) -> Skill | None: ...

    async def update_for_owner(
        self, owner_id: str, skill_id: UUID, content: SkillContent
    ) -> Skill | None: ...

    async def archive_for_owner(self, owner_id: str, skill_id: UUID) -> Skill | None: ...


def _slug(name: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return result or "skill"


def normalize_skill_content(
    name: str,
    description: str,
    instructions: str,
    dependencies: list[str],
    resources: list[dict[str, str]],
) -> SkillContent:
    normalized_name = name.strip()
    normalized_description = description.strip()
    if not normalized_name:
        raise SkillInputError("name must not be blank")
    if len(normalized_name) > 120:
        raise SkillInputError("name must be at most 120 characters")
    if len(normalized_description) > 500:
        raise SkillInputError("description must be at most 500 characters")
    if not instructions or not instructions.strip() or "\x00" in instructions:
        raise SkillInputError("instructions must be non-empty text without NUL bytes")
    try:
        total_bytes = len(instructions.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise SkillInputError("instructions must be valid UTF-8 text") from error
    if len(dependencies) > MAX_DEPENDENCIES:
        raise SkillInputError("too many declared dependencies")
    normalized_dependencies: list[str] = []
    seen_dependencies: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, str) or "\x00" in dependency:
            raise SkillInputError("dependencies must be text")
        selected = dependency.strip()
        if not selected or len(selected) > MAX_DEPENDENCY_LENGTH:
            raise SkillInputError("dependencies must contain 1 to 200 characters")
        if selected in seen_dependencies:
            raise SkillInputError("dependencies cannot be duplicated")
        seen_dependencies.add(selected)
        normalized_dependencies.append(selected)
    if len(resources) > MAX_RESOURCES:
        raise SkillInputError("too many text resources")
    normalized_resources: list[TextResource] = []
    seen_paths: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict) or set(resource) != {"path", "content"}:
            raise SkillInputError("each text resource needs a path and content")
        path = resource["path"]
        content = resource["content"]
        if not isinstance(path, str) or not isinstance(content, str):
            raise SkillInputError("resource paths and contents must be text")
        if (
            not path
            or len(path) > MAX_RESOURCE_PATH_LENGTH
            or "\x00" in path
            or "\\" in path
            or path.startswith("/")
            or re.match(r"^[A-Za-z]:", path)
        ):
            raise SkillInputError("resource path must be a relative POSIX path")
        posix_path = PurePosixPath(path)
        if (
            not posix_path.parts
            or posix_path.as_posix() != path
            or any(part in (".", "..") for part in posix_path.parts)
        ):
            raise SkillInputError("resource path must be canonical and cannot traverse directories")
        if path in seen_paths:
            raise SkillInputError("resource paths cannot be duplicated")
        seen_paths.add(path)
        if "\x00" in content:
            raise SkillInputError("resource content cannot contain NUL bytes")
        try:
            content_bytes = len(content.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise SkillInputError("resource content must be valid UTF-8 text") from error
        if content_bytes > MAX_RESOURCE_BYTES:
            raise SkillInputError("a text resource exceeds the 100,000-byte limit")
        total_bytes += content_bytes
        if total_bytes > MAX_SKILL_BYTES:
            raise SkillInputError("instructions and text resources exceed the 1 MiB limit")
        normalized_resources.append(TextResource(path, content))
    if total_bytes > MAX_SKILL_BYTES:
        raise SkillInputError("instructions and text resources exceed the 1 MiB limit")
    return SkillContent(
        normalized_name,
        normalized_description,
        instructions,
        tuple(normalized_dependencies),
        tuple(normalized_resources),
    )


def _decode_json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _decode_dependencies(value: object) -> tuple[str, ...]:
    decoded = _decode_json(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise RuntimeError("stored skill dependencies are invalid")
    return tuple(decoded)


def _decode_resources(value: object) -> tuple[TextResource, ...]:
    decoded = _decode_json(value)
    if not isinstance(decoded, list):
        raise RuntimeError("stored skill resources are invalid")
    resources: list[TextResource] = []
    for item in decoded:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise RuntimeError("stored skill resource is invalid")
        resources.append(TextResource(item["path"], item["content"]))
    return tuple(resources)


def _skill(row: asyncpg.Record) -> Skill:
    return Skill(
        id=row["id"],
        owner_id=row["owner_id"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        instructions=row["instructions"],
        dependencies=_decode_dependencies(row["dependencies"]),
        resources=_decode_resources(row["resources"]),
        archived_at=row["archived_at"],
        created_at=row["created_at"],
    )


def _summary(row: asyncpg.Record) -> SkillSummary:
    return SkillSummary(
        id=row["id"],
        owner_id=row["owner_id"],
        version=row["version"],
        name=row["name"],
        description=row["description"],
        dependencies=_decode_dependencies(row["dependencies"]),
        archived_at=row["archived_at"],
        created_at=row["created_at"],
    )


def _content_values(content: SkillContent) -> tuple[str, str, str, str, str]:
    return (
        content.name,
        content.description,
        content.instructions,
        json.dumps(list(content.dependencies), ensure_ascii=False, allow_nan=False),
        json.dumps([{"path": item.path, "content": item.content} for item in content.resources], ensure_ascii=False, allow_nan=False),
    )


class PostgresSkillRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, owner_id: str, content: SkillContent) -> Skill:
        skill_id = uuid4()
        values = _content_values(content)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO skills (id, owner_id, slug) VALUES ($1, $2, $3)",
                    skill_id,
                    owner_id,
                    _slug(content.name),
                )
                await connection.execute(
                    """
                    INSERT INTO skill_versions
                        (owner_id, skill_id, version, name, description, instructions, dependencies, resources)
                    VALUES ($1, $2, 1, $3, $4, $5, $6::jsonb, $7::jsonb)
                    """,
                    owner_id,
                    skill_id,
                    *values,
                )
        skill = await self.get_for_owner(owner_id, skill_id)
        if skill is None:
            raise RuntimeError("created skill could not be read")
        return skill

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[SkillSummary]:
        rows = await self._pool.fetch(
            """
            SELECT s.id, s.owner_id, s.archived_at, v.version, v.name, v.description,
                   v.dependencies, v.created_at
            FROM skills s
            JOIN skill_versions v
              ON v.owner_id = s.owner_id AND v.skill_id = s.id AND v.version = s.current_version
            WHERE s.owner_id = $1 AND ($2 OR s.archived_at IS NULL)
            ORDER BY s.created_at, s.id
            LIMIT $3 OFFSET $4
            """,
            owner_id,
            include_archived,
            limit,
            offset,
        )
        return [_summary(row) for row in rows]

    async def get_for_owner(
        self, owner_id: str, skill_id: UUID, *, version: int | None = None
    ) -> Skill | None:
        row = await self._pool.fetchrow(
            """
            SELECT s.id, s.owner_id, s.archived_at, v.version, v.name, v.description,
                   v.instructions, v.dependencies, v.resources, v.created_at
            FROM skills s
            JOIN skill_versions v ON v.owner_id = s.owner_id AND v.skill_id = s.id
            WHERE s.owner_id = $1 AND s.id = $2
              AND (($3::integer IS NULL AND v.version = s.current_version) OR v.version = $3)
            """,
            owner_id,
            skill_id,
            version,
        )
        return _skill(row) if row is not None else None

    async def update_for_owner(
        self, owner_id: str, skill_id: UUID, content: SkillContent
    ) -> Skill | None:
        values = _content_values(content)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT s.current_version, s.archived_at, v.name, v.description,
                           v.instructions, v.dependencies, v.resources, v.created_at
                    FROM skills s
                    JOIN skill_versions v
                      ON v.owner_id = s.owner_id AND v.skill_id = s.id AND v.version = s.current_version
                    WHERE s.owner_id = $1 AND s.id = $2
                    FOR UPDATE OF s
                    """,
                    owner_id,
                    skill_id,
                )
                if current is None or current["archived_at"] is not None:
                    return None
                existing = (
                    current["name"],
                    current["description"],
                    current["instructions"],
                    json.dumps(list(_decode_dependencies(current["dependencies"])), ensure_ascii=False),
                    json.dumps([{"path": item.path, "content": item.content} for item in _decode_resources(current["resources"])], ensure_ascii=False),
                )
                if existing != values:
                    version = current["current_version"] + 1
                    await connection.execute(
                        """
                        INSERT INTO skill_versions
                            (owner_id, skill_id, version, name, description, instructions, dependencies, resources)
                        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
                        """,
                        owner_id,
                        skill_id,
                        version,
                        *values,
                    )
                    await connection.execute(
                        "UPDATE skills SET current_version = $3, slug = $4, updated_at = now() WHERE owner_id = $1 AND id = $2",
                        owner_id,
                        skill_id,
                        version,
                        _slug(content.name),
                    )
        return await self.get_for_owner(owner_id, skill_id)

    async def archive_for_owner(self, owner_id: str, skill_id: UUID) -> Skill | None:
        await self._pool.execute(
            "UPDATE skills SET archived_at = COALESCE(archived_at, now()), updated_at = now() WHERE owner_id = $1 AND id = $2",
            owner_id,
            skill_id,
        )
        return await self.get_for_owner(owner_id, skill_id)
