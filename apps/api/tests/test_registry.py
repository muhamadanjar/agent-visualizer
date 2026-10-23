from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.registry import AgentDefinition
from test_api import MemoryProjectRepository, ORIGIN, database_ready, login, settings


class MemoryAgentRegistryRepository:
    def __init__(self) -> None:
        self.versions: dict[UUID, list[AgentDefinition]] = {}
        self.archived_at: dict[UUID, datetime] = {}

    async def create(
        self, owner_id: str, name: str, description: str, source_markdown: str, unmapped_metadata: dict[str, object]
    ) -> AgentDefinition:
        now = datetime.now(UTC)
        definition = AgentDefinition(uuid4(), owner_id, 1, name, description, source_markdown, unmapped_metadata, None, now)
        self.versions[definition.id] = [definition]
        return definition

    async def list_for_owner(self, owner_id: str, *, include_archived: bool, limit: int, offset: int) -> list[AgentDefinition]:
        definitions = [
            replace(versions[-1], archived_at=self.archived_at.get(definition_id))
            for definition_id, versions in self.versions.items()
            if versions[-1].owner_id == owner_id and (include_archived or definition_id not in self.archived_at)
        ]
        return sorted(definitions, key=lambda item: (item.created_at, item.id))[offset : offset + limit]

    async def get_for_owner(self, owner_id: str, definition_id: UUID, *, version: int | None = None) -> AgentDefinition | None:
        versions = self.versions.get(definition_id, [])
        selected = next((item for item in versions if version is None or item.version == version), None)
        if selected is None or selected.owner_id != owner_id:
            return None
        return replace(selected, archived_at=self.archived_at.get(definition_id))

    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> AgentDefinition | None:
        current = await self.get_for_owner(owner_id, definition_id)
        if current is None or current.archived_at is not None:
            return None
        revised = replace(current, version=current.version + 1, **changes)
        self.versions[definition_id].append(revised)
        return revised

    async def archive_for_owner(self, owner_id: str, definition_id: UUID) -> AgentDefinition | None:
        current = await self.get_for_owner(owner_id, definition_id)
        if current is None:
            return None
        self.archived_at.setdefault(definition_id, datetime.now(UTC))
        return await self.get_for_owner(owner_id, definition_id)


def test_imported_definition_keeps_source_and_immutable_versions() -> None:
    registry = MemoryAgentRegistryRepository()
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
        )
    ) as client:
        assert client.get("/api/v1/agent-definitions").status_code == 401
        assert login(client).status_code == 200
        source = "---\nname: Architect\ndescription: Plans bounded work.\ncustom:\n  domain: office\n---\n\n# Original\nDo not rewrite this body.\n"
        created = client.post(
            "/api/v1/agent-definitions/import",
            json={"source_markdown": source},
            headers={"origin": ORIGIN},
        )
        assert created.status_code == 201
        original = created.json()
        assert original["name"] == "Architect"
        assert original["version"] == 1
        assert original["source_markdown"] == source
        assert original["unmapped_metadata"] == {"custom": {"domain": "office"}}
        summaries = client.get("/api/v1/agent-definitions").json()
        assert len(summaries) == 1
        assert "source_markdown" not in summaries[0]

        definition_id = original["id"]
        revised = client.put(
            f"/api/v1/agent-definitions/{definition_id}",
            json={"name": "Systems architect"},
            headers={"origin": ORIGIN},
        )
        assert revised.status_code == 200
        assert revised.json()["version"] == 2
        assert revised.json()["name"] == "Systems architect"
        assert revised.json()["source_markdown"] == source
        assert client.get(f"/api/v1/agent-definitions/{definition_id}?version=1").json()["name"] == "Architect"
        assert client.get(f"/api/v1/agent-definitions/{definition_id}/export?version=1").content.decode() == source

        archived = client.post(
            f"/api/v1/agent-definitions/{definition_id}/archive",
            headers={"origin": ORIGIN},
        )
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert client.get("/api/v1/agent-definitions").json() == []
        assert len(client.get("/api/v1/agent-definitions?include_archived=true").json()) == 1
        assert client.get(f"/api/v1/agent-definitions/{definition_id}").status_code == 200
        assert client.put(
            f"/api/v1/agent-definitions/{definition_id}",
            json={"name": "Archived change"},
            headers={"origin": ORIGIN},
        ).status_code == 404


def test_free_prompt_requires_metadata_and_rejects_unsafe_or_oversized_source() -> None:
    registry = MemoryAgentRegistryRepository()
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
        )
    ) as client:
        assert login(client).status_code == 200
        no_metadata = client.post(
            "/api/v1/agent-definitions/import",
            json={"source_markdown": "# Free prompt\nKeep every byte.\n"},
            headers={"origin": ORIGIN},
        )
        assert no_metadata.status_code == 422
        supplied = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "# Free prompt\nKeep every byte.\n",
                "name": "Free-form role",
                "description": "Imported without front matter",
            },
            headers={"origin": ORIGIN},
        )
        assert supplied.status_code == 201
        assert supplied.json()["source_markdown"] == "# Free prompt\nKeep every byte.\n"
        oversized = client.post(
            "/api/v1/agent-definitions/import",
            json={"source_markdown": "x" * (1_048_577), "name": "Too large"},
            headers={"origin": ORIGIN},
        )
        assert oversized.status_code == 422


def test_front_matter_uses_safe_yaml_and_rejects_cross_owner_access(tmp_path: Path) -> None:
    registry = MemoryAgentRegistryRepository()
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
        )
    ) as client:
        assert login(client).status_code == 200
        foreign_id = uuid4()
        registry.versions[foreign_id] = [
            AgentDefinition(
                foreign_id,
                "other-owner",
                1,
                "Private",
                "",
                "# private",
                {},
                None,
                datetime.now(UTC),
            )
        ]
        assert client.get(f"/api/v1/agent-definitions/{foreign_id}").status_code == 404
        marker = tmp_path / "yaml-executed"
        hostile = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": f"---\nname: Hostile\nextra: !!python/object/apply:os.system ['touch {marker}']\n---\nBody\n"
            },
            headers={"origin": ORIGIN},
        )
        assert hostile.status_code == 422
        assert not marker.exists()
        aliases = "\n".join(f"alias_{index}: *source" for index in range(51))
        too_many_aliases = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": f"---\nname: Aliases\nsource: &source [safe]\n{aliases}\n---\nBody\n"
            },
            headers={"origin": ORIGIN},
        )
        assert too_many_aliases.status_code == 422
