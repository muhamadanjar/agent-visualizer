from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.domain.projects import Project
from test_api import ORIGIN, database_ready, login, settings


@dataclass(frozen=True, slots=True)
class Definition:
    id: UUID
    owner_id: str
    name: str
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentInstance:
    id: UUID
    owner_id: str
    display_name: str
    definition_id: UUID
    definition_name: str
    project_id: UUID | None
    project_name: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[UUID, Project] = {}

    async def create(self, owner_id: str, name: str, summary: str) -> Project:
        project = Project(uuid4(), owner_id, name, summary, datetime.now(UTC))
        self.projects[project.id] = project
        return project

    async def list_for_owner(self, owner_id: str, *, limit: int, offset: int) -> list[Project]:
        owned = sorted(
            (item for item in self.projects.values() if item.owner_id == owner_id),
            key=lambda item: (item.created_at, item.id),
        )
        return owned[offset : offset + limit]

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None:
        project = self.projects.get(project_id)
        return project if project is not None and project.owner_id == owner_id else None


class MemoryDefinitionRepository:
    def __init__(self) -> None:
        self.definitions: dict[UUID, Definition] = {}

    async def get_for_owner(self, owner_id: str, definition_id: UUID, *, version: int | None = None):
        definition = self.definitions.get(definition_id)
        return definition if definition is not None and definition.owner_id == owner_id else None


class MemoryAgentInstanceRepository:
    def __init__(self, definitions: MemoryDefinitionRepository, projects: MemoryProjectRepository) -> None:
        self.definitions = definitions
        self.projects = projects
        self.instances: dict[UUID, AgentInstance] = {}

    def _with_names(self, instance: AgentInstance) -> AgentInstance:
        definition = self.definitions.definitions[instance.definition_id]
        project = self.projects.projects.get(instance.project_id) if instance.project_id else None
        return replace(instance, definition_name=definition.name, project_name=project.name if project else None)

    async def create(self, owner_id: str, display_name: str, definition_id: UUID, project_id: UUID | None) -> AgentInstance:
        now = datetime.now(UTC)
        definition = self.definitions.definitions[definition_id]
        project = self.projects.projects.get(project_id) if project_id else None
        instance = AgentInstance(uuid4(), owner_id, display_name, definition_id, definition.name, project_id, project.name if project else None, None, now, now)
        self.instances[instance.id] = instance
        return instance

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
        project_id: UUID | None = None,
    ) -> list[AgentInstance]:
        owned = [
            item
            for item in self.instances.values()
            if item.owner_id == owner_id
            and (include_archived or item.archived_at is None)
            and (project_id is None or item.project_id == project_id)
        ]
        owned.sort(key=lambda item: (item.created_at, item.id))
        return [self._with_names(item) for item in owned[offset : offset + limit]]

    async def get_for_owner(self, owner_id: str, instance_id: UUID) -> AgentInstance | None:
        instance = self.instances.get(instance_id)
        return self._with_names(instance) if instance and instance.owner_id == owner_id else None

    async def update_for_owner(self, owner_id: str, instance_id: UUID, changes: dict[str, object]) -> AgentInstance | None:
        instance = self.instances.get(instance_id)
        if instance is None or instance.owner_id != owner_id or instance.archived_at is not None:
            return None
        updated = replace(instance, **changes, updated_at=datetime.now(UTC))
        self.instances[instance_id] = updated
        return self._with_names(updated)

    async def archive_for_owner(self, owner_id: str, instance_id: UUID) -> AgentInstance | None:
        instance = self.instances.get(instance_id)
        if instance is None or instance.owner_id != owner_id:
            return None
        if instance.archived_at is None:
            now = datetime.now(UTC)
            instance = replace(instance, archived_at=now, updated_at=now)
            self.instances[instance_id] = instance
        return self._with_names(instance)


class Repositories:
    def __init__(self) -> None:
        self.projects = MemoryProjectRepository()
        self.definitions = MemoryDefinitionRepository()
        self.instances = MemoryAgentInstanceRepository(self.definitions, self.projects)


def make_client(repositories: Repositories) -> TestClient:
    return TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=repositories.projects,
            agent_registry_repository=repositories.definitions,
            agent_instance_repository=repositories.instances,
        )
    )


def authorized(client: TestClient) -> None:
    assert login(client).status_code == 200


