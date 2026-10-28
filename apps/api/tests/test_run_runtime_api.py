from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.runs import Run
from app.main import create_app
from app.presentation.http.application import SESSION_COOKIE

ORIGIN = "http://testserver"
RUNNER_TOKEN = "runner-shared-token-with-more-than-thirty-two-characters"
OWNER_PASSWORD = "runtime-api-test-password"


def _settings() -> Settings:
    return Settings(
        environment="test",
        owner_username="owner",
        owner_password_hash=PasswordHasher().hash(OWNER_PASSWORD),
        session_secret="test-session-secret-with-enough-entropy",
        public_origin=ORIGIN,
        database_url="postgresql://office:test-password@localhost:5432/office",
        session_cookie_secure=False,
        session_ttl_seconds=600,
        session_cookie_samesite="strict",
        runner_shared_token=RUNNER_TOKEN,
    )


async def _database_ready() -> None:
    return None


class MemoryRuns:
    def __init__(self, run: Run) -> None:
        self.run = run

    async def get_for_owner(self, owner_id: str, run_id: UUID) -> Run | None:
        return self.run if owner_id == self.run.owner_id and run_id == self.run.id else None


class MemoryExecution:
    def __init__(self, runner_id: UUID, run_id: UUID) -> None:
        self.runner_id = runner_id
        self.run_id = run_id
        self.active = True
        self.cancel_state: str | None = "cancellation_pending"
        self.owner_id = "owner"

    async def request_cancellation(self, owner_id: str, run_id: UUID) -> str | None:
        if owner_id != self.owner_id or run_id != self.run_id:
            return None
        return self.cancel_state

    async def get_run_context_for_runner(self, runner_id: UUID, run_id: UUID):
        if runner_id != self.runner_id or run_id != self.run_id:
            return None
        return SimpleNamespace(owner_id=self.owner_id)

    async def get_lease_statuses(self, runner_id: UUID, run_ids: list[UUID]):
        if runner_id != self.runner_id or self.run_id not in run_ids:
            return []
        return [
            SimpleNamespace(
                runner_id=self.runner_id,
                active_lease=self.active,
            )
        ]


class MemoryResults:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, UUID], SimpleNamespace] = {}

    async def create_for_run(self, owner_id: str, run_id: UUID, text: str):
        key = (owner_id, run_id)
        if key in self.rows and self.rows[key].text != text:
            from app.domain.run_results import RunResultConflictError

            raise RunResultConflictError("conflicting Run Result")
        if key not in self.rows:
            self.rows[key] = SimpleNamespace(
                owner_id=owner_id,
                run_id=run_id,
                text=text,
                byte_length=len(text.encode("utf-8")),
                created_at=datetime.now(UTC),
            )
            return self.rows[key], True
        return self.rows[key], False

    async def get_for_owner(self, owner_id: str, run_id: UUID):
        return self.rows.get((owner_id, run_id))


def _run() -> Run:
    task_id = uuid4()
    return Run(
        id=uuid4(),
        owner_id="owner",
        task_id=task_id,
        task_revision=1,
        attempt_number=1,
        request_id=uuid4(),
        status="created",
        configuration_snapshot={"task": {"id": str(task_id), "revision": 1}},
        created_at=datetime.now(UTC),
    )


def _client(run: Run, execution: MemoryExecution, results: MemoryResults) -> TestClient:
    return TestClient(
        create_app(
            _settings(),
            database_check=_database_ready,
            run_repository=MemoryRuns(run),
            execution_repository=execution,
            run_result_repository=results,
        )
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": OWNER_PASSWORD},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 200
    assert client.cookies.get(SESSION_COOKIE)


def test_runner_result_is_saved_once_and_read_only_by_the_run_owner() -> None:
    run = _run()
    runner_id = uuid4()
    execution = MemoryExecution(runner_id, run.id)
    results = MemoryResults()
    with _client(run, execution, results) as client:
        payload = {
            "runner_id": str(runner_id),
            "run_id": str(run.id),
            "text": "Completed safely.",
        }
        url = "/internal/runner/results"
        headers = {"Authorization": f"Bearer {RUNNER_TOKEN}"}

        assert client.post(url, json=payload).status_code == 401
        saved = client.post(url, json=payload, headers=headers)
        replayed = client.post(url, json=payload, headers=headers)

        assert saved.status_code == 200
        assert replayed.status_code == 200
        assert saved.json() == {"accepted": True}
        _login(client)
        result = client.get(f"/api/v1/runs/{run.id}/result")
        assert result.status_code == 200
        assert result.json()["text"] == "Completed safely."
        assert client.get(f"/api/v1/runs/{uuid4()}/result").status_code == 404


def test_result_requires_active_runner_lease_and_rejects_conflicting_replay() -> None:
    run = _run()
    runner_id = uuid4()
    execution = MemoryExecution(runner_id, run.id)
    results = MemoryResults()
    with _client(run, execution, results) as client:
        url = "/internal/runner/results"
        headers = {"Authorization": f"Bearer {RUNNER_TOKEN}"}
        payload = {
            "runner_id": str(runner_id),
            "run_id": str(run.id),
            "text": "First result",
        }
        execution.active = False
        assert client.post(url, json=payload, headers=headers).status_code == 409

        execution.active = True
        assert client.post(url, json=payload, headers=headers).status_code == 200
        payload["text"] = "Different result"
        assert client.post(url, json=payload, headers=headers).status_code == 409


def test_owner_can_request_cancellation_only_for_an_active_run() -> None:
    run = _run()
    runner_id = uuid4()
    execution = MemoryExecution(runner_id, run.id)
    with _client(run, execution, MemoryResults()) as client:
        _login(client)
        url = f"/api/v1/runs/{run.id}/cancel"
        assert client.post(url).status_code == 403
        response = client.post(url, headers={"origin": ORIGIN})
        assert response.status_code == 202
        assert response.json() == {"status": "cancellation_pending"}

        execution.cancel_state = "succeeded"
        assert client.post(url, headers={"origin": ORIGIN}).status_code == 409
        assert client.post(
            f"/api/v1/runs/{uuid4()}/cancel", headers={"origin": ORIGIN}
        ).status_code == 404
