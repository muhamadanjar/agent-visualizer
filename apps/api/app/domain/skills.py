from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

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
