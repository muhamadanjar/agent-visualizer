from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.domain.projects import Project
from app.domain.tasks import TaskSpec
from test_api import ORIGIN, MemoryProjectRepository, database_ready, login, settings


@dataclass(frozen=True, slots=True)
class StoredTask:
    id: UUID
    owner_id: str
    revision: int
    project_id: UUID
    project_name: str
    agent_instance_id: UUID | None
    agent_instance_name: str | None
    title: str
    goal: str
    input_text: str
    resource_refs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    blocked_by_task_ids: tuple[UUID, ...]
    max_duration_minutes: int | None
    max_child_runs: int | None
    stage: str
    created_at: datetime
    updated_at: datetime
    revision_created_at: datetime


class MemoryTaskRepository:
    def __init__(self, project_names: dict[UUID, str]) -> None:
        self.project_names = project_names
        self.tasks: dict[UUID, StoredTask] = {}
        self.revisions: dict[UUID, list[StoredTask]] = {}

    def _record(
        self,
        owner_id: str,
        task_id: UUID,
        revision: int,
        spec: TaskSpec,
        created_at: datetime,
        updated_at: datetime,
    ) -> StoredTask:
        return StoredTask(
            id=task_id, owner_id=owner_id, revision=revision,
            project_id=spec.project_id, project_name=self.project_names[spec.project_id],
            agent_instance_id=spec.agent_instance_id, agent_instance_name=None,
            title=spec.title, goal=spec.goal, input_text=spec.input_text,
            resource_refs=spec.resource_refs,
            acceptance_criteria=spec.acceptance_criteria,
            blocked_by_task_ids=spec.blocked_by_task_ids,
            max_duration_minutes=spec.max_duration_minutes,
            max_child_runs=spec.max_child_runs, stage="Backlog",
            created_at=created_at, updated_at=updated_at,
            revision_created_at=updated_at,
        )

    async def create(self, owner_id: str, spec: TaskSpec) -> StoredTask:
        now = datetime.now(UTC)
        task = self._record(owner_id, uuid4(), 1, spec, now, now)
        self.tasks[task.id] = task
        self.revisions[task.id] = [task]
        return task

    async def list_for_owner(
        self, owner_id: str, *, project_id: UUID | None, limit: int, offset: int
    ) -> list[StoredTask]:
        tasks = sorted(
            (task for task in self.tasks.values()
             if task.owner_id == owner_id and (project_id is None or task.project_id == project_id)),
            key=lambda task: (task.created_at, task.id), reverse=True,
        )
        return tasks[offset:offset + limit]

    async def get_for_owner(self, owner_id: str, task_id: UUID) -> StoredTask | None:
        task = self.tasks.get(task_id)
        return task if task is not None and task.owner_id == owner_id else None

    async def list_revisions_for_owner(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[StoredTask] | None:
        task = await self.get_for_owner(owner_id, task_id)
        if task is None:
            return None
        revisions = self.revisions[task_id]
        return revisions[offset:offset + limit]

    async def update_for_owner(
        self, owner_id: str, task_id: UUID, spec: TaskSpec
    ) -> StoredTask | None:
        current = await self.get_for_owner(owner_id, task_id)
        if current is None:
            return None
        current_spec = TaskSpec(
            project_id=current.project_id,
            agent_instance_id=current.agent_instance_id,
            title=current.title,
            goal=current.goal,
            input_text=current.input_text,
            resource_refs=current.resource_refs,
            acceptance_criteria=current.acceptance_criteria,
            blocked_by_task_ids=current.blocked_by_task_ids,
            max_duration_minutes=current.max_duration_minutes,
            max_child_runs=current.max_child_runs,
        )
        if current_spec == spec:
            return current
        now = datetime.now(UTC)
        updated = self._record(
            owner_id, task_id, current.revision + 1, spec, current.created_at, now
        )
        self.tasks[task_id] = updated
        self.revisions[task_id].append(updated)
        return updated


def make_client(projects: MemoryProjectRepository, tasks: MemoryTaskRepository) -> TestClient:
    return TestClient(
        create_app(
            settings(), database_check=database_ready,
            project_repository=projects, task_repository=tasks,
        )
    )


def test_backlog_task_create_update_and_revision_history_are_owner_scoped() -> None:
    project = Project(uuid4(), "owner", "Atlas", "Project for a persisted backlog task", datetime.now(UTC))
    foreign_project = Project(uuid4(), "another-owner", "Private", "Other owner's project", datetime.now(UTC))
    projects = MemoryProjectRepository()
    projects.seed(project)
    projects.seed(foreign_project)
    tasks = MemoryTaskRepository({project.id: project.name, foreign_project.id: foreign_project.name})

    with make_client(projects, tasks) as client:
        assert login(client).status_code == 200
        payload = {
            "project_id": str(project.id),
            "agent_instance_id": None,
            "title": "Persist provider comparison",
            "goal": "Compare public provider route behavior.",
            "input_text": "Use public information only.",
            "resource_refs": ["notes/requirements.md"],
            "acceptance_criteria": ["List observable differences.", "Mark unknown capabilities explicitly."],
            "blocked_by_task_ids": [],
            "max_duration_minutes": 30,
            "max_child_runs": 0,
        }
        created_response = client.post("/api/v1/tasks", json=payload, headers={"origin": ORIGIN})
        assert created_response.status_code == 201
        created = created_response.json()
        assert created["revision"] == 1
        assert created["stage"] == "Backlog"
        assert created["project_name"] == "Atlas"
        assert created["agent_instance_id"] is None
        assert created["resource_refs"] == ["notes/requirements.md"]
        assert "owner_id" not in created
        task_id = created["id"]

        assert client.get("/api/v1/tasks").json() == [created]
        assert client.get(f"/api/v1/tasks/{task_id}").json() == created
        assert client.get(
            "/api/v1/tasks", params={"project_id": str(foreign_project.id)}
        ).json() == []
        assert client.get(f"/api/v1/tasks/{task_id}", headers={"cookie": ""}).status_code == 401

        updated_payload = {**payload, "title": "Compare provider route evidence"}
        updated_response = client.put(
            f"/api/v1/tasks/{task_id}", json=updated_payload, headers={"origin": ORIGIN}
        )
        assert updated_response.status_code == 200
        updated = updated_response.json()
        assert updated["revision"] == 2
        assert updated["title"] == "Compare provider route evidence"
        assert updated["stage"] == "Backlog"
        assert client.put(
            f"/api/v1/tasks/{task_id}", json=updated_payload, headers={"origin": ORIGIN}
        ).json()["revision"] == 2
        history = client.get(f"/api/v1/tasks/{task_id}/revisions")
        assert history.status_code == 200
        assert [revision["revision"] for revision in history.json()] == [1, 2]
        assert history.json()[0]["title"] == "Persist provider comparison"

        assert client.put(
            f"/api/v1/tasks/{task_id}", json=updated_payload
        ).status_code == 403
        assert client.post(
            "/api/v1/tasks", json={**payload, "project_id": str(foreign_project.id)}, headers={"origin": ORIGIN}
        ).status_code == 404

def test_reordered_task_dependencies_do_not_create_revision() -> None:
    project = Project(uuid4(), "owner", "Atlas", "Dependency ordering test", datetime.now(UTC))
    projects = MemoryProjectRepository()
    projects.seed(project)
    tasks = MemoryTaskRepository({project.id: project.name})

    with make_client(projects, tasks) as client:
        assert login(client).status_code == 200
        payload = {
            "project_id": str(project.id),
            "agent_instance_id": None,
            "title": "Dependency task",
            "goal": "Keep dependency order out of task revision identity.",
            "input_text": "",
            "resource_refs": [],
            "acceptance_criteria": ["Equivalent dependency sets do not create revisions."],
            "blocked_by_task_ids": [],
            "max_duration_minutes": None,
            "max_child_runs": None,
        }
        blocker_ids = []
        for title in ("Blocker A", "Blocker B"):
            response = client.post(
                "/api/v1/tasks",
                json={**payload, "title": title, "goal": title},
                headers={"origin": ORIGIN},
            )
            assert response.status_code == 201
            blocker_ids.append(response.json()["id"])

        create_response = client.post(
            "/api/v1/tasks",
            json={**payload, "blocked_by_task_ids": blocker_ids},
            headers={"origin": ORIGIN},
        )
        assert create_response.status_code == 201
        task_id = create_response.json()["id"]
        assert create_response.json()["blocked_by_task_ids"] == sorted(blocker_ids)

        reordered_response = client.put(
            f"/api/v1/tasks/{task_id}",
            json={**payload, "blocked_by_task_ids": list(reversed(blocker_ids))},
            headers={"origin": ORIGIN},
        )
        assert reordered_response.status_code == 200
        assert reordered_response.json()["revision"] == 1
