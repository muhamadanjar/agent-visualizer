from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.domain.agent_configuration import AgentDefinitionConfiguration
from app.domain.model_endpoints import ADAPTER, ModelEndpoint
from app.domain.registry import (
    AgentDefinition,
    AgentDefinitionInputError,
    AgentRegistryConflictError,
)

from test_api import (
    MemoryEmptyModelEndpointRepository,
    MemoryProjectRepository,
    ORIGIN,
    database_ready,
    login,
    settings,
)


class MemoryAgentRegistryRepository:
    def __init__(self) -> None:
        self.versions: dict[UUID, list[AgentDefinition]] = {}
        self.archived_at: dict[UUID, datetime] = {}

    async def create(
        self, owner_id: str, name: str, description: str, source_markdown: str,
        unmapped_metadata: dict[str, object], configuration: AgentDefinitionConfiguration | None,
    ) -> AgentDefinition:
        now = datetime.now(UTC)
        definition = AgentDefinition(
            uuid4(), owner_id, 1, name, description, source_markdown, unmapped_metadata,
            configuration, configuration is not None, None, now,
        )
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
        selected = (
            versions[-1]
            if version is None and versions
            else next((item for item in versions if item.version == version), None)
        )
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
    async def set_enabled_for_owner(
        self, owner_id: str, definition_id: UUID, enabled: bool
    ) -> AgentDefinition | None:
        current = await self.get_for_owner(owner_id, definition_id)
        if current is None or current.archived_at is not None:
            return None
        updated = replace(current, enabled=enabled)
        self.versions[definition_id][-1] = updated
        return updated


class RacingEnableRegistryRepository(MemoryAgentRegistryRepository):
    async def set_enabled_for_owner(
        self, owner_id: str, definition_id: UUID, enabled: bool
    ) -> AgentDefinition | None:
        if enabled:
            raise AgentRegistryConflictError(
                "Configure an active Model Endpoint before enabling the definition"
            )
        return await super().set_enabled_for_owner(owner_id, definition_id, enabled)

class RegistryWriteConflictRepository(MemoryAgentRegistryRepository):
    conflict_operation: str | None = None

    async def create(
        self, owner_id: str, name: str, description: str, source_markdown: str,
        unmapped_metadata: dict[str, object], configuration: AgentDefinitionConfiguration | None,
    ) -> AgentDefinition:
        if self.conflict_operation == "create" and configuration is not None:
            raise AgentRegistryConflictError(
                "Configure an active Model Endpoint before saving this Definition"
            )
        return await super().create(
            owner_id, name, description, source_markdown, unmapped_metadata, configuration
        )

    async def update_for_owner(
        self, owner_id: str, definition_id: UUID, changes: dict[str, object]
    ) -> AgentDefinition | None:
        if self.conflict_operation == "update" and "configuration" in changes:
            raise AgentRegistryConflictError(
                "Configure an active Model Endpoint before saving this Definition"
            )
        return await super().update_for_owner(owner_id, definition_id, changes)





class MemoryRegistryEndpointRepository:
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self.endpoint = endpoint

    async def get_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None:
        if owner_id != self.endpoint.owner_id or endpoint_id != self.endpoint.id:
            return None
        return self.endpoint



