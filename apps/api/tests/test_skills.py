from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from test_api import (
    MemoryEmptyModelEndpointRepository,
    MemoryProjectRepository,
    ORIGIN,
    database_ready,
    login,
    settings,
)
from app.domain.registry import AgentDefinitionInputError
from app.domain.skills import TextResource


@dataclass(frozen=True)
class MemorySkillVersion:
    id: UUID
    owner_id: str
    version: int
    name: str
    description: str
    instructions: str
    dependencies: list[str]
    resources: list[TextResource]
    archived_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class MemorySkillReference:
    skill_id: UUID
    version: int
    name: str


@dataclass(frozen=True)
class MemoryDefinition:
    id: UUID
    owner_id: str
    version: int
    name: str
    description: str
    source_markdown: str
    unmapped_metadata: dict[str, object]
    configuration: object | None
    enabled: bool
    archived_at: datetime | None
    created_at: datetime
    skills: tuple[MemorySkillReference, ...] = ()


class MemorySkillRepository:
    def __init__(self) -> None:
        self.versions: dict[UUID, list[MemorySkillVersion]] = {}
        self.archived: set[UUID] = set()

    @staticmethod
    def _resources(content: object) -> list[TextResource]:
        return [
            resource
            if isinstance(resource, TextResource)
            else TextResource(resource.path, resource.content)
            if hasattr(resource, "path")
            else TextResource(resource["path"], resource["content"])
            for resource in content.resources
        ]

    async def create(self, owner_id: str, content: object) -> MemorySkillVersion:
        item = MemorySkillVersion(
            uuid4(), owner_id, 1, content.name, content.description,
            content.instructions, list(content.dependencies), self._resources(content),
            None, datetime.now(UTC),
        )
        self.versions[item.id] = [item]
        return item

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ) -> list[MemorySkillVersion]:
        items = [
            versions[-1]
            for skill_id, versions in self.versions.items()
            if versions[-1].owner_id == owner_id
            and (include_archived or skill_id not in self.archived)
        ]
        return sorted(items, key=lambda item: (item.created_at, item.id))[offset:offset + limit]

    async def get_for_owner(
        self, owner_id: str, skill_id: UUID, *, version: int | None = None
    ) -> MemorySkillVersion | None:
        versions = self.versions.get(skill_id, [])
        selected = versions[-1] if version is None and versions else next(
            (item for item in versions if item.version == version), None
        )
        if selected is None or selected.owner_id != owner_id:
            return None
        archived_at = datetime.now(UTC) if skill_id in self.archived else None
        return replace(selected, archived_at=archived_at)

    async def update_for_owner(
        self, owner_id: str, skill_id: UUID, content: object
    ) -> MemorySkillVersion | None:
        current = await self.get_for_owner(owner_id, skill_id)
        if current is None or current.archived_at is not None:
            return None
        resources = self._resources(content)
        values = (content.name, content.description, content.instructions, list(content.dependencies), resources)
        existing = (current.name, current.description, current.instructions, current.dependencies, current.resources)
        if values == existing:
            return current
        item = MemorySkillVersion(
            skill_id, owner_id, current.version + 1, *values[:3], values[3], values[4],
            None, datetime.now(UTC),
        )
        self.versions[skill_id].append(item)
        return item

    async def archive_for_owner(self, owner_id: str, skill_id: UUID) -> MemorySkillVersion | None:
        current = await self.get_for_owner(owner_id, skill_id)
        if current is None:
            return None
        self.archived.add(skill_id)
        return await self.get_for_owner(owner_id, skill_id)


