from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.runs import Run, RunInputError
from test_api import ORIGIN, database_ready, login, settings


class MemoryRunRepository:
    def __init__(self, task_id: UUID) -> None:
        self.task_ids = {task_id}
        self.runs: dict[UUID, Run] = {}
        self.requests: dict[tuple[str, UUID], UUID] = {}

    async def create_for_task(
        self, owner_id: str, task_id: UUID, request_id: UUID
    ) -> tuple[Run, bool]:
        if task_id not in self.task_ids:
            raise RunInputError("task")
        existing_id = self.requests.get((owner_id, request_id))
        if existing_id is not None:
            existing = self.runs[existing_id]
            if existing.task_id != task_id:
                raise RunInputError("idempotency_conflict")
            return existing, False
        attempt_number = 1 + max(
            (run.attempt_number for run in self.runs.values()
             if run.owner_id == owner_id and run.task_id == task_id),
            default=0,
        )
        run = Run(
            id=uuid4(),
            owner_id=owner_id,
            task_id=task_id,
            task_revision=1,
            attempt_number=attempt_number,
            request_id=request_id,
            status="created",
            configuration_snapshot={
                "task": {"id": str(task_id), "revision": 1},
                "agent_instance": {"id": str(uuid4()), "display_name": "Mira"},
                "agent_definition": {"id": str(uuid4()), "version": 1},
                "skills": [],
                "provider": None,
                "runtime": None,
            },
            created_at=datetime.now(UTC),
        )
        self.runs[run.id] = run
        self.requests[(owner_id, request_id)] = run.id
        return run, True

    async def get_for_owner(self, owner_id: str, run_id: UUID) -> Run | None:
        run = self.runs.get(run_id)
        return run if run is not None and run.owner_id == owner_id else None

    async def list_for_task(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Run] | None:
        if task_id not in self.task_ids:
            return None
        rows = sorted(
            (run for run in self.runs.values()
             if run.owner_id == owner_id and run.task_id == task_id),
            key=lambda run: run.attempt_number,
        )
        return rows[offset : offset + limit]


def test_run_api_requires_auth_and_replays_request_without_duplicate_attempt() -> None:
    task_id = uuid4()
    request_id = uuid4()
    runs = MemoryRunRepository(task_id)
    client = TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            run_repository=runs,
        )
    )

    with client:
        body = {"request_id": str(request_id)}
        assert client.post(
            f"/api/v1/tasks/{task_id}/runs", json=body, headers={"origin": ORIGIN}
        ).status_code == 401
        assert login(client).status_code == 200

        created = client.post(
            f"/api/v1/tasks/{task_id}/runs", json=body, headers={"origin": ORIGIN}
        )
        assert created.status_code == 201
        run = created.json()
        assert run["status"] == "created"
        assert run["attempt_number"] == 1
        assert run["task_id"] == str(task_id)
        assert "owner_id" not in run

        replayed = client.post(
            f"/api/v1/tasks/{task_id}/runs", json=body, headers={"origin": ORIGIN}
        )
        assert replayed.status_code == 200
        assert replayed.json()["id"] == run["id"]
        assert len(client.get(f"/api/v1/tasks/{task_id}/runs").json()) == 1
        assert client.get(f"/api/v1/runs/{run['id']}").json() == run
        assert client.get(f"/api/v1/runs/{uuid4()}").status_code == 404