def test_imported_definition_keeps_source_and_immutable_versions() -> None:
    registry = MemoryAgentRegistryRepository()
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
            model_endpoint_repository=MemoryEmptyModelEndpointRepository(),
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
        assert "owner_id" not in original
        summaries = client.get("/api/v1/agent-definitions").json()
        assert len(summaries) == 1
        assert "source_markdown" not in summaries[0]
        assert "owner_id" not in summaries[0]

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
            model_endpoint_repository=MemoryEmptyModelEndpointRepository(),
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
            model_endpoint_repository=MemoryEmptyModelEndpointRepository(),
        )
    ) as client:
        assert login(client).status_code == 200
        valid_source = "---\nname: Stable definition\n---\nBody\n"
        baseline = client.post(
            "/api/v1/agent-definitions/import",
            json={"source_markdown": valid_source},
            headers={"origin": ORIGIN},
        )
        assert baseline.status_code == 201
        definition_id = baseline.json()["id"]
        malformed_scalars = (
            "---\nname: Invalid date\nwhen: 2026-13-01\n---\nBody\n",
            "---\nname: Invalid timestamp\nwhen: !!timestamp invalid\n---\nBody\n",
            "---\nname: Invalid boolean\nextra: !!bool invalid\n---\nBody\n",
            '---\nname: Invalid integer\nextra: !!int ""\n---\nBody\n',
            '---\nname: Invalid float\nextra: !!float ""\n---\nBody\n',
            "---\nname: Overflow\nextra: !!float \"" + "1:" * 200 + "1\"\n---\nBody\n",
        )
        for source in malformed_scalars:
            imported = client.post(
                "/api/v1/agent-definitions/import",
                json={"source_markdown": source},
                headers={"origin": ORIGIN},
            )
            updated = client.put(
                f"/api/v1/agent-definitions/{definition_id}",
                json={"source_markdown": source},
                headers={"origin": ORIGIN},
            )
            assert imported.status_code == 422
            assert updated.status_code == 422
        unchanged = client.get(f"/api/v1/agent-definitions/{definition_id}?version=1")
        stored_versions = registry.versions[UUID(definition_id)]
        assert unchanged.json()["source_markdown"] == valid_source
        assert len(stored_versions) == 1
        assert stored_versions[0].version == 1
        assert stored_versions[0].source_markdown == valid_source
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
                False,
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
        alias = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Aliases\nsource: &source [safe]\ncopy: *source\n---\nBody\n"
            },
            headers={"origin": ORIGIN},
        )
        assert alias.status_code == 422
        nested_value = "[" * 65 + "0" + "]" * 65
        too_deep = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": f"---\nname: Nested\nmetadata: {nested_value}\n---\nBody\n"
            },
            headers={"origin": ORIGIN},
        )
        assert too_deep.status_code == 422
        wide_front_matter = "\n".join(f"key_{index}: value" for index in range(10_000))
        too_many_nodes = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": f"---\nname: Wide\n{wide_front_matter}\n---\nBody\n"
            },
            headers={"origin": ORIGIN},
        )
        assert too_many_nodes.status_code == 422