class MemoryAgentRegistryRepository:
    def __init__(self, skills: MemorySkillRepository) -> None:
        self.versions: dict[UUID, list[MemoryDefinition]] = {}
        self.skill_repository = skills

    async def create(
        self, owner_id: str, name: str, description: str,
        source_markdown: str, unmapped_metadata: dict[str, object],
        configuration: object | None = None,
    ) -> MemoryDefinition:
        item = MemoryDefinition(
            uuid4(), owner_id, 1, name, description, source_markdown,
            unmapped_metadata, configuration, configuration is not None, None, datetime.now(UTC),
        )
        self.versions[item.id] = [item]
        return item

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ) -> list[MemoryDefinition]:
        items = [versions[-1] for versions in self.versions.values() if versions[-1].owner_id == owner_id]
        return sorted(items, key=lambda item: (item.created_at, item.id))[offset:offset + limit]

    async def get_for_owner(
        self, owner_id: str, definition_id: UUID, *, version: int | None = None
    ) -> MemoryDefinition | None:
        versions = self.versions.get(definition_id, [])
        selected = versions[-1] if version is None and versions else next(
            (item for item in versions if item.version == version), None
        )
        return selected if selected is not None and selected.owner_id == owner_id else None

    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> MemoryDefinition | None:
        current = await self.get_for_owner(owner_id, definition_id)
        if current is None or current.archived_at is not None:
            return None
        item = replace(current, version=current.version + 1, **changes)
        self.versions[definition_id].append(item)
        return item

    async def archive_for_owner(self, owner_id: str, definition_id: UUID) -> MemoryDefinition | None:
        return None

    async def set_skills_for_owner(
        self, owner_id: str, definition_id: UUID, pins: list[object]
    ) -> MemoryDefinition | None:
        current = await self.get_for_owner(owner_id, definition_id)
        if current is None or current.archived_at is not None:
            return None
        refs: list[MemorySkillReference] = []
        for pin in pins:
            skill = await self.skill_repository.get_for_owner(owner_id, pin.skill_id, version=pin.version)
            if skill is None or skill.archived_at is not None:
                raise AgentDefinitionInputError("skill version is not available to this owner")
            refs.append(MemorySkillReference(pin.skill_id, pin.version, skill.name))
        if [(item.skill_id, item.version) for item in current.skills] == [
            (item.skill_id, item.version) for item in refs
        ]:
            return current
        item = replace(current, version=current.version + 1, skills=tuple(refs))
        self.versions[definition_id].append(item)
        return item


def make_client() -> tuple[TestClient, MemorySkillRepository, MemoryAgentRegistryRepository]:
    skills = MemorySkillRepository()
    definitions = MemoryAgentRegistryRepository(skills)
    client = TestClient(create_app(
        settings(),
        database_check=database_ready,
        project_repository=MemoryProjectRepository(),
        agent_registry_repository=definitions,
        model_endpoint_repository=MemoryEmptyModelEndpointRepository(),
        skill_repository=skills,
    ))
    return client, skills, definitions


def valid_skill_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Research notes",
        "description": "A text-only source checklist.",
        "instructions": "# Research notes\nKeep every source traceable.\n",
        "dependencies": ["browser-read", "citation-check"],
        "resources": [{"path": "references/checklist.md", "content": "# Checklist\nCompare primary sources.\n"}],
    }
    payload.update(changes)
    return payload


def test_skill_import_versions_export_archive_and_owner_scope() -> None:
    client, repository, _ = make_client()
    with client:
        assert client.get("/api/v1/skills").status_code == 401
        assert login(client).status_code == 200
        source = valid_skill_payload()
        created = client.post("/api/v1/skills/import", json=source, headers={"origin": ORIGIN})
        assert created.status_code == 201
        original = created.json()
        skill_path = original["id"]
        assert original["version"] == 1
        assert original["instructions"] == source["instructions"]
        assert original["resources"] == source["resources"]
        assert original["dependencies"] == source["dependencies"]
        assert "owner_id" not in original

        summaries = client.get("/api/v1/skills").json()
        assert len(summaries) == 1
        assert "instructions" not in summaries[0]
        assert "resources" not in summaries[0]

        revised_payload = valid_skill_payload(
            description="Updated without rewriting version one.",
            instructions="# Research notes v2\nUse first-party sources.\n",
        )
        revised = client.put(f"/api/v1/skills/{skill_path}", json=revised_payload, headers={"origin": ORIGIN})
        assert revised.status_code == 200
        assert revised.json()["version"] == 2
        historical = client.get(f"/api/v1/skills/{skill_path}?version=1").json()
        assert historical["instructions"] == source["instructions"]
        assert historical["resources"] == source["resources"]
        exported = client.get(f"/api/v1/skills/{skill_path}/export?version=1")
        assert exported.status_code == 200
        assert exported.json()["instructions"] == source["instructions"]
        assert exported.json()["resources"] == source["resources"]

        foreign_id = uuid4()
        repository.versions[foreign_id] = [MemorySkillVersion(
            foreign_id, "another-owner", 1, "Private", "", "private instructions", [], [], None, datetime.now(UTC)
        )]
        assert client.get(f"/api/v1/skills/{foreign_id}").status_code == 404
        assert client.get(f"/api/v1/skills/{foreign_id}/export").status_code == 404

        archived = client.post(f"/api/v1/skills/{skill_path}/archive", json={}, headers={"origin": ORIGIN})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert client.get("/api/v1/skills").json() == []
        assert len(client.get("/api/v1/skills?include_archived=true").json()) == 1
        assert client.get(f"/api/v1/skills/{skill_path}?version=1").status_code == 200
        assert client.put(f"/api/v1/skills/{skill_path}", json=revised_payload, headers={"origin": ORIGIN}).status_code == 404


