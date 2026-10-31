from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.evidence import ActivityEventDraft
from app.domain.execution import RunnerRunContext, RunnerStatus, WorkspacePolicy, WorkspacePolicyDraft
from app.domain.projects import Project
from app.main import create_app
from app.presentation.http.application import SESSION_COOKIE

ORIGIN = "http://testserver"
RUNNER_TOKEN = "runner-shared-token-with-more-than-thirty-two-characters"


def _settings() -> Settings:
    return Settings(
        environment="test",
        owner_username="owner",
        owner_password_hash=PasswordHasher().hash("execution-test-password"),
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


class MemoryProjects:
    def __init__(self, project: Project) -> None:
        self.project = project

    async def get_for_owner(self, owner_id: str, project_id: UUID) -> Project | None:
        if owner_id == self.project.owner_id and project_id == self.project.id:
            return self.project
        return None


class MemoryExecution:
    def __init__(self, project_id: UUID, runner_id: UUID, run_id: UUID) -> None:
        self.project_id = project_id
        self.runner_id = runner_id
        self.unready_runner_id = uuid4()
        self.run_id = run_id
        self.policy: WorkspacePolicy | None = None
        self.context = RunnerRunContext(
            owner_id="owner",
            run_id=run_id,
            task_id=uuid4(),
            project_id=project_id,
            instance_id=uuid4(),
            task_revision=1,
        )
        self.heartbeats: list[tuple[UUID, UUID]] = []
        self.heartbeat_allowed = True

    async def get_policy_for_project(self, owner_id: str, project_id: UUID) -> WorkspacePolicy | None:
        return self.policy if owner_id == "owner" and project_id == self.project_id else None

    async def save_policy(
        self, owner_id: str, project_id: UUID, draft: WorkspacePolicyDraft
    ) -> WorkspacePolicy | None:
        if owner_id != "owner" or project_id != self.project_id:
            return None
        self.policy = WorkspacePolicy(
            owner_id=owner_id,
            project_id=project_id,
            workspace_path=draft.workspace_path,
            allowed_tools=draft.allowed_tools,
            max_duration_seconds=draft.max_duration_seconds,
            max_inference_turns=draft.max_inference_turns,
            max_tool_calls=draft.max_tool_calls,
            enabled=draft.enabled,
            revision=1 if self.policy is None else self.policy.revision + 1,
            updated_at=datetime.now(UTC),
        )
        return self.policy

    async def get_ready_runner(self, *, max_age_seconds: int, credential_env: str = "NINEROUTER_KEY") -> RunnerStatus:
        return RunnerStatus(self.runner_id, datetime.now(UTC), True)
    async def get_runner_status(
        self, runner_id: UUID, *, max_age_seconds: int
    ) -> RunnerStatus | None:
        if runner_id == self.runner_id:
            return RunnerStatus(runner_id, datetime.now(UTC), True)
        if runner_id == self.unready_runner_id:
            return RunnerStatus(runner_id, datetime.now(UTC), False)
        return None

    async def register_runner(self, runner_id: UUID, *, provider_ready: bool, gateway_ready: bool = False) -> None:
        self.registered_status = RunnerStatus(runner_id, datetime.now(UTC), provider_ready, gateway_ready)

    async def claim_next(self, runner_id: UUID, *, lease_seconds: int):
        return None

    async def heartbeat_run(self, runner_id: UUID, run_id: UUID, *, lease_seconds: int) -> bool:
        self.heartbeats.append((runner_id, run_id))
        return self.heartbeat_allowed

    async def runner_owns_run(self, runner_id: UUID, run_id: UUID) -> bool:
        return runner_id == self.runner_id and run_id == self.run_id

    async def get_run_context_for_runner(
        self, runner_id: UUID, run_id: UUID
    ) -> RunnerRunContext | None:
        if runner_id == self.runner_id and run_id == self.run_id:
            return self.context
        return None



class MemoryEvents:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str, UUID], tuple[ActivityEventDraft, int]] = {}
        self.cursor = 0

    async def append_terminal_event(
        self, event: ActivityEventDraft, runner_id: UUID
    ):
        return await self.append_event(event)

    async def append_event(self, event: ActivityEventDraft):
        event.validate()
        key = (event.owner_id, event.source_id, event.event_id)
        existing = self.events.get(key)
        if existing is not None:
            previous, cursor = existing
            if previous != event:
                raise ValueError("event id reused with different content")
            return SimpleNamespace(event_id=event.event_id, cursor=cursor), False
        self.cursor += 1
        self.events[key] = (event, self.cursor)
        return SimpleNamespace(event_id=event.event_id, cursor=self.cursor), True

    async def has_event_for_runner(
        self, owner_id: str, source_id: str, run_id: UUID, event_id: UUID
    ) -> bool:
        existing = self.events.get((owner_id, source_id, event_id))
        return existing is not None and existing[0].run_id == run_id


