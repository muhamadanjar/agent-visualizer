from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

import yaml

from app.domain.common import SkillPin, SkillReference

MAX_SOURCE_BYTES = 1_048_576
MAX_YAML_ALIASES = 0
MAX_METADATA_DEPTH = 64
MAX_YAML_NODES = 20_000
FRONT_MATTER = re.compile(r"\A\ufeff?---\r?\n(?P<metadata>[\s\S]*?)\r?\n---(?:\r?\n|\Z)")


class LimitedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._alias_count = 0
        self._depth = 0
        self._node_count = 0

    def compose_node(self, parent: object, index: object) -> object:
        if self.check_event(yaml.AliasEvent):
            self._alias_count += 1
            if self._alias_count > MAX_YAML_ALIASES:
                raise yaml.YAMLError("YAML aliases are not supported")
        self._node_count += 1
        if self._node_count > MAX_YAML_NODES:
            raise yaml.YAMLError("front matter contains too many nodes")
        self._depth += 1
        if self._depth > MAX_METADATA_DEPTH:
            self._depth -= 1
            raise yaml.YAMLError("front matter nesting is too deep")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


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
    skills: tuple[SkillReference, ...] = ()

class AgentDefinitionInputError(ValueError):
    """Definition source or metadata is unsafe or invalid."""


def _json_value(value: object, active: set[int] | None = None, depth: int = 0) -> object:
    if depth > MAX_METADATA_DEPTH:
        raise AgentDefinitionInputError("front matter nesting is too deep")
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
            return [_json_value(item, active, depth + 1) for item in value]
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
            return {key: _json_value(item, active, depth + 1) for key, item in value.items()}
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
    try:
        source_size = len(source_markdown.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise AgentDefinitionInputError("source Markdown must be valid UTF-8 text") from error
    if source_size > MAX_SOURCE_BYTES:
        raise AgentDefinitionInputError("source Markdown exceeds the 1 MiB limit")
    front_matter = FRONT_MATTER.match(source_markdown)
    metadata: dict[str, object] = {}
    parsed_name: str | None = None
    parsed_description: str | None = None
    if front_matter:
        try:
            parsed = yaml.load(front_matter.group("metadata"), Loader=LimitedSafeLoader)
        except MemoryError:
            raise
        except Exception as error:
            raise AgentDefinitionInputError(
                "front matter must be valid safe YAML within resource limits"
            ) from error
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

    async def set_skills_for_owner(
        self, owner_id: str, definition_id: UUID, pins: list[SkillPin]
    ) -> AgentDefinition | None: ...