def test_definition_configuration_is_versioned_and_enabled_status_is_separate() -> None:
    registry = MemoryAgentRegistryRepository()
    endpoint = ModelEndpoint(
        id=uuid4(),
        owner_id="owner",
        name="Free model endpoint",
        adapter=ADAPTER,
        endpoint_url="https://provider.example/v1",
        credential_env="NINEROUTER_KEY",
        default_model_id="openrouter/model:free",
        default_route="free",
        archived_at=None,
        created_at=datetime.now(UTC),
    )
    endpoints = MemoryRegistryEndpointRepository(endpoint)
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
            model_endpoint_repository=endpoints,
        )
    ) as client:
        assert login(client).status_code == 200
        configuration = {
            "role": "Research worker",
            "model_endpoint_id": str(endpoint.id),
            "model_id": "openrouter/model:free",
            "route": None,
            "runtime_id": "langchain",
            "temperature": 0.3,
            "max_iterations": 5,
            "concurrency_limit": 2,
            "execution_timeout_seconds": 300,
            "allowed_tools": ["read_file"],
        }
        foreign_endpoint = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Foreign endpoint use\n---\n",
                "configuration": {**configuration, "model_endpoint_id": str(uuid4())},
            },
            headers={"origin": ORIGIN},
        )
        assert foreign_endpoint.status_code == 422
        created = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Research worker\n---\nUse evidence.\n",
                "configuration": configuration,
            },
            headers={"origin": ORIGIN},
        )
        assert created.status_code == 201
        version_one = created.json()
        definition_id = version_one["id"]
        assert version_one["enabled"] is True
        assert version_one["configuration"]["role"] == "Research worker"
        assert version_one["configuration"]["model_endpoint_id"] == str(endpoint.id)
        duplicate_name = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Research worker\n---\nSecond UUID.\n",
                "configuration": configuration,
            },
            headers={"origin": ORIGIN},
        )
        assert duplicate_name.status_code == 201
        assert duplicate_name.json()["name"] == version_one["name"]
        assert duplicate_name.json()["id"] != version_one["id"]

        next_configuration = {**configuration, "model_id": "openrouter/second-model:free"}
        updated = client.put(
            f"/api/v1/agent-definitions/{definition_id}",
            json={"configuration": next_configuration},
            headers={"origin": ORIGIN},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated.json()["configuration"]["model_id"] == "openrouter/second-model:free"
        assert client.get(f"/api/v1/agent-definitions/{definition_id}?version=1").json()[
            "configuration"
        ]["model_id"] == "openrouter/model:free"

        disabled = client.put(
            f"/api/v1/agent-definitions/{definition_id}/enabled",
            json={"enabled": False},
            headers={"origin": ORIGIN},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        endpoints.endpoint = replace(endpoint, archived_at=datetime.now(UTC))
        rejected_enable = client.put(
            f"/api/v1/agent-definitions/{definition_id}/enabled",
            json={"enabled": True},
            headers={"origin": ORIGIN},
        )
        assert rejected_enable.status_code == 409
        assert client.get(f"/api/v1/agent-definitions/{definition_id}").json()["enabled"] is False


def test_definition_create_and_update_conflicts_return_http_409() -> None:
    registry = RegistryWriteConflictRepository()
    endpoint = ModelEndpoint(
        id=uuid4(),
        owner_id="owner",
        name="Free model endpoint",
        adapter=ADAPTER,
        endpoint_url="https://provider.example/v1",
        credential_env="NINEROUTER_KEY",
        default_model_id="openrouter/model:free",
        default_route="free",
        archived_at=None,
        created_at=datetime.now(UTC),
    )
    configuration = {
        "role": "Research worker",
        "model_endpoint_id": str(endpoint.id),
        "model_id": endpoint.default_model_id,
        "max_iterations": 5,
        "concurrency_limit": 1,
        "execution_timeout_seconds": 300,
        "allowed_tools": [],
    }
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
            model_endpoint_repository=MemoryRegistryEndpointRepository(endpoint),
        )
    ) as client:
        assert login(client).status_code == 200
        base = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Base worker\n---\n",
                "configuration": configuration,
            },
            headers={"origin": ORIGIN},
        )
        assert base.status_code == 201

        registry.conflict_operation = "update"
        updated = client.put(
            f"/api/v1/agent-definitions/{base.json()['id']}",
            json={
                "configuration": {
                    **configuration,
                    "model_id": "openrouter/updated-model:free",
                }
            },
            headers={"origin": ORIGIN},
        )
        assert updated.status_code == 409
        assert "active Model Endpoint" in updated.json()["detail"]

        registry.conflict_operation = "create"
        created = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Concurrent worker\n---\n",
                "configuration": configuration,
            },
            headers={"origin": ORIGIN},
        )
        assert created.status_code == 409
        assert "active Model Endpoint" in created.json()["detail"]


def test_enable_conflict_is_reported_when_endpoint_is_archived_during_enable() -> None:
    registry = RacingEnableRegistryRepository()
    endpoint = ModelEndpoint(
        id=uuid4(),
        owner_id="owner",
        name="Free model endpoint",
        adapter=ADAPTER,
        endpoint_url="https://provider.example/v1",
        credential_env="NINEROUTER_KEY",
        default_model_id="openrouter/model:free",
        default_route="free",
        archived_at=None,
        created_at=datetime.now(UTC),
    )
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=MemoryProjectRepository(),
            agent_registry_repository=registry,
            model_endpoint_repository=MemoryRegistryEndpointRepository(endpoint),
        )
    ) as client:
        assert login(client).status_code == 200
        created = client.post(
            "/api/v1/agent-definitions/import",
            json={
                "source_markdown": "---\nname: Race-safe worker\n---\n",
                "configuration": {
                    "role": "Research worker",
                    "model_endpoint_id": str(endpoint.id),
                    "model_id": endpoint.default_model_id,
                    "max_iterations": 5,
                    "concurrency_limit": 1,
                    "execution_timeout_seconds": 300,
                    "allowed_tools": [],
                },
            },
            headers={"origin": ORIGIN},
        )
        assert created.status_code == 201
        response = client.put(
            f"/api/v1/agent-definitions/{created.json()['id']}/enabled",
            json={"enabled": True},
            headers={"origin": ORIGIN},
        )
        assert response.status_code == 409
        assert "active Model Endpoint" in response.json()["detail"]