def test_instance_creation_list_detail_and_project_placement_are_persisted() -> None:
    repositories = Repositories()
    definition = Definition(uuid4(), "owner", "Researcher")
    project = Project(uuid4(), "owner", "Atlas", "Research", datetime.now(UTC))
    repositories.definitions.definitions[definition.id] = definition
    repositories.projects.projects[project.id] = project

    with make_client(repositories) as client:
        authorized(client)
        created = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Mira", "definition_id": str(definition.id), "project_id": str(project.id)},
        )
        assert created.status_code == 201
        record = created.json()
        assert record["display_name"] == "Mira"
        assert record["definition_id"] == str(definition.id)
        assert record["definition_name"] == "Researcher"
        assert record["project_id"] == str(project.id)
        assert record["project_name"] == "Atlas"
        assert "runtime_session_id" not in record
        assert "presence" not in record

        assert client.get("/api/v1/agent-instances").json() == [record]
        assert client.get(f"/api/v1/agent-instances/{record['id']}").json() == record
        assert client.get(f"/api/v1/agent-instances?project_id={project.id}").json() == [record]
        assert client.get("/api/v1/agent-instances", headers={"cookie": ""}).status_code == 401


def test_foreign_definition_or_project_cannot_be_linked_or_inspected() -> None:
    repositories = Repositories()
    foreign_definition = Definition(uuid4(), "another-owner", "Private role")
    foreign_project = Project(uuid4(), "another-owner", "Private project", "Hidden", datetime.now(UTC))
    repositories.definitions.definitions[foreign_definition.id] = foreign_definition
    repositories.projects.projects[foreign_project.id] = foreign_project

    with make_client(repositories) as client:
        authorized(client)
        unowned_definition = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Hidden", "definition_id": str(foreign_definition.id)},
        )
        assert unowned_definition.status_code == 404
        own_definition = Definition(uuid4(), "owner", "Writer")
        repositories.definitions.definitions[own_definition.id] = own_definition
        unowned_project = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Writer", "definition_id": str(own_definition.id), "project_id": str(foreign_project.id)},
        )
        assert unowned_project.status_code == 404
        assert client.get(f"/api/v1/agent-instances/{uuid4()}").status_code == 404


def test_archive_is_soft_and_removes_instance_from_default_list() -> None:
    repositories = Repositories()
    definition = Definition(uuid4(), "owner", "Analyst")
    repositories.definitions.definitions[definition.id] = definition

    with make_client(repositories) as client:
        authorized(client)
        created = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Nara", "definition_id": str(definition.id)},
        ).json()
        archived = client.post(
            f"/api/v1/agent-instances/{created['id']}/archive",
            headers={"origin": ORIGIN},
        )
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert client.get("/api/v1/agent-instances").json() == []
        assert client.get("/api/v1/agent-instances?include_archived=true").json()[0]["id"] == created["id"]
        assert client.put(
            f"/api/v1/agent-instances/{created['id']}",
            headers={"origin": ORIGIN},
            json={"display_name": "Renamed"},
        ).status_code == 404


def test_invalid_input_and_missing_write_origin_are_rejected() -> None:
    repositories = Repositories()
    definition = Definition(uuid4(), "owner", "Researcher")
    repositories.definitions.definitions[definition.id] = definition

    with make_client(repositories) as client:
        authorized(client)
        body = {"display_name": "  ", "definition_id": str(definition.id)}
        assert client.post("/api/v1/agent-instances", headers={"origin": ORIGIN}, json=body).status_code == 422
        assert client.post("/api/v1/agent-instances", json={"display_name": "Valid", "definition_id": str(definition.id)}).status_code == 403
        assert client.get("/api/v1/agent-instances").status_code == 200



def test_archived_definition_stays_linked_for_existing_instance_edits() -> None:
    repositories = Repositories()
    definition = Definition(uuid4(), "owner", "Legacy role")
    repositories.definitions.definitions[definition.id] = definition

    with make_client(repositories) as client:
        authorized(client)
        created = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Old agent", "definition_id": str(definition.id)},
        ).json()
        repositories.definitions.definitions[definition.id] = replace(
            definition, archived_at=datetime.now(UTC)
        )
        updated = client.put(
            f"/api/v1/agent-instances/{created['id']}",
            headers={"origin": ORIGIN},
            json={"display_name": "Renamed", "definition_id": str(definition.id), "project_id": None},
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "Renamed"
        assert updated.json()["definition_id"] == str(definition.id)

def test_archived_definition_cannot_create_new_instance() -> None:
    repositories = Repositories()
    definition = Definition(uuid4(), "owner", "Archived role", datetime.now(UTC))
    repositories.definitions.definitions[definition.id] = definition

    with make_client(repositories) as client:
        authorized(client)
        response = client.post(
            "/api/v1/agent-instances",
            headers={"origin": ORIGIN},
            json={"display_name": "Legacy", "definition_id": str(definition.id)},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Archived definitions cannot be assigned to new instances"
