from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.presentation.http.application import OwnerSessionStore

from app.infrastructure.redis.activity_stream import ActivityEventHub
from app.config import ConfigError, Settings
from app.domain.evidence import (
    ActivityEvent,
    ActivityEventDraft,
    ActivitySnapshot,
    EvidenceInputError,
    RunActivityProjection,
)
from app.main import create_app
from app.presentation.http.application import SESSION_COOKIE
from app.domain.projects import Project

TEST_PASSWORD = "phase-two-test-password"
TEST_PASSWORD_HASH = PasswordHasher().hash(TEST_PASSWORD)
ORIGIN = "http://testserver"


def settings(*, attempts: int = 5) -> Settings:
    return Settings(
        environment="test",
        owner_username="owner",
        owner_password_hash=TEST_PASSWORD_HASH,
        session_secret="test-session-secret-with-enough-entropy",
        public_origin=ORIGIN,
        database_url="postgresql://office:test-password@localhost:5432/office",
        session_cookie_secure=False,
        session_ttl_seconds=600,
        session_cookie_samesite="strict",
        login_rate_limit_attempts=attempts,
        login_rate_limit_window_seconds=60,
    )


def test_redis_url_rejects_non_redis_schemes() -> None:
    with pytest.raises(ConfigError, match="REDIS_URL"):
        replace(settings(), redis_url="http://redis:6379/0")

def test_production_requires_https_and_secure_session_cookie() -> None:
    with pytest.raises(ConfigError):
        Settings(
            environment="production",
            owner_username="owner",
            owner_password_hash=TEST_PASSWORD_HASH,
            session_secret="test-session-secret-with-enough-entropy",
            public_origin="http://testserver",
            database_url="postgresql://office:test-password@db:5432/office",
            session_cookie_secure=False,
        )


async def database_ready() -> None:
    return None

class MemoryProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[UUID, Project] = {}

    async def create(self, owner_id: str, name: str, summary: str) -> Project:
        project = Project(uuid4(), owner_id, name, summary, datetime.now(UTC))
        self.projects[project.id] = project
        return project

    async def list_for_owner(
        self, owner_id: str, *, limit: int, offset: int
    ) -> list[Project]:
        owned = sorted(
            (project for project in self.projects.values() if project.owner_id == owner_id),
            key=lambda project: (project.created_at, project.id),
        )
        return owned[offset : offset + limit]

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None:
        project = self.projects.get(project_id)
        return project if project is not None and project.owner_id == owner_id else None

    def seed(self, project: Project) -> None:
        self.projects[project.id] = project

class MemoryEmptyModelEndpointRepository:
    async def get_for_owner(self, owner_id: str, endpoint_id: UUID):
        return None

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ):
        return []



@pytest.fixture
def project_repository() -> MemoryProjectRepository:
    return MemoryProjectRepository()


@pytest.fixture
def client(project_repository: MemoryProjectRepository) -> Iterator[TestClient]:
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            project_repository=project_repository,
        )
    ) as test_client:
        yield test_client


def login(client: TestClient, *, username: str = "owner", password: str = TEST_PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"origin": ORIGIN},
    )


def test_owner_can_login_read_session_and_logout(client: TestClient) -> None:
    response = login(client)
    session_token = response.cookies.get(SESSION_COOKIE)
    assert session_token is not None
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "agent_office_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" not in cookie
    assert client.get("/api/v1/auth/me").json() == {"owner_id": "owner", "username": "owner"}

    logout = client.post("/api/v1/auth/logout", headers={"origin": ORIGIN})
    assert logout.status_code == 200
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert client.get(
        "/api/v1/auth/me",
        headers={"cookie": f"{SESSION_COOKIE}={session_token}"},
    ).status_code == 401