def test_skill_import_rejects_unsafe_paths_duplicate_resources_and_oversized_text() -> None:
    client, _, _ = make_client()
    with client:
        assert login(client).status_code == 200
        for path in ("/absolute.txt", "../secret.txt", "guide/../../secret.txt", "guide\\secret.txt", "./normalized.txt"):
            bad = valid_skill_payload(resources=[{"path": path, "content": "text"}])
            response = client.post("/api/v1/skills/import", json=bad, headers={"origin": ORIGIN})
            assert response.status_code == 422
        duplicate = valid_skill_payload(resources=[
            {"path": "notes.md", "content": "first"},
            {"path": "notes.md", "content": "second"},
        ])
        assert client.post("/api/v1/skills/import", json=duplicate, headers={"origin": ORIGIN}).status_code == 422
        oversized = valid_skill_payload(instructions="x" * 1_048_577, resources=[])
        assert client.post("/api/v1/skills/import", json=oversized, headers={"origin": ORIGIN}).status_code == 422
        assert client.get("/api/v1/skills").json() == []


def test_definition_skill_pins_are_versioned_ordered_idempotent_and_owner_scoped() -> None:
    client, skill_repository, definitions = make_client()
    with client:
        assert login(client).status_code == 200
        definition = client.post(
            "/api/v1/agent-definitions/import",
            json={"source_markdown": "---\nname: Researcher\n---\nOriginal role.\n"},
            headers={"origin": ORIGIN},
        ).json()
        skill = client.post(
            "/api/v1/skills/import",
            json=valid_skill_payload(),
            headers={"origin": ORIGIN},
        ).json()
        skill_id = UUID(skill["id"])
        skill_path = skill["id"]
        definition_id = definition["id"]
        pin_v1 = {"skill_id": skill_path, "version": 1}

        attached = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": [pin_v1]},
            headers={"origin": ORIGIN},
        )
        assert attached.status_code == 200
        assert attached.json()["version"] == 2
        assert [(item["skill_id"], item["version"]) for item in attached.json()["skills"]] == [(skill_path, 1)]

        unchanged = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": [pin_v1]},
            headers={"origin": ORIGIN},
        )
        assert unchanged.json()["version"] == 2

        updated_skill = client.put(
            f"/api/v1/skills/{skill_path}",
            json=valid_skill_payload(instructions="# v2\nNew instructions.\n"),
            headers={"origin": ORIGIN},
        ).json()
        assert updated_skill["version"] == 2
        assert client.get(f"/api/v1/agent-definitions/{definition_id}?version=2").json()["skills"][0]["version"] == 1

        definition_edit = client.put(
            f"/api/v1/agent-definitions/{definition_id}",
            json={"name": "Research specialist"},
            headers={"origin": ORIGIN},
        ).json()
        assert definition_edit["version"] == 3
        assert definition_edit["skills"][0]["version"] == 1
        assert client.get(f"/api/v1/agent-definitions/{definition_id}?version=2").json()["skills"][0]["version"] == 1

        pin_v2 = {"skill_id": skill_path, "version": 2}
        changed_pin = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": [pin_v2]},
            headers={"origin": ORIGIN},
        ).json()
        assert changed_pin["version"] == 4
        assert changed_pin["skills"][0]["version"] == 2
        assert client.get(f"/api/v1/agent-definitions/{definition_id}?version=3").json()["skills"][0]["version"] == 1

        detached = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": []},
            headers={"origin": ORIGIN},
        ).json()
        assert detached["version"] == 5
        assert detached["skills"] == []

        foreign_skill_id = uuid4()
        skill_repository.versions[foreign_skill_id] = [MemorySkillVersion(
            foreign_skill_id, "another-owner", 1, "Private", "", "private", [], [], None, datetime.now(UTC)
        )]
        foreign_pin = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": [{"skill_id": str(foreign_skill_id), "version": 1}]},
            headers={"origin": ORIGIN},
        )
        assert foreign_pin.status_code == 422
        archived_skill = client.post(f"/api/v1/skills/{skill_path}/archive", json={}, headers={"origin": ORIGIN})
        assert archived_skill.status_code == 200
        archived_pin = client.put(
            f"/api/v1/agent-definitions/{definition_id}/skills",
            json={"skills": [pin_v2]},
            headers={"origin": ORIGIN},
        )
        assert archived_pin.status_code == 422
        assert len(definitions.versions[UUID(definition_id)]) == 5