def _client(project: Project, execution: MemoryExecution, events: MemoryEvents) -> TestClient:
    return TestClient(
        create_app(
            _settings(),
            database_check=_database_ready,
            project_repository=MemoryProjects(project),
            execution_repository=execution,
            activity_event_repository=events,
        )
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "execution-test-password"},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 200
    assert client.cookies.get(SESSION_COOKIE)


def test_project_policy_is_owner_scoped_and_rejects_unsafe_paths() -> None:
    project = Project(uuid4(), "owner", "Atlas", "", datetime.now(UTC))
    execution = MemoryExecution(project.id, uuid4(), uuid4())
    with _client(project, execution, MemoryEvents()) as client:
        _login(client)
        path = f"/api/v1/projects/{project.id}/execution-policy"
        response = client.put(
            path,
            headers={"origin": ORIGIN},
            json={
                "workspace_path": "team/project",
                "allowed_tools": ["list_files", "read_file", "write_file"],
                "max_duration_seconds": 300,
                "max_inference_turns": 24,
                "max_tool_calls": 15,
                "enabled": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["revision"] == 1
        assert response.json()["max_inference_turns"] == 24
        assert client.get(path).json()["allowed_tools"] == [
            "list_files",
            "read_file",
            "write_file",
        ]
        over_limit = client.put(
            path,
            headers={"origin": ORIGIN},
            json={
                "workspace_path": "team/project",
                "allowed_tools": ["list_files", "read_file"],
                "max_duration_seconds": 300,
                "max_inference_turns": 25,
                "max_tool_calls": 15,
                "enabled": True,
            },
        )
        assert over_limit.status_code == 422


        unsafe = client.put(
            path,
            headers={"origin": ORIGIN},
            json={
                "workspace_path": "../outside",
                "allowed_tools": ["shell"],
                "max_duration_seconds": 300,
                "max_inference_turns": 6,
                "max_tool_calls": 15,
                "enabled": True,
            },
        )
        assert unsafe.status_code == 422
        assert client.get(f"/api/v1/projects/{uuid4()}/execution-policy").status_code == 404


def test_runner_event_ack_is_authenticated_scoped_and_idempotent() -> None:
    project = Project(uuid4(), "owner", "Atlas", "", datetime.now(UTC))
    runner_id, run_id = uuid4(), uuid4()
    execution = MemoryExecution(project.id, runner_id, run_id)
    events = MemoryEvents()
    with _client(project, execution, events) as client:
        payload = {
            "runner_id": str(runner_id),
            "run_id": str(run_id),
            "event_id": str(uuid4()),
            "event_type": "run.started",
            "occurred_at": datetime.now(UTC).isoformat(),
            "source_sequence": 1,
        }
        route = "/internal/runner/events"
        assert client.post(route, json=payload).status_code == 401

        headers = {"authorization": f"Bearer {RUNNER_TOKEN}"}
        first = client.post(route, json=payload, headers=headers)
        replay = client.post(route, json=payload, headers=headers)
        assert first.status_code == 200
        assert first.json()["acknowledged"] is True
        assert first.json()["replayed"] is False
        assert replay.json()["cursor"] == first.json()["cursor"]
        assert replay.json()["replayed"] is True
        stored, _cursor = next(iter(events.events.values()))
        assert stored.owner_id == "owner"
        assert stored.project_id == project.id
        assert stored.task_id == execution.context.task_id
        assert stored.agent_instance_id == execution.context.instance_id
        action_payload = {
            **payload,
            "event_id": str(uuid4()),
            "event_type": "run.tool_action",
            "tool_name": "write_file",
        }
        action_response = client.post(route, json=action_payload, headers=headers)
        assert action_response.status_code == 200
        assert action_response.json()["acknowledged"] is True
        assert next(
            event.action_summary
            for event, _cursor in events.events.values()
            if event.event_type == "run.tool_action"
        ) == "Wrote a project file"
        terminal_payload = {
            **payload,
            "event_id": str(uuid4()),
            "event_type": "run.succeeded",
            "source_sequence": 4,
        }
        terminal_response = client.post(
            route, json=terminal_payload, headers=headers
        )
        assert terminal_response.status_code == 200
        assert terminal_response.json()["acknowledged"] is True
        arbitrary_summary = {
            **action_payload,
            "event_id": str(uuid4()),
            "source_sequence": 3,
            "action_summary": "secret token: attacker-controlled",
        }
        assert client.post(route, json=arbitrary_summary, headers=headers).status_code == 422

        wrong_runner = {**payload, "runner_id": str(uuid4()), "event_id": str(uuid4())}
        denied = client.post(route, json=wrong_runner, headers=headers)
        assert denied.status_code == 404


def test_runner_event_requires_an_active_lease_before_persisting() -> None:
    project = Project(uuid4(), "owner", "Atlas", "", datetime.now(UTC))
    runner_id, run_id = uuid4(), uuid4()
    execution = MemoryExecution(project.id, runner_id, run_id)
    execution.heartbeat_allowed = False
    events = MemoryEvents()
    with _client(project, execution, events) as client:
        payload = {
            "runner_id": str(runner_id),
            "run_id": str(run_id),
            "event_id": str(uuid4()),
            "event_type": "session.heartbeat",
            "occurred_at": datetime.now(UTC).isoformat(),
            "source_sequence": 1,
        }
        response = client.post(
            "/internal/runner/events",
            json=payload,
            headers={"authorization": f"Bearer {RUNNER_TOKEN}"},
        )
        assert response.status_code == 409
        assert events.events == {}


def test_runner_claim_checks_that_claimants_own_provider_readiness() -> None:
    project = Project(uuid4(), "owner", "Atlas", "", datetime.now(UTC))
    execution = MemoryExecution(project.id, uuid4(), uuid4())
    with _client(project, execution, MemoryEvents()) as client:
        response = client.post(
            "/internal/runner/claim",
            json={"runner_id": str(execution.unready_runner_id)},
            headers={"authorization": f"Bearer {RUNNER_TOKEN}"},
        )
        assert response.status_code == 200
        assert response.json() is None


def test_runner_heartbeat_defaults_gateway_readiness_to_false() -> None:
    project = Project(uuid4(), "owner", "Atlas", "", datetime.now(UTC))
    execution = MemoryExecution(project.id, uuid4(), uuid4())
    with _client(project, execution, MemoryEvents()) as client:
        headers = {"authorization": f"Bearer {RUNNER_TOKEN}"}
        payload = {"runner_id": str(execution.runner_id), "provider_ready": True}
        response = client.post("/internal/runner/heartbeat", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["provider_ready"] is True
        assert response.json()["gateway_ready"] is False
        assert execution.registered_status.gateway_ready is False
        response = client.post("/internal/runner/heartbeat", json={
            **payload, "provider_ready": False, "gateway_ready": True,
        }, headers=headers)
        assert response.status_code == 200
        assert execution.registered_status.provider_ready is False
        assert execution.registered_status.gateway_ready is True
        client.post("/internal/runner/heartbeat", json=payload, headers=headers)
        assert execution.registered_status.gateway_ready is False


def test_preflight_uses_only_the_selected_provider_readiness() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.tasks import TaskWorkflow

    async def scenario(credential_env: str, provider_ready: bool, gateway_ready: bool) -> None:
        task_id, project_id, instance_id, definition_id = uuid4(), uuid4(), uuid4(), uuid4()
        task = SimpleNamespace(id=task_id, project_id=project_id, agent_instance_id=instance_id)
        instance = SimpleNamespace(definition_id=definition_id, archived_at=None)
        policy = SimpleNamespace(enabled=True, workspace_path="project", allowed_tools=("read_file",))
        execution = SimpleNamespace(
            get_policy_for_project=AsyncMock(return_value=policy),
            get_ready_runner=AsyncMock(return_value=RunnerStatus(
                uuid4(), datetime.now(UTC), provider_ready, gateway_ready,
            )),
        )
        workflow = TaskWorkflow(
            SimpleNamespace(get_for_owner=AsyncMock(return_value=task)),
            SimpleNamespace(get_for_owner=AsyncMock(return_value=instance)),
            None, None, execution,
        )
        workflow._factory = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(
            model_endpoint=SimpleNamespace(credential_env=credential_env),
            configuration=SimpleNamespace(allowed_tools=("read_file",)),
            effective_tools=("read_file",),
        )))
        preflight = await workflow.preflight("owner", task_id)
        assert preflight is not None
        assert preflight.ready is (gateway_ready if credential_env == "OMP_GATEWAY_KEY" else provider_ready)
        execution.get_ready_runner.assert_awaited_once_with(
            max_age_seconds=30, credential_env=credential_env,
        )

    for credential_env in ("NINEROUTER_KEY", "OMP_GATEWAY_KEY"):
        for provider_ready, gateway_ready in ((True, False), (False, True), (False, False), (True, True)):
            asyncio.run(scenario(credential_env, provider_ready, gateway_ready))