def test_invalid_credentials_are_generic_and_do_not_create_session(client: TestClient) -> None:
    response = login(client, username="someone-else", password="wrong-password")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_requires_exact_configured_origin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": TEST_PASSWORD},
        headers={"origin": "https://attacker.invalid"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Origin not allowed"}
    assert "set-cookie" not in response.headers

def test_login_rate_limit_rejects_after_configured_failures() -> None:
    app = create_app(
        settings(attempts=1),
        database_check=database_ready,
        project_repository=MemoryProjectRepository(),
    )
    with TestClient(app) as client:
        assert login(client, password="wrong-password").status_code == 401
        assert login(client).status_code == 429


def test_login_rate_limit_rejects_concurrent_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        settings(attempts=1),
        database_check=database_ready,
        project_repository=MemoryProjectRepository(),
    )
    verification_started = threading.Event()
    release_verification = threading.Event()
    original_verify = PasswordHasher.verify

    def delayed_verify(hasher: PasswordHasher, encoded: str, password: str) -> bool:
        if password == "slow-failure":
            verification_started.set()
            assert release_verification.wait(timeout=5)
        return original_verify(hasher, encoded, password)

    monkeypatch.setattr(PasswordHasher, "verify", delayed_verify)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        first_attempt = executor.submit(
            client.post,
            "/api/v1/auth/login",
            json={"username": "owner", "password": "slow-failure"},
            headers={"origin": ORIGIN},
        )
        assert verification_started.wait(timeout=5)
        second_attempt = executor.submit(
            client.post,
            "/api/v1/auth/login",
            json={"username": "owner", "password": "wrong-password"},
            headers={"origin": ORIGIN},
        )
        try:
            assert second_attempt.result(timeout=5).status_code == 429
        finally:
            release_verification.set()
        assert first_attempt.result(timeout=5).status_code == 401
        assert login(client, password="wrong-password").status_code == 429


