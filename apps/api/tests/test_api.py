from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import ConfigError, Settings
from app.main import SESSION_COOKIE, create_app
from app.projects import Project

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
