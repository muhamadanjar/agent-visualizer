from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SkillPin:
    skill_id: UUID
    version: int


@dataclass(frozen=True, slots=True)
class SkillReference:
    skill_id: UUID
    version: int
    name: str