def test_project_routes_enforce_auth_origin_and_owner_scope(
    client: TestClient,
    project_repository: MemoryProjectRepository,
) -> None:
    assert client.get("/api/v1/projects").status_code == 401
    assert login(client).status_code == 200

    created = client.post(
        "/api/v1/projects",
        json={"name": "  Private project  ", "summary": "  Bounded scope  "},
        headers={"origin": ORIGIN},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Private project"
    assert project["summary"] == "Bounded scope"
    assert "owner_id" not in project
    assert client.get("/api/v1/projects").json() == [project]
    assert client.get(f"/api/v1/projects/{project['id']}").json() == project

    foreign = Project(
        id=uuid4(),
        owner_id="other-owner",
        name="Private to another owner",
        summary="Must not be disclosed",
        created_at=datetime.now(UTC),
    )
    project_repository.seed(foreign)
    assert client.get("/api/v1/projects").json() == [project]
    assert client.get(f"/api/v1/projects/{foreign.id}").status_code == 404

    assert client.post(
        "/api/v1/projects",
        json={"name": "Cross-origin"},
        headers={"origin": "https://attacker.invalid"},
    ).status_code == 403
    assert client.post(
        "/api/v1/projects",
        json={"name": "   "},
        headers={"origin": ORIGIN},
    ).status_code == 422
    assert client.post(
        "/api/v1/projects",
        json={"name": "Invalid field", "owner_id": "attacker"},
        headers={"origin": ORIGIN},
    ).status_code == 422
    assert client.get("/api/v1/projects?limit=0").status_code == 422
    assert client.get("/api/v1/projects?limit=201").status_code == 422
    assert client.get("/api/v1/projects/not-a-uuid").status_code == 422


def test_health_readiness_reports_database_failure() -> None:
    async def database_down() -> None:
        raise RuntimeError("database offline")

    with TestClient(
        create_app(
            settings(),
            database_check=database_down,
            project_repository=MemoryProjectRepository(),
        )
    ) as client:
        assert client.get("/health/live").json() == {"status": "live"}
        assert client.get("/health/ready").status_code == 503

def test_websocket_requires_exact_origin_and_owner_session(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws",
            headers={"origin": "https://attacker.invalid"},
        ):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws", headers={"origin": ORIGIN}):
            pass

    assert login(client).status_code == 200
    with client.websocket_connect("/api/v1/ws", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_json() == {"type": "session_ready", "owner_id": "owner"}
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}
        logout = client.post("/api/v1/auth/logout", headers={"origin": ORIGIN})
        assert logout.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()



def test_websocket_closes_expired_session_before_live_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = False
    authenticate_session = OwnerSessionStore.authenticate

    async def authenticate(self: OwnerSessionStore, token: str | None):
        if expired:
            return None
        return await authenticate_session(self, token)

    monkeypatch.setattr(OwnerSessionStore, "authenticate", authenticate)
    hub = ActivityEventHub()

    class EmptyEvents:
        async def get_snapshot_for_owner(self, owner_id: str, *, limit: int):
            return ActivitySnapshot(cursor=0, projections=())

        async def list_events_after(self, owner_id: str, cursor: int, *, limit: int):
            return []

    app = create_app(
        settings(),
        database_check=database_ready,
        project_repository=MemoryProjectRepository(),
        activity_event_repository=EmptyEvents(),
    )
    with TestClient(app) as client:
        client.app.state.activity_event_hub = hub
        assert login(client).status_code == 200
        with client.websocket_connect("/api/v1/ws", headers={"origin": ORIGIN}) as socket:
            assert socket.receive_json()["type"] == "session_ready"
            socket.send_json({"type": "subscribe"})
            assert socket.receive_json()["type"] == "activity_snapshot"
            expired = True
            client.portal.call(
                hub.publish,
                {
                    "event_id": str(uuid4()),
                    "event_version": 1,
                    "sequence": 1,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "type": "run.started",
                    "owner_id": "owner",
                    "project_id": str(uuid4()),
                    "agent_id": str(uuid4()),
                    "run_id": str(uuid4()),
                    "task_id": str(uuid4()),
                    "payload": {
                        "task_revision": 1,
                        "source_sequence": 1,
                        "safe_action_summary": None,
                    },
                },
            )
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()

def test_task_preflight_checks_active_assignment_and_fails_closed_without_runner() -> None:
    instance_id = uuid4()
    definition_id = uuid4()
    task_id = uuid4()
    task = SimpleNamespace(agent_instance_id=instance_id)
    instance = SimpleNamespace(archived_at=None, definition_id=definition_id)

    class MemoryTasks:
        async def get_for_owner(self, owner_id: str, requested_id: UUID):
            return task if owner_id == "owner" and requested_id == task_id else None

    class MemoryDefinitions:
        async def get_for_owner(self, owner_id: str, requested_id: UUID, *, version: int | None = None):
            if owner_id != "owner" or requested_id != definition_id:
                return None
            return SimpleNamespace(
                id=definition_id,
                version=1,
                archived_at=None,
                enabled=True,
                configuration=None,
            )

    class MemoryEndpoints:
        async def get_for_owner(self, owner_id: str, requested_id: UUID):
            return None

    class MemoryInstances:
        async def get_for_owner(self, owner_id: str, requested_id: UUID):
            return instance if owner_id == "owner" and requested_id == instance_id else None
    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            task_repository=MemoryTasks(),
            agent_registry_repository=MemoryDefinitions(),
            model_endpoint_repository=MemoryEndpoints(),
            agent_instance_repository=MemoryInstances(),
        )
    ) as client:
        assert client.get(f"/api/v1/tasks/{task_id}/preflight").status_code == 401
        assert login(client).status_code == 200
        assert client.get(f"/api/v1/tasks/{uuid4()}/preflight").status_code == 404

        response = client.get(f"/api/v1/tasks/{task_id}/preflight")
        assert response.status_code == 200
        preflight = response.json()
        assert preflight["ready"] is False
        checks = {item["key"]: item["status"] for item in preflight["checks"]}
        assert checks == {
            "agent_assignment": "ready",
            "model_endpoint": "blocked",
            "workspace_policy": "unavailable",
            "tool_allowlist": "unavailable",
            "runner_connection": "unavailable",
            "provider_credentials": "unavailable",
        }

        instance.archived_at = datetime.now(UTC)
        archived_response = client.get(f"/api/v1/tasks/{task_id}/preflight")
        assert archived_response.status_code == 200
        archived_checks = {
            item["key"]: item["status"] for item in archived_response.json()["checks"]
        }
        assert archived_checks["agent_assignment"] == "blocked"


def test_activity_event_rejects_unknown_schema_version() -> None:
    draft = ActivityEventDraft(
        owner_id="owner",
        source_id="runner",
        event_id=uuid4(),
        schema_version=2,
        source_session_id=None,
        workspace_id=None,
        project_id=uuid4(),
        task_id=uuid4(),
        task_revision=1,
        agent_instance_id=uuid4(),
        run_id=uuid4(),
        event_type="run.started",
        occurred_at=datetime.now(UTC),
        source_sequence=0,
    )
    with pytest.raises(EvidenceInputError, match="schema_version"):
        draft.validate()


def test_activity_reads_and_websocket_reconnect_are_owner_scoped() -> None:
    owner_id = "owner"
    run_id = uuid4()
    task_id = uuid4()
    project_id = uuid4()
    instance_id = uuid4()
    now = datetime.now(UTC)
    first = ActivityEvent(
        owner_id=owner_id,
        cursor=1,
        source_id="runner",
        event_id=uuid4(),
        schema_version=1,
        source_session_id=uuid4(),
        workspace_id=None,
        project_id=project_id,
        task_id=task_id,
        task_revision=1,
        agent_instance_id=instance_id,
        run_id=run_id,
        event_type="run.started",
        occurred_at=now,
        received_at=now,
        source_sequence=1,
    )
    second = replace(
        first,
        cursor=2,
        event_id=uuid4(),
        event_type="run.waiting_input",
        source_sequence=2,
    )
    projection = RunActivityProjection(
        owner_id=owner_id,
        run_id=run_id,
        source_id="runner",
        task_id=task_id,
        task_revision=1,
        project_id=project_id,
        agent_instance_id=instance_id,
        status="running",
        last_event_cursor=1,
        last_event_type="run.started",
        last_event_at=now,
        last_sequence=1,
        last_heartbeat_at=None,
        last_action_summary=None,
        updated_at=now,
    )

    class MemoryRuns:
        async def get_for_owner(self, requested_owner: str, requested_run: UUID):
            return object() if requested_owner == owner_id and requested_run == run_id else None

    class MemoryEvents:
        async def get_snapshot_for_owner(self, requested_owner: str, *, limit: int):
            return ActivitySnapshot(
                cursor=1 if requested_owner == owner_id else 0,
                projections=(projection,) if requested_owner == owner_id else (),
            )

        async def get_projection_for_run(self, requested_owner: str, requested_run: UUID):
            return projection if requested_owner == owner_id and requested_run == run_id else None

        async def list_events_for_run(self, requested_owner: str, requested_run: UUID, *, limit: int):
            return [first, second] if requested_owner == owner_id and requested_run == run_id else None

        async def list_events_after(self, requested_owner: str, cursor: int, *, limit: int):
            return [second] if requested_owner == owner_id and cursor < second.cursor else []

    with TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            run_repository=MemoryRuns(),
            activity_event_repository=MemoryEvents(),
        )
    ) as client:
        assert client.get(f"/api/v1/runs/{run_id}/activity").status_code == 401
        assert login(client).status_code == 200
        activity_response = client.get(f"/api/v1/runs/{run_id}/activity")
        assert activity_response.status_code == 200
        assert activity_response.json()["status"] == "running"
        assert [item["type"] for item in activity_response.json()["events"]] == [
            "run.started",
            "run.waiting_input",
        ]
        event = activity_response.json()["events"][0]
        assert event["event_version"] == 1
        assert event["sequence"] == 1
        assert event["run_id"] == str(run_id)
        assert event["task_id"] == str(task_id)
        assert event["agent_id"] == str(instance_id)
        assert event["payload"]["source_sequence"] == 1
        assert client.get(f"/api/v1/runs/{uuid4()}/activity").status_code == 404
        snapshot_response = client.get("/api/v1/activity/snapshot")
        assert snapshot_response.status_code == 200
        assert snapshot_response.json()["projections"][0]["agent_instance_id"] == str(instance_id)

        with client.websocket_connect("/api/v1/ws", headers={"origin": ORIGIN}) as socket:
            assert socket.receive_json()["type"] == "session_ready"
            socket.send_json({"type": "subscribe"})
            snapshot_message = socket.receive_json()
            assert snapshot_message["type"] == "activity_snapshot"
            assert snapshot_message["cursor"] == 1
            event_message = socket.receive_json()
            assert event_message["type"] == "activity_events"
            assert event_message["events"][0]["type"] == "run.waiting_input"
        with client.websocket_connect("/api/v1/ws", headers={"origin": ORIGIN}) as socket:
            assert socket.receive_json()["type"] == "session_ready"
            socket.send_json({"type": "subscribe", "after_cursor": 1})
            assert socket.receive_json()["type"] == "activity_snapshot"
            replay = socket.receive_json()
            assert replay["type"] == "activity_events"
            assert replay["events"][0]["sequence"] == 2
            assert replay["events"][0]["type"] == "run.waiting_input"
