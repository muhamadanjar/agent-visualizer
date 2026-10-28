"""Private owner API for authenticated sessions and scoped domain resources."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable, Literal
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator
from app.domain.execution import (
    ALLOWED_WORKSPACE_TOOLS,
    ExecutionPolicyInputError,
    ExecutionRepository,
    WorkspacePolicy,
    WorkspacePolicyDraft,
)
from app.application.tasks import TaskWorkflow, validate_task_references
from app.domain.agent_configuration import (
    AgentConfigurationInputError,
    AgentDefinitionConfiguration,
)
from app.domain.common import NameConflictError, SkillPin
from app.domain.evidence import (
    ActivityEvent,
    ActivityEventDraft,
    ActivityEventRepository,
    ActivitySnapshot,
    ApprovalRepository,
    ApprovalRequest,
    Artifact,
    ArtifactRepository,
    EvidenceInputError,
    OfficeSceneRepository,
    OfficeSceneVersion,
    RunActivityProjection,
    activity_event_envelope,
)
from app.domain.instances import AgentInstance, AgentInstanceInputError, AgentInstanceRepository
from app.domain.projects import Project, ProjectRepository
from app.domain.model_endpoints import (
    ModelEndpoint,
    ModelEndpointDraft,
    ModelEndpointInputError,
    ModelEndpointRepository,
)
from app.domain.registry import (
    AgentDefinition,
    AgentDefinitionInputError,
    AgentRegistryConflictError,
    AgentRegistryRepository,
    parse_agent_markdown,
)
from app.domain.runs import Run, RunInputError, RunRepository
from app.domain.skills import (
    Skill,
    SkillInputError,
    SkillRepository,
    SkillSummary,
    normalize_skill_content,
)
from app.domain.run_results import (
    RunResultConflictError,
    RunResultRepository,
)
from app.domain.tasks import Task, TaskInputError, TaskRepository, TaskSpec

SESSION_COOKIE = "agent_office_session"
CORRELATION_HEADER = "X-Correlation-ID"
CORRELATION_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
logger = logging.getLogger("agent_office.api")
_RUNNER_TOOL_ACTION_SUMMARIES = {
    "list_files": "Listed project files",
    "read_file": "Read a project file",
    "write_file": "Wrote a project file",
}


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    owner_id: str
    username: str


class OwnerSessionStore:
    """Opaque, revocable owner sessions for the single-replica private pilot."""

    def __init__(self, secret: str, ttl_seconds: int, max_sessions: int = 128) -> None:
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, tuple[OwnerPrincipal, float]] = OrderedDict()
        self._sockets: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    def _session_key(self, token: str | None) -> str | None:
        if not token or len(token) > 128:
            return None
        try:
            token_bytes = token.encode("ascii")
        except UnicodeEncodeError:
            return None
        return hmac.new(self._secret, token_bytes, hashlib.sha256).hexdigest()

    def _prune_expired(self, now: float) -> None:
        expired = [
            key
            for key, (_, expires_at) in self._sessions.items()
            if expires_at <= now
        ]
        for key in expired:
            self._sessions.pop(key, None)

    async def issue(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        key = hmac.new(self._secret, token.encode("ascii"), hashlib.sha256).hexdigest()
        async with self._lock:
            now = time.monotonic()
            self._prune_expired(now)
            self._sessions[key] = (
                OwnerPrincipal(owner_id="owner", username=username),
                now + self._ttl_seconds,
            )
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        return token

    async def authenticate(self, token: str | None) -> OwnerPrincipal | None:
        key = self._session_key(token)
        if key is None:
            return None
        async with self._lock:
            now = time.monotonic()
            self._prune_expired(now)
            session = self._sessions.get(key)
            if session is None:
                return None
            principal, expires_at = session
            if expires_at <= now:
                del self._sessions[key]
                return None
            self._sessions.move_to_end(key)
            return principal


    async def register_socket(self, token: str | None, websocket: WebSocket) -> float | None:
        key = self._session_key(token)
        if key is None:
            return None
        async with self._lock:
            now = time.monotonic()
            self._prune_expired(now)
            session = self._sessions.get(key)
            if session is None:
                return None
            _, expires_at = session
            if expires_at <= now:
                self._sessions.pop(key, None)
                return None
            self._sockets.setdefault(key, set()).add(websocket)
            return expires_at

    async def unregister_socket(self, token: str | None, websocket: WebSocket) -> None:
        key = self._session_key(token)
        if key is None:
            return
        async with self._lock:
            sockets = self._sockets.get(key)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                self._sockets.pop(key, None)
    async def revoke(self, token: str | None) -> None:
        key = self._session_key(token)
        if key is None:
            return
        async with self._lock:
            self._sessions.pop(key, None)
            sockets = tuple(self._sockets.pop(key, ()))
        if sockets:
            await asyncio.gather(
                *(websocket.close(code=1008) for websocket in sockets),
                return_exceptions=True,
            )

class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: StrictStr = Field(min_length=1, max_length=64)
    password: StrictStr = Field(min_length=1, max_length=1024)


class LoginRateLimiter:
    """Single-process failed-login limiter; deployment runs exactly one API replica."""

    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._in_flight: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def reserve_attempt(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            failures = self._failures.get(key)
            if failures is not None:
                while failures and failures[0] <= now - self.window_seconds:
                    failures.popleft()
                if not failures:
                    self._failures.pop(key, None)
                    failures = None

            in_flight = self._in_flight.get(key, 0)
            if (len(failures) if failures is not None else 0) + in_flight >= self.attempts:
                if failures is not None:
                    self._failures.move_to_end(key)
                return False
            self._in_flight[key] = in_flight + 1
            return True

    async def finish_attempt(self, key: str, *, failed: bool | None = None) -> None:
        now = time.monotonic()
        async with self._lock:
            in_flight = self._in_flight.get(key, 0)
            if in_flight < 1:
                raise RuntimeError("login attempt finished without a reservation")
            if in_flight == 1:
                del self._in_flight[key]
            else:
                self._in_flight[key] = in_flight - 1

            if failed is False:
                self._failures.pop(key, None)
            elif failed is True:
                failures = self._failures.setdefault(key, deque())
                while failures and failures[0] <= now - self.window_seconds:
                    failures.popleft()
                failures.append(now)
                self._failures.move_to_end(key)
                while len(self._failures) > 4096:
                    self._failures.popitem(last=False)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=120)
    summary: StrictStr = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return value.strip()

class ProjectResponse(BaseModel):
    id: UUID
    name: str
    summary: str
    created_at: datetime
class WorkspacePolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_path: StrictStr = Field(min_length=1, max_length=512)

    allowed_tools: list[StrictStr] = Field(min_length=1, max_length=3)

    @field_validator("allowed_tools")
    @classmethod
    def validate_tool_names(cls, value: list[str]) -> list[str]:
        if (
            len(set(value)) != len(value)
            or any(tool not in ALLOWED_WORKSPACE_TOOLS for tool in value)
        ):
            raise ValueError("allowed_tools must contain distinct supported tools")
        return value

    max_duration_seconds: StrictInt = Field(ge=1, le=3600)
    max_inference_turns: StrictInt = Field(ge=1, le=12)
    max_tool_calls: StrictInt = Field(ge=1, le=50)
    enabled: StrictBool = True


class WorkspacePolicyResponse(BaseModel):
    project_id: UUID
    workspace_path: str
    allowed_tools: list[str]
    max_duration_seconds: int
    max_inference_turns: int
    max_tool_calls: int
    enabled: bool
    revision: int
    updated_at: datetime


class RunnerHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID
    provider_ready: StrictBool


class RunnerClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID


class RunnerEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID
    run_id: UUID
    event_id: UUID
    event_type: Literal[
        "run.started",
        "run.waiting_input",
        "run.waiting_approval",
        "run.blocked",
        "run.tool_action",
        "run.cancellation_pending",
        "run.succeeded",
        "run.failed",
        "run.canceled",
        "run.unknown",
        "session.heartbeat",
    ]
    occurred_at: datetime
    source_sequence: StrictInt = Field(ge=0)
    tool_name: Literal["list_files", "read_file", "write_file"] | None = None



class RunnerRunHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID
    run_id: UUID


class RunnerCancellationPollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID


class RunnerLeaseStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID
    run_ids: list[UUID] = Field(min_length=1, max_length=2)

    @field_validator("run_ids")
    @classmethod
    def unique_run_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("Run IDs must be unique")
        return values


class RunnerResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: UUID
    run_id: UUID
    text: StrictStr = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_result_size(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 1_048_576:
            raise ValueError("Run Result must be non-empty and at most 1 MiB")
        return value


class RunResultResponse(BaseModel):
    run_id: UUID
    text: str
    byte_length: int
    created_at: datetime


class RunCancellationResponse(BaseModel):
    status: Literal["cancellation_pending"]

class RunnerClaimResponse(BaseModel):
    owner_id: str
    run_id: UUID
    task_id: UUID
    project_id: UUID
    instance_id: UUID
    configuration_snapshot: dict[str, object]
    workspace_path: str
    allowed_tools: list[str]
    max_duration_seconds: int
    max_inference_turns: int
    max_tool_calls: int


class TaskWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    agent_instance_id: UUID | None = None
    title: StrictStr = Field(min_length=1, max_length=200)
    goal: StrictStr = Field(min_length=1, max_length=10000)
    input_text: StrictStr = Field(default="", max_length=10000)
    resource_refs: list[StrictStr] = Field(default_factory=list, max_length=100)
    acceptance_criteria: list[StrictStr] = Field(min_length=1, max_length=100)
    blocked_by_task_ids: list[UUID] = Field(default_factory=list, max_length=100)
    max_duration_minutes: StrictInt | None = Field(default=None, ge=1, le=1440)
    max_child_runs: StrictInt | None = Field(default=None, ge=0, le=100)

    @field_validator("title", "goal")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must not be blank")
        return normalized

    @field_validator("resource_refs")
    @classmethod
    def normalize_resource_refs(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 2048 for value in normalized):
            raise ValueError("resource references must contain 1 to 2048 characters")
        return normalized

    @field_validator("acceptance_criteria")
    @classmethod
    def normalize_acceptance_criteria(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 2000 for value in normalized):
            raise ValueError("acceptance criteria must contain 1 to 2000 characters")
        return normalized

    @field_validator("blocked_by_task_ids")
    @classmethod
    def unique_dependencies(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("task dependencies must be unique")
        return sorted(values)


class TaskResponse(BaseModel):
    id: UUID
    revision: int
    stage: str
    project_id: UUID
    project_name: str
    agent_instance_id: UUID | None
    agent_instance_name: str | None
    title: str
    goal: str
    input_text: str
    resource_refs: list[str]
    acceptance_criteria: list[str]
    blocked_by_task_ids: list[UUID]
    max_duration_minutes: int | None
    max_child_runs: int | None
    created_at: datetime
    updated_at: datetime
    revision_created_at: datetime


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class ModelEndpointWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=120)
    endpoint_url: StrictStr = Field(min_length=1, max_length=2048)
    credential_env: StrictStr
    default_model_id: StrictStr = Field(min_length=1, max_length=200)
    default_route: StrictStr | None = Field(default=None, max_length=120)


class ModelEndpointResponse(BaseModel):
    id: UUID
    name: str
    adapter: str
    endpoint_url: str
    credential_env: str
    default_model_id: str
    default_route: str | None
    archived: bool
    created_at: datetime


def _model_endpoint_response(endpoint: ModelEndpoint) -> ModelEndpointResponse:
    return ModelEndpointResponse(
        id=endpoint.id,
        name=endpoint.name,
        adapter=endpoint.adapter,
        endpoint_url=endpoint.endpoint_url,
        credential_env=endpoint.credential_env,
        default_model_id=endpoint.default_model_id,
        default_route=endpoint.default_route,
        archived=endpoint.archived_at is not None,
        created_at=endpoint.created_at,
    )


class AgentConfigurationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: StrictStr = Field(min_length=1, max_length=120)
    model_endpoint_id: UUID
    model_id: StrictStr = Field(min_length=1, max_length=200)
    route: StrictStr | None = Field(default=None, max_length=120)
    runtime_id: Literal["langchain"] = "langchain"
    temperature: StrictFloat | StrictInt | None = None
    max_iterations: StrictInt = Field(ge=1, le=100)
    concurrency_limit: StrictInt = Field(ge=1, le=20)
    execution_timeout_seconds: StrictInt = Field(ge=1, le=86400)
    allowed_tools: list[StrictStr] = Field(default_factory=list, max_length=3)

    def to_domain(self) -> AgentDefinitionConfiguration:
        return AgentDefinitionConfiguration.create(
            role=self.role,
            model_endpoint_id=self.model_endpoint_id,
            model_id=self.model_id,
            route=self.route,
            runtime_id=self.runtime_id,
            temperature=self.temperature,
            max_iterations=self.max_iterations,
            concurrency_limit=self.concurrency_limit,
            execution_timeout_seconds=self.execution_timeout_seconds,
            allowed_tools=self.allowed_tools,
        )

class RunResponse(BaseModel):
    id: UUID
    task_id: UUID
    task_revision: int
    attempt_number: int
    request_id: UUID
    status: str
    configuration_snapshot: dict[str, object]
    created_at: datetime


def _run_response(run: Run) -> RunResponse:
    return RunResponse(
        id=run.id,
        task_id=run.task_id,
        task_revision=run.task_revision,
        attempt_number=run.attempt_number,
        request_id=run.request_id,
        status=run.status,
        configuration_snapshot=run.configuration_snapshot,
        created_at=run.created_at,
    )

class ActivityEventPayloadResponse(BaseModel):
    task_revision: int
    source_sequence: int | None
    safe_action_summary: str | None


class ActivityEventResponse(BaseModel):
    event_id: UUID
    event_version: int
    sequence: int
    timestamp: datetime
    type: str
    owner_id: str
    project_id: UUID
    agent_id: UUID
    run_id: UUID
    task_id: UUID
    payload: ActivityEventPayloadResponse


class RunActivityResponse(BaseModel):
    run_id: UUID
    status: str | None
    freshness: Literal["unknown", "fresh", "stale"]
    last_event_at: datetime | None
    last_heartbeat_at: datetime | None
    cursor: int
    safe_action_summary: str | None
    events: list[ActivityEventResponse]


class RuntimeProjectionResponse(BaseModel):
    run_id: UUID
    task_id: UUID
    project_id: UUID
    agent_instance_id: UUID
    status: str
    freshness: Literal["fresh", "stale"]
    last_event_cursor: int
    last_event_type: str
    last_event_at: datetime
    last_heartbeat_at: datetime | None
    safe_action_summary: str | None


class ActivitySnapshotResponse(BaseModel):
    cursor: int
    projections: list[RuntimeProjectionResponse]


class ApprovalResponse(BaseModel):
    id: UUID
    run_id: UUID
    action_type: str
    action_digest: str
    resource_ref: str | None
    created_at: datetime
    expires_at: datetime
    decision: str | None
    decided_at: datetime | None


class ApprovalDecisionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]


class ArtifactResponse(BaseModel):
    id: UUID
    run_id: UUID
    artifact_type: str
    display_name: str
    media_type: str | None
    byte_length: int | None
    sha256: str | None
    created_at: datetime
    available: bool = False


class OfficeSceneVersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contents: dict[str, object]
    expected_previous_version: StrictInt | None = Field(default=None, ge=0)


class OfficeSceneVersionResponse(BaseModel):
    scene_id: str
    version: int
    contents: dict[str, object]
    created_at: datetime


class PreflightCheckResponse(BaseModel):
    key: str
    status: Literal["ready", "blocked", "unavailable"]
    detail: str


class TaskPreflightResponse(BaseModel):
    task_id: UUID
    ready: bool
    checks: list[PreflightCheckResponse]


def _activity_event_response(event: ActivityEvent) -> ActivityEventResponse:
    envelope = activity_event_envelope(event)
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise RuntimeError("Activity Event payload must be an object")
    return ActivityEventResponse(
        event_id=event.event_id,
        event_version=event.schema_version,
        sequence=event.cursor,
        timestamp=event.occurred_at,
        type=event.event_type,
        owner_id=event.owner_id,
        project_id=event.project_id,
        agent_id=event.agent_instance_id,
        run_id=event.run_id,
        task_id=event.task_id,
        payload=ActivityEventPayloadResponse(**payload),
    )


def _activity_freshness(
    projection: RunActivityProjection | None, now: datetime
) -> Literal["unknown", "fresh", "stale"]:
    if projection is None:
        return "unknown"
    last_seen = projection.last_heartbeat_at or projection.last_event_at
    return "stale" if (now - last_seen).total_seconds() > 30 else "fresh"


def _activity_projection_response(
    projection: RunActivityProjection, now: datetime
) -> RuntimeProjectionResponse:
    freshness = _activity_freshness(projection, now)
    assert freshness != "unknown"
    return RuntimeProjectionResponse(
        run_id=projection.run_id,
        task_id=projection.task_id,
        project_id=projection.project_id,
        agent_instance_id=projection.agent_instance_id,
        status=projection.status,
        freshness=freshness,
        last_event_cursor=projection.last_event_cursor,
        last_event_type=projection.last_event_type,
        last_event_at=projection.last_event_at,
        last_heartbeat_at=projection.last_heartbeat_at,
        safe_action_summary=projection.last_action_summary,
    )


def _approval_response(approval: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        run_id=approval.run_id,
        action_type=approval.action_type,
        action_digest=approval.action_digest,
        resource_ref=approval.resource_ref,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        decision=approval.decision,
        decided_at=approval.decided_at,
    )


def _artifact_response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        run_id=artifact.run_id,
        artifact_type=artifact.artifact_type,
        display_name=artifact.display_name,
        media_type=artifact.media_type,
        byte_length=artifact.byte_length,
        sha256=artifact.sha256,
        created_at=artifact.created_at,
        available=False,
    )


def _office_scene_response(scene: OfficeSceneVersion) -> OfficeSceneVersionResponse:
    return OfficeSceneVersionResponse(
        scene_id=scene.scene_id,
        version=scene.version,
        contents=scene.contents,
        created_at=scene.created_at,
    )


def _task_spec(body: TaskWrite) -> TaskSpec:
    return TaskSpec(
        project_id=body.project_id,
        agent_instance_id=body.agent_instance_id,
        title=body.title,
        goal=body.goal,
        input_text=body.input_text,
        resource_refs=tuple(body.resource_refs),
        acceptance_criteria=tuple(body.acceptance_criteria),
        blocked_by_task_ids=tuple(body.blocked_by_task_ids),
        max_duration_minutes=body.max_duration_minutes,
        max_child_runs=body.max_child_runs,
    )


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        revision=task.revision,
        stage=task.stage,
        project_id=task.project_id,
        project_name=task.project_name,
        agent_instance_id=task.agent_instance_id,
        agent_instance_name=task.agent_instance_name,
        title=task.title,
        goal=task.goal,
        input_text=task.input_text,
        resource_refs=list(task.resource_refs),
        acceptance_criteria=list(task.acceptance_criteria),
        blocked_by_task_ids=list(task.blocked_by_task_ids),
        max_duration_minutes=task.max_duration_minutes,
        max_child_runs=task.max_child_runs,
        created_at=task.created_at,
        updated_at=task.updated_at,
        revision_created_at=task.revision_created_at,
    )


class AgentInstanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: StrictStr = Field(min_length=1, max_length=120)
    definition_id: UUID
    project_id: UUID | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized


class AgentInstanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    definition_id: UUID | None = None
    project_id: UUID | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized


class AgentInstanceResponse(BaseModel):
    id: UUID
    display_name: str
    definition_id: UUID
    definition_name: str
    project_id: UUID | None
    project_name: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime


def _agent_instance_response(instance: AgentInstance) -> AgentInstanceResponse:
    return AgentInstanceResponse(
        id=instance.id,
        display_name=instance.display_name,
        definition_id=instance.definition_id,
        definition_name=instance.definition_name,
        project_id=instance.project_id,
        project_name=instance.project_name,
        archived=instance.archived_at is not None,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


class AgentEnabledWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool

class AgentDefinitionImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_markdown: StrictStr = Field(min_length=1)
    name: StrictStr | None = Field(default=None, max_length=120)
    description: StrictStr | None = Field(default=None, max_length=500)
    configuration: AgentConfigurationWrite | None = None


class AgentDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_markdown: StrictStr | None = Field(default=None, min_length=1)
    name: StrictStr | None = Field(default=None, max_length=120)
    description: StrictStr | None = Field(default=None, max_length=500)
    configuration: AgentConfigurationWrite | None = None


class SkillResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: StrictStr = Field(min_length=1, max_length=256)
    content: StrictStr = Field(max_length=100_000)


class SkillWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=120)
    description: StrictStr = Field(default="", max_length=500)
    instructions: StrictStr = Field(min_length=1, max_length=1_048_576)
    dependencies: list[StrictStr] = Field(default_factory=list, max_length=100)
    resources: list[SkillResourceRequest] = Field(default_factory=list, max_length=10)


class SkillPinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: UUID
    version: int = Field(ge=1)


class DefinitionSkillsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[SkillPinRequest] = Field(max_length=100)

    @field_validator("skills")
    @classmethod
    def require_distinct_skills(cls, value: list[SkillPinRequest]) -> list[SkillPinRequest]:
        if len({item.skill_id for item in value}) != len(value):
            raise ValueError("a skill can be pinned only once per definition version")
        return value


class SkillResourceResponse(BaseModel):
    path: str
    content: str


class SkillResponse(BaseModel):
    id: UUID
    version: int
    name: str
    description: str
    instructions: str
    dependencies: list[str]
    resources: list[SkillResourceResponse]
    archived: bool
    created_at: datetime


class SkillSummaryResponse(BaseModel):
    id: UUID
    version: int
    name: str
    description: str
    dependencies: list[str]
    archived: bool
    created_at: datetime


class DefinitionSkillReferenceResponse(BaseModel):
    skill_id: UUID
    version: int
    name: str


class AgentDefinitionResponse(BaseModel):
    id: UUID
    version: int
    name: str
    description: str
    source_markdown: str
    unmapped_metadata: dict[str, object]
    configuration: dict[str, object] | None
    enabled: bool
    archived: bool
    created_at: datetime
    skills: list[DefinitionSkillReferenceResponse] = Field(default_factory=list)


class AgentDefinitionSummary(BaseModel):
    id: UUID
    version: int
    name: str
    description: str
    configured: bool
    enabled: bool
    archived: bool
    created_at: datetime


def _agent_definition_summary(definition: AgentDefinition) -> AgentDefinitionSummary:
    return AgentDefinitionSummary(
        id=definition.id,
        version=definition.version,
        name=definition.name,
        description=definition.description,
        configured=definition.configuration is not None,
        enabled=definition.enabled,
        archived=definition.archived_at is not None,
        created_at=definition.created_at,
    )


def _agent_definition_response(definition: AgentDefinition) -> AgentDefinitionResponse:
    return AgentDefinitionResponse(
        id=definition.id,
        version=definition.version,
        name=definition.name,
        description=definition.description,
        source_markdown=definition.source_markdown,
        unmapped_metadata=definition.unmapped_metadata,
        configuration=(
            None if definition.configuration is None else definition.configuration.to_json()
        ),
        enabled=definition.enabled,
        archived=definition.archived_at is not None,
        created_at=definition.created_at,
        skills=[
            DefinitionSkillReferenceResponse(
                skill_id=reference.skill_id,
                version=reference.version,
                name=reference.name,
            )
            for reference in definition.skills
        ],
    )


def _skill_summary(skill: SkillSummary) -> SkillSummaryResponse:
    return SkillSummaryResponse(
        id=skill.id,
        version=skill.version,
        name=skill.name,
        description=skill.description,
        dependencies=list(skill.dependencies),
        archived=skill.archived_at is not None,
        created_at=skill.created_at,
    )


def _skill_response(skill: Skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        version=skill.version,
        name=skill.name,
        description=skill.description,
        instructions=skill.instructions,
        dependencies=list(skill.dependencies),
        resources=[
            SkillResourceResponse(path=resource.path, content=resource.content)
            for resource in skill.resources
        ],
        archived=skill.archived_at is not None,
        created_at=skill.created_at,
    )

def _configure_logging(level: str) -> None:
    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload: dict[str, object] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
                "correlation_id": correlation_id_var.get(),
            }
            for key in ("method", "path", "status_code", "duration_ms", "dependency"):
                value = getattr(record, key, None)
                if value is not None:
                    payload[key] = value
            return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def _client_key(request: Request) -> str:
    # Nginx overwrites X-Real-IP; the API has no published host port.
    forwarded = request.headers.get("x-real-ip", "")
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return request.client.host if request.client else "unknown"



def create_http_app(
    settings: Settings,
    database_check: Callable[[], Awaitable[None]],
    lifespan: Callable,
) -> FastAPI:
    configured = settings
    _configure_logging(configured.log_level)
    sessions = OwnerSessionStore(configured.session_secret, configured.session_ttl_seconds)
    password_hasher = PasswordHasher()
    login_limiter = LoginRateLimiter(
        configured.login_rate_limit_attempts,
        configured.login_rate_limit_window_seconds,
    )
    verify_database = database_check
    application = FastAPI(
        title="Agent Office API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        details = [
            {
                key: item[key]
                for key in ("loc", "msg", "type")
                if key in item
            }
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})
    application.state.settings = configured

    def require_project_repository(request: Request) -> ProjectRepository:
        repository = getattr(request.app.state, "project_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Project storage is not ready")
        return repository

    def require_agent_registry_repository(request: Request) -> AgentRegistryRepository:
        repository = getattr(request.app.state, "agent_registry_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Agent definition storage is not ready")
        return repository

    def require_skill_repository(request: Request) -> SkillRepository:
        repository = getattr(request.app.state, "skill_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Skill storage is not ready")
        return repository

    def require_agent_instance_repository(request: Request) -> AgentInstanceRepository:
        repository = getattr(request.app.state, "agent_instance_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Agent instance storage is not ready")
        return repository

    def require_task_repository(request: Request) -> TaskRepository:
        repository = getattr(request.app.state, "task_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Task storage is not ready")
        return repository

    def require_run_repository(request: Request) -> RunRepository:
        repository = getattr(request.app.state, "run_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Run storage is not ready")
        return repository

    def require_run_result_repository(request: Request) -> RunResultRepository:
        repository = getattr(request.app.state, "run_result_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Run Result storage is not ready")
        return repository

    def require_model_endpoint_repository(request: Request) -> ModelEndpointRepository:
        repository = getattr(request.app.state, "model_endpoint_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Model Endpoint storage is not ready")
        return repository
    def optional_execution_repository(request: Request) -> ExecutionRepository | None:
        return getattr(request.app.state, "execution_repository", None)

    def require_execution_repository(request: Request) -> ExecutionRepository:
        repository = optional_execution_repository(request)
        if repository is None:
            raise HTTPException(status_code=503, detail="Execution policy storage is not ready")
        return repository

    def authenticate_runner(request: Request) -> None:
        token = configured.runner_shared_token
        authorization = request.headers.get("authorization", "")
        if token is None:
            raise HTTPException(status_code=503, detail="Runner authentication is not configured")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Runner authentication required")


    def require_activity_event_repository(request: Request) -> ActivityEventRepository:
        repository = getattr(request.app.state, "activity_event_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Activity event storage is not ready")
        return repository

    def require_approval_repository(request: Request) -> ApprovalRepository:
        repository = getattr(request.app.state, "approval_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Approval storage is not ready")
        return repository

    def require_artifact_repository(request: Request) -> ArtifactRepository:
        repository = getattr(request.app.state, "artifact_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Artifact metadata storage is not ready")
        return repository

    def require_office_scene_repository(request: Request) -> OfficeSceneRepository:
        repository = getattr(request.app.state, "office_scene_repository", None)
        if repository is None:
            raise HTTPException(status_code=503, detail="Office scene storage is not ready")
        return repository

    def optional_agent_instance_repository(request: Request) -> AgentInstanceRepository | None:
        return getattr(request.app.state, "agent_instance_repository", None)


    @application.middleware("http")
    async def request_context(request: Request, call_next: Callable[..., Awaitable[Response]]) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER, "")
        correlation_id = incoming if CORRELATION_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        context_token = correlation_id_var.set(correlation_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            if configured.session_cookie_secure:
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            correlation_id_var.reset(context_token)

    def require_origin(origin: str | None) -> None:
        if origin != configured.public_origin:
            raise HTTPException(status_code=403, detail="Origin not allowed")

    async def require_owner(request: Request) -> OwnerPrincipal:
        principal = await sessions.authenticate(request.cookies.get(SESSION_COOKIE))
        if principal is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return principal

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/ready")
    async def ready() -> JSONResponse:
        try:
            await verify_database()
        except Exception:
            logger.warning("readiness_failed", extra={"dependency": "postgres"})
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    @application.post("/internal/runner/heartbeat")
    async def runner_heartbeat(
        body: RunnerHeartbeatRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> dict[str, object]:
        authenticate_runner(request)
        await execution.register_runner(body.runner_id, provider_ready=body.provider_ready)
        return {
            "runner_id": body.runner_id,
            "provider_ready": body.provider_ready,
            "heartbeat_at": datetime.now(UTC),
        }

    @application.post(
        "/internal/runner/claim",
        response_model=RunnerClaimResponse | None,
    )
    async def runner_claim(
        body: RunnerClaimRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> RunnerClaimResponse | None:
        authenticate_runner(request)
        status = await execution.get_runner_status(body.runner_id, max_age_seconds=30)
        if status is None or not status.provider_ready:
            return None
        job = await execution.claim_next(body.runner_id, lease_seconds=120)
        if job is None:
            return None
        return RunnerClaimResponse(
            owner_id=job.owner_id,
            run_id=job.run_id,
            task_id=job.task_id,
            project_id=job.project_id,
            instance_id=job.instance_id,
            configuration_snapshot=job.configuration_snapshot,
            workspace_path=job.policy.workspace_path,
            allowed_tools=list(job.policy.allowed_tools),
            max_duration_seconds=job.policy.max_duration_seconds,
            max_inference_turns=job.policy.max_inference_turns,
            max_tool_calls=job.policy.max_tool_calls,
        )

    @application.post("/internal/runner/events")
    async def runner_event(
        body: RunnerEventRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
        events: ActivityEventRepository = Depends(require_activity_event_repository),
    ) -> dict[str, object]:
        authenticate_runner(request)
        if (body.event_type == "run.tool_action") != (body.tool_name is not None):
            raise HTTPException(status_code=422, detail="Runner tool action is invalid")
        context = await execution.get_run_context_for_runner(
            body.runner_id, body.run_id
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Run is not assigned to this Runner")
        draft = ActivityEventDraft(
            owner_id=context.owner_id,
            source_id=str(body.runner_id),
            event_id=body.event_id,
            schema_version=1,
            source_session_id=None,
            workspace_id=None,
            project_id=context.project_id,
            task_id=context.task_id,
            task_revision=context.task_revision,
            agent_instance_id=context.instance_id,
            run_id=context.run_id,
            event_type=body.event_type,
            occurred_at=body.occurred_at,
            source_sequence=body.source_sequence,
            action_summary=_RUNNER_TOOL_ACTION_SUMMARIES.get(body.tool_name),
        )
        terminal_event = body.event_type in {
            "run.succeeded",
            "run.failed",
            "run.canceled",
            "run.unknown",
        }
        try:
            if terminal_event:
                event, created = await events.append_terminal_event(
                    draft, body.runner_id
                )
            else:
                existing = await events.has_event_for_runner(
                    context.owner_id,
                    str(body.runner_id),
                    context.run_id,
                    body.event_id,
                )
                if not existing and not await execution.heartbeat_run(
                    body.runner_id, body.run_id, lease_seconds=120
                ):
                    raise EvidenceInputError("lease")
                event, created = await events.append_event(draft)
        except EvidenceInputError as error:
            if error.reason == "lease":
                raise HTTPException(
                    status_code=409, detail="Runner lease is no longer active"
                ) from error
            raise HTTPException(
                status_code=409, detail="Runner event conflicts with stored state"
            ) from error
        return {
            "event_id": event.event_id,
            "cursor": event.cursor,
            "acknowledged": True,
            "replayed": not created,
        }

    @application.post("/internal/runner/run-heartbeat")
    async def runner_run_heartbeat(
        body: RunnerRunHeartbeatRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> dict[str, bool]:
        authenticate_runner(request)
        active = await execution.heartbeat_run(
            body.runner_id, body.run_id, lease_seconds=120
        )
        return {"active": active}

    @application.post("/internal/runner/cancel-requests")
    async def runner_cancel_requests(
        body: RunnerCancellationPollRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> dict[str, list[str]]:
        authenticate_runner(request)
        run_ids = await execution.get_cancellation_requests(body.runner_id)
        return {"run_ids": [str(run_id) for run_id in run_ids]}

    @application.post("/internal/runner/lease-status")
    async def runner_lease_status(
        body: RunnerLeaseStatusRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> dict[str, list[dict[str, object]]]:
        authenticate_runner(request)
        statuses = await execution.get_lease_statuses(
            body.runner_id, body.run_ids
        )
        return {
            "runs": [
                {
                    "run_id": str(status.run_id),
                    "runner_id": (
                        str(status.runner_id) if status.runner_id is not None else None
                    ),
                    "state": status.state,
                    "active_lease": status.active_lease,
                    "lease_expires_at": status.lease_expires_at,
                    "max_duration_seconds": status.max_duration_seconds,
                    "cancellation_requested": status.cancellation_requested,
                }
                for status in statuses
            ]
        }

    @application.post("/internal/runner/results")
    async def save_runner_result(
        body: RunnerResultRequest,
        request: Request,
        execution: ExecutionRepository = Depends(require_execution_repository),
        results: RunResultRepository = Depends(require_run_result_repository),
    ) -> dict[str, bool]:
        authenticate_runner(request)
        context = await execution.get_run_context_for_runner(
            body.runner_id, body.run_id
        )
        statuses = await execution.get_lease_statuses(body.runner_id, [body.run_id])
        if (
            context is None
            or not statuses
            or statuses[0].runner_id != body.runner_id
            or not statuses[0].active_lease
        ):
            raise HTTPException(status_code=409, detail="Run lease is not active")
        try:
            await results.create_for_run(context.owner_id, body.run_id, body.text)
        except RunResultConflictError as error:
            raise HTTPException(
                status_code=409, detail="Run Result conflicts with stored result"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Run Result is invalid") from error
        return {"accepted": True}

    @application.post(
        "/api/v1/projects",
        status_code=201,
        response_model=ProjectResponse,
    )
    async def create_project(
        body: ProjectCreate,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ProjectRepository = Depends(require_project_repository),
    ) -> ProjectResponse:
        require_origin(request.headers.get("origin"))
        project = await repository.create(principal.owner_id, body.name, body.summary)
        return ProjectResponse(
            id=project.id,
            name=project.name,
            summary=project.summary,
            created_at=project.created_at,
        )

    @application.get(
        "/api/v1/projects",
        response_model=list[ProjectResponse],
    )
    async def list_projects(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ProjectRepository = Depends(require_project_repository),
    ) -> list[ProjectResponse]:
        projects = await repository.list_for_owner(
            principal.owner_id,
            limit=limit,
            offset=offset,
        )
        return [
            ProjectResponse(
                id=project.id,
                name=project.name,
                summary=project.summary,
                created_at=project.created_at,
            )
            for project in projects
        ]

    @application.get(
        "/api/v1/projects/{project_id}",
        response_model=ProjectResponse,
    )
    async def get_project(
        project_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ProjectRepository = Depends(require_project_repository),
    ) -> ProjectResponse:
        project = await repository.get_for_owner(principal.owner_id, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return ProjectResponse(
            id=project.id,
            name=project.name,
            summary=project.summary,
            created_at=project.created_at,
        )

    @application.get(
        "/api/v1/projects/{project_id}/execution-policy",
        response_model=WorkspacePolicyResponse | None,
    )
    async def get_project_execution_policy(
        project_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        projects: ProjectRepository = Depends(require_project_repository),
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> WorkspacePolicyResponse | None:
        if await projects.get_for_owner(principal.owner_id, project_id) is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        policy = await execution.get_policy_for_project(principal.owner_id, project_id)
        if policy is None:
            return None
        return WorkspacePolicyResponse(
            project_id=policy.project_id,
            workspace_path=policy.workspace_path,
            allowed_tools=list(policy.allowed_tools),
            max_duration_seconds=policy.max_duration_seconds,
            max_inference_turns=policy.max_inference_turns,
            max_tool_calls=policy.max_tool_calls,
            enabled=policy.enabled,
            revision=policy.revision,
            updated_at=policy.updated_at,
        )

    @application.put(
        "/api/v1/projects/{project_id}/execution-policy",
        response_model=WorkspacePolicyResponse,
    )
    async def save_project_execution_policy(
        project_id: UUID,
        body: WorkspacePolicyWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        projects: ProjectRepository = Depends(require_project_repository),
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> WorkspacePolicyResponse:
        require_origin(request.headers.get("origin"))
        if await projects.get_for_owner(principal.owner_id, project_id) is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        try:
            draft = WorkspacePolicyDraft.create(
                workspace_path=body.workspace_path,
                allowed_tools=body.allowed_tools,
                max_duration_seconds=body.max_duration_seconds,
                max_inference_turns=body.max_inference_turns,
                max_tool_calls=body.max_tool_calls,
                enabled=body.enabled,
            )
        except ExecutionPolicyInputError as error:
            raise HTTPException(
                status_code=422, detail=f"Invalid execution policy: {error.reason}"
            ) from error
        policy = await execution.save_policy(principal.owner_id, project_id, draft)
        if policy is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return WorkspacePolicyResponse(
            project_id=policy.project_id,
            workspace_path=policy.workspace_path,
            allowed_tools=list(policy.allowed_tools),
            max_duration_seconds=policy.max_duration_seconds,
            max_inference_turns=policy.max_inference_turns,
            max_tool_calls=policy.max_tool_calls,
            enabled=policy.enabled,
            revision=policy.revision,
            updated_at=policy.updated_at,
        )

    def raise_task_input_error(error: TaskInputError) -> None:
        if error.reason == "cycle":
            raise HTTPException(status_code=409, detail="Task dependency would create a cycle") from error
        if error.reason == "instance_storage":
            raise HTTPException(status_code=503, detail="Agent instance storage is not ready") from error
        raise HTTPException(status_code=404, detail="Resource not found") from error

    @application.post(
        "/api/v1/tasks",
        status_code=201,
        response_model=TaskResponse,
    )
    async def create_task(
        body: TaskWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        projects: ProjectRepository = Depends(require_project_repository),
        instances: AgentInstanceRepository | None = Depends(optional_agent_instance_repository),
        repository: TaskRepository = Depends(require_task_repository),
    ) -> TaskResponse:
        require_origin(request.headers.get("origin"))
        spec = _task_spec(body)
        try:
            await validate_task_references(principal.owner_id, spec, projects, instances)
            task = await repository.create(principal.owner_id, spec)
        except TaskInputError as error:
            raise_task_input_error(error)
        return _task_response(task)

    @application.get("/api/v1/tasks", response_model=list[TaskResponse])
    async def list_tasks(
        project_id: UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: TaskRepository = Depends(require_task_repository),
    ) -> list[TaskResponse]:
        tasks = await repository.list_for_owner(
            principal.owner_id, project_id=project_id, limit=limit, offset=offset
        )
        return [_task_response(task) for task in tasks]

    @application.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: TaskRepository = Depends(require_task_repository),
    ) -> TaskResponse:
        task = await repository.get_for_owner(principal.owner_id, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _task_response(task)

    @application.get(
        "/api/v1/tasks/{task_id}/preflight",
        response_model=TaskPreflightResponse,
    )
    async def get_task_preflight(
        task_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        tasks: TaskRepository = Depends(require_task_repository),
        definitions: AgentRegistryRepository = Depends(require_agent_registry_repository),
        endpoints: ModelEndpointRepository = Depends(require_model_endpoint_repository),
        instances: AgentInstanceRepository | None = Depends(optional_agent_instance_repository),
        execution: ExecutionRepository | None = Depends(optional_execution_repository),
    ) -> TaskPreflightResponse:
        workflow = TaskWorkflow(tasks, instances, definitions, endpoints, execution)
        preflight = await workflow.preflight(principal.owner_id, task_id)
        if preflight is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return TaskPreflightResponse(
            task_id=preflight.task_id,
            ready=preflight.ready,
            checks=[
                PreflightCheckResponse(key=check.key, status=check.status, detail=check.detail)
                for check in preflight.checks
            ],
        )

    @application.put("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    async def update_task(
        task_id: UUID,
        body: TaskWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        projects: ProjectRepository = Depends(require_project_repository),
        instances: AgentInstanceRepository | None = Depends(optional_agent_instance_repository),
        repository: TaskRepository = Depends(require_task_repository),
    ) -> TaskResponse:
        require_origin(request.headers.get("origin"))
        spec = _task_spec(body)
        try:
            await validate_task_references(principal.owner_id, spec, projects, instances)
            task = await repository.update_for_owner(principal.owner_id, task_id, spec)
        except TaskInputError as error:
            raise_task_input_error(error)
        if task is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _task_response(task)

    @application.get(
        "/api/v1/tasks/{task_id}/revisions",
        response_model=list[TaskResponse],
    )
    async def list_task_revisions(
        task_id: UUID,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: TaskRepository = Depends(require_task_repository),
    ) -> list[TaskResponse]:
        revisions = await repository.list_revisions_for_owner(
            principal.owner_id, task_id, limit=limit, offset=offset
        )
        if revisions is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return [_task_response(revision) for revision in revisions]

    @application.post(
        "/api/v1/tasks/{task_id}/runs",
        status_code=201,
        response_model=RunResponse,
    )
    async def create_run(
        task_id: UUID,
        body: RunCreate,
        request: Request,
        response: Response,
        principal: OwnerPrincipal = Depends(require_owner),
        tasks: TaskRepository = Depends(require_task_repository),
        definitions: AgentRegistryRepository = Depends(require_agent_registry_repository),
        endpoints: ModelEndpointRepository = Depends(require_model_endpoint_repository),
        instances: AgentInstanceRepository | None = Depends(optional_agent_instance_repository),
        execution: ExecutionRepository = Depends(require_execution_repository),
        repository: RunRepository = Depends(require_run_repository),
    ) -> RunResponse:
        require_origin(request.headers.get("origin"))
        workflow = TaskWorkflow(tasks, instances, definitions, endpoints, execution)
        preflight = await workflow.preflight(principal.owner_id, task_id)
        if preflight is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if not preflight.ready:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Run preflight is blocked.",
                    "checks": [
                        {
                            "key": check.key,
                            "status": check.status,
                            "detail": check.detail,
                        }
                        for check in preflight.checks
                    ],
                },
            )
        task = await tasks.get_for_owner(principal.owner_id, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        policy = await execution.get_policy_for_project(
            principal.owner_id, task.project_id
        )
        if policy is None or not policy.enabled:
            raise HTTPException(status_code=409, detail="Project execution policy changed")
        try:
            run, created = await repository.create_for_task(
                principal.owner_id,
                task_id,
                body.request_id,
                execution_policy_revision=policy.revision,
            )
        except RunInputError as error:
            if error.reason == "task":
                raise HTTPException(status_code=404, detail="Resource not found") from error
            if error.reason == "assignment":
                raise HTTPException(
                    status_code=409,
                    detail="Task needs an active assigned agent instance before a run can start",
                ) from error
            if error.reason == "skill":
                raise HTTPException(
                    status_code=409,
                    detail="A pinned skill is unavailable for a new run",
                ) from error
            if error.reason in {"configuration", "model_endpoint"}:
                raise HTTPException(
                    status_code=409,
                    detail="Agent Definition configuration or Model Endpoint is unavailable",
                ) from error
            if error.reason == "execution_policy":
                raise HTTPException(
                    status_code=409,
                    detail="Project execution policy changed; reload preflight and retry",
                ) from error
            raise HTTPException(
                status_code=409,
                detail="Request ID was already used for a different task",
            ) from error
        if not created:
            response.status_code = 200
        return _run_response(run)

    @application.get(
        "/api/v1/tasks/{task_id}/runs",
        response_model=list[RunResponse],
    )
    async def list_task_runs(
        task_id: UUID,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: RunRepository = Depends(require_run_repository),
    ) -> list[RunResponse]:
        runs = await repository.list_for_task(
            principal.owner_id, task_id, limit=limit, offset=offset
        )
        if runs is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return [_run_response(run) for run in runs]

    @application.get("/api/v1/runs/{run_id}", response_model=RunResponse)
    async def get_run(
        run_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: RunRepository = Depends(require_run_repository),
    ) -> RunResponse:
        run = await repository.get_for_owner(principal.owner_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _run_response(run)

    @application.post(
        "/api/v1/runs/{run_id}/cancel",
        status_code=202,
        response_model=RunCancellationResponse,
    )
    async def cancel_run(
        run_id: UUID,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        runs: RunRepository = Depends(require_run_repository),
        execution: ExecutionRepository = Depends(require_execution_repository),
    ) -> RunCancellationResponse:
        require_origin(request.headers.get("origin"))
        if await runs.get_for_owner(principal.owner_id, run_id) is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        state = await execution.request_cancellation(principal.owner_id, run_id)
        if state == "cancellation_pending":
            return RunCancellationResponse(status=state)
        if state is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        raise HTTPException(status_code=409, detail="Run is not active")

    @application.get(
        "/api/v1/runs/{run_id}/result",
        response_model=RunResultResponse,
    )
    async def get_run_result(
        run_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        runs: RunRepository = Depends(require_run_repository),
        results: RunResultRepository = Depends(require_run_result_repository),
    ) -> RunResultResponse:
        if await runs.get_for_owner(principal.owner_id, run_id) is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        result = await results.get_for_owner(principal.owner_id, run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run Result is not available")
        return RunResultResponse(
            run_id=result.run_id,
            text=result.text,
            byte_length=result.byte_length,
            created_at=result.created_at,
        )


    @application.get(
        "/api/v1/activity/snapshot",
        response_model=ActivitySnapshotResponse,
    )
    async def get_activity_snapshot(
        limit: int = Query(default=200, ge=1, le=200),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ActivityEventRepository = Depends(require_activity_event_repository),
    ) -> ActivitySnapshotResponse:
        snapshot = await repository.get_snapshot_for_owner(
            principal.owner_id, limit=limit
        )
        now = datetime.now(UTC)
        return ActivitySnapshotResponse(
            cursor=snapshot.cursor,
            projections=[
                _activity_projection_response(item, now)
                for item in snapshot.projections
            ],
        )

    @application.get(
        "/api/v1/runs/{run_id}/activity",
        response_model=RunActivityResponse,
    )
    async def get_run_activity(
        run_id: UUID,
        limit: int = Query(default=100, ge=1, le=200),
        principal: OwnerPrincipal = Depends(require_owner),
        runs: RunRepository = Depends(require_run_repository),
        repository: ActivityEventRepository = Depends(require_activity_event_repository),
    ) -> RunActivityResponse:
        if await runs.get_for_owner(principal.owner_id, run_id) is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        projection = await repository.get_projection_for_run(
            principal.owner_id, run_id
        )
        events = await repository.list_events_for_run(
            principal.owner_id, run_id, limit=limit
        )
        if events is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        now = datetime.now(UTC)
        cursor = events[-1].cursor if events else (
            projection.last_event_cursor if projection is not None else 0
        )
        return RunActivityResponse(
            run_id=run_id,
            status=None if projection is None else projection.status,
            freshness=_activity_freshness(projection, now),
            last_event_at=None if projection is None else projection.last_event_at,
            last_heartbeat_at=(
                None if projection is None else projection.last_heartbeat_at
            ),
            cursor=cursor,
            safe_action_summary=(
                None if projection is None else projection.last_action_summary
            ),
            events=[_activity_event_response(event) for event in events],
        )

    @application.get(
        "/api/v1/runs/{run_id}/approvals",
        response_model=list[ApprovalResponse],
    )
    async def list_run_approvals(
        run_id: UUID,
        limit: int = Query(default=100, ge=1, le=200),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ApprovalRepository = Depends(require_approval_repository),
    ) -> list[ApprovalResponse]:
        approvals = await repository.list_for_run(
            principal.owner_id, run_id, limit=limit
        )
        if approvals is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return [_approval_response(approval) for approval in approvals]

    @application.post(
        "/api/v1/approvals/{approval_id}/decision",
        response_model=ApprovalResponse,
    )
    async def decide_approval(
        approval_id: UUID,
        body: ApprovalDecisionWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ApprovalRepository = Depends(require_approval_repository),
    ) -> ApprovalResponse:
        require_origin(request.headers.get("origin"))
        try:
            approval = await repository.resolve(
                principal.owner_id,
                approval_id,
                body.action_digest,
                body.decision,
                datetime.now(UTC),
            )
        except EvidenceInputError as error:
            if error.reason == "approval":
                raise HTTPException(status_code=404, detail="Resource not found") from error
            if error.reason in {
                "action_digest_mismatch",
                "approval_expired",
                "approval_not_expired",
                "decision_conflict",
            }:
                raise HTTPException(
                    status_code=409,
                    detail="Approval is no longer valid for this action",
                ) from error
            raise HTTPException(status_code=422, detail="Invalid approval decision") from error
        return _approval_response(approval)

    @application.get(
        "/api/v1/runs/{run_id}/artifacts",
        response_model=list[ArtifactResponse],
    )
    async def list_run_artifacts(
        run_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ArtifactRepository = Depends(require_artifact_repository),
    ) -> list[ArtifactResponse]:
        artifacts = await repository.list_for_run(principal.owner_id, run_id)
        if artifacts is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return [_artifact_response(artifact) for artifact in artifacts]

    @application.get(
        "/api/v1/office-scenes/{scene_id}",
        response_model=OfficeSceneVersionResponse,
    )
    async def get_office_scene(
        scene_id: str,
        version: int | None = Query(default=None, ge=1),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: OfficeSceneRepository = Depends(require_office_scene_repository),
    ) -> OfficeSceneVersionResponse:
        try:
            scene = await repository.get_version(principal.owner_id, scene_id, version)
        except EvidenceInputError as error:
            raise HTTPException(status_code=422, detail="Invalid scene identifier") from error
        if scene is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _office_scene_response(scene)

    @application.post(
        "/api/v1/office-scenes/{scene_id}/versions",
        status_code=201,
        response_model=OfficeSceneVersionResponse,
    )
    async def create_office_scene_version(
        scene_id: str,
        body: OfficeSceneVersionWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: OfficeSceneRepository = Depends(require_office_scene_repository),
    ) -> OfficeSceneVersionResponse:
        require_origin(request.headers.get("origin"))
        try:
            scene = await repository.create_version(
                principal.owner_id,
                scene_id,
                body.contents,
                body.expected_previous_version,
            )
        except EvidenceInputError as error:
            if error.reason == "scene_version_conflict":
                raise HTTPException(
                    status_code=409, detail="Office scene version changed"
                ) from error
            raise HTTPException(status_code=422, detail="Invalid office scene version") from error
        return _office_scene_response(scene)
    @application.get("/api/v1/model-endpoints", response_model=list[ModelEndpointResponse])
    async def list_model_endpoints(
        include_archived: bool = False,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> list[ModelEndpointResponse]:
        endpoints = await repository.list_for_owner(
            principal.owner_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return [_model_endpoint_response(endpoint) for endpoint in endpoints]

    @application.post(
        "/api/v1/model-endpoints",
        status_code=201,
        response_model=ModelEndpointResponse,
    )
    async def create_model_endpoint(
        body: ModelEndpointWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> ModelEndpointResponse:
        require_origin(request.headers.get("origin"))
        try:
            draft = ModelEndpointDraft.create(
                body.name,
                body.endpoint_url,
                body.credential_env,
                body.default_model_id,
                body.default_route,
            )
        except ModelEndpointInputError as error:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid Model Endpoint field: {error.reason}",
            ) from error
        endpoint = await repository.create(principal.owner_id, draft)
        return _model_endpoint_response(endpoint)

    @application.post(
        "/api/v1/model-endpoints/{endpoint_id}/archive",
        response_model=ModelEndpointResponse,
    )
    async def archive_model_endpoint(
        endpoint_id: UUID,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> ModelEndpointResponse:
        require_origin(request.headers.get("origin"))
        endpoint = await repository.archive_for_owner(principal.owner_id, endpoint_id)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _model_endpoint_response(endpoint)
    @application.post(
        "/api/v1/agent-instances",
        status_code=201,
        response_model=AgentInstanceResponse,
    )
    async def create_agent_instance(
        body: AgentInstanceCreate,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        project_repository: ProjectRepository = Depends(require_project_repository),
        definition_repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
        repository: AgentInstanceRepository = Depends(require_agent_instance_repository),
    ) -> AgentInstanceResponse:
        require_origin(request.headers.get("origin"))
        definition = await definition_repository.get_for_owner(
            principal.owner_id, body.definition_id
        )
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if definition.archived_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Archived definitions cannot be assigned to new instances",
            )
        if not definition.enabled:
            raise HTTPException(
                status_code=409,
                detail="Disabled definitions cannot be assigned to new instances",
            )
        if body.project_id is not None:
            project = await project_repository.get_for_owner(
                principal.owner_id, body.project_id
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Resource not found")
        try:
            instance = await repository.create(
                principal.owner_id,
                body.display_name,
                body.definition_id,
                body.project_id,
            )
        except AgentInstanceInputError as error:
            raise HTTPException(status_code=404, detail="Resource not found") from error
        return _agent_instance_response(instance)

    @application.get(
        "/api/v1/agent-instances",
        response_model=list[AgentInstanceResponse],
    )
    async def list_agent_instances(
        include_archived: bool = False,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        project_id: UUID | None = None,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentInstanceRepository = Depends(require_agent_instance_repository),
    ) -> list[AgentInstanceResponse]:
        instances = await repository.list_for_owner(
            principal.owner_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
            project_id=project_id,
        )
        return [_agent_instance_response(instance) for instance in instances]

    @application.get(
        "/api/v1/agent-instances/{instance_id}",
        response_model=AgentInstanceResponse,
    )
    async def get_agent_instance(
        instance_id: UUID,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentInstanceRepository = Depends(require_agent_instance_repository),
    ) -> AgentInstanceResponse:
        instance = await repository.get_for_owner(principal.owner_id, instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_instance_response(instance)

    @application.put(
        "/api/v1/agent-instances/{instance_id}",
        response_model=AgentInstanceResponse,
    )
    async def update_agent_instance(
        instance_id: UUID,
        body: AgentInstanceUpdate,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        project_repository: ProjectRepository = Depends(require_project_repository),
        definition_repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
        repository: AgentInstanceRepository = Depends(require_agent_instance_repository),
    ) -> AgentInstanceResponse:
        require_origin(request.headers.get("origin"))
        if not body.model_fields_set:
            raise HTTPException(status_code=422, detail="At least one instance field is required")
        if any(
            getattr(body, field) is None
            for field in body.model_fields_set
            if field != "project_id"
        ):
            raise HTTPException(status_code=422, detail="Instance fields cannot be null")
        current = await repository.get_for_owner(principal.owner_id, instance_id)
        if current is None or current.archived_at is not None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if (
            "definition_id" in body.model_fields_set
            and body.definition_id != current.definition_id
        ):
            definition = await definition_repository.get_for_owner(
                principal.owner_id, body.definition_id  # type: ignore[arg-type]
            )
            if definition is None:
                raise HTTPException(status_code=404, detail="Resource not found")
            if definition.archived_at is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Archived definitions cannot be assigned to instances",
                )
            if not definition.enabled:
                raise HTTPException(
                    status_code=409,
                    detail="Disabled definitions cannot be assigned to instances",
                )
        if body.project_id is not None:
            project = await project_repository.get_for_owner(
                principal.owner_id, body.project_id
            )
            if project is None:
                raise HTTPException(status_code=404, detail="Resource not found")
        try:
            instance = await repository.update_for_owner(
                principal.owner_id,
                instance_id,
                body.model_dump(exclude_unset=True),
            )
        except AgentInstanceInputError as error:
            raise HTTPException(status_code=404, detail="Resource not found") from error
        if instance is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_instance_response(instance)

    @application.post(
        "/api/v1/agent-instances/{instance_id}/archive",
        response_model=AgentInstanceResponse,
    )
    async def archive_agent_instance(
        instance_id: UUID,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentInstanceRepository = Depends(require_agent_instance_repository),
    ) -> AgentInstanceResponse:
        require_origin(request.headers.get("origin"))
        instance = await repository.archive_for_owner(principal.owner_id, instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_instance_response(instance)

    @application.post(
        "/api/v1/agent-definitions/import",
        status_code=201,
        response_model=AgentDefinitionResponse,
    )
    async def import_agent_definition(
        body: AgentDefinitionImport,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
        endpoints: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        try:
            name, description, unmapped = await run_in_threadpool(
                parse_agent_markdown,
                body.source_markdown,
                name=body.name,
                description=body.description,
            )
            configuration = None if body.configuration is None else body.configuration.to_domain()
            if configuration is not None:
                endpoint = await endpoints.get_for_owner(
                    principal.owner_id, configuration.model_endpoint_id
                )
                if endpoint is None or endpoint.archived_at is not None:
                    raise AgentConfigurationInputError("model_endpoint_id")
            definition = await repository.create(
                principal.owner_id,
                name,
                description,
                body.source_markdown,
                unmapped,
                configuration,
            )
        except AgentRegistryConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentDefinitionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except AgentConfigurationInputError as error:
            raise HTTPException(
                status_code=422, detail=f"Invalid agent configuration field: {error.reason}"
            ) from error
        return _agent_definition_response(definition)

    @application.get(
        "/api/v1/agent-definitions",
        response_model=list[AgentDefinitionSummary],
    )
    async def list_agent_definitions(
        include_archived: bool = False,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
    ) -> list[AgentDefinitionSummary]:
        definitions = await repository.list_for_owner(
            principal.owner_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return [_agent_definition_summary(definition) for definition in definitions]

    @application.get(
        "/api/v1/agent-definitions/{definition_id}",
        response_model=AgentDefinitionResponse,
    )
    async def get_agent_definition(
        definition_id: UUID,
        version: int | None = Query(default=None, ge=1),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
    ) -> AgentDefinitionResponse:
        definition = await repository.get_for_owner(
            principal.owner_id, definition_id, version=version
        )
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_definition_response(definition)

    @application.get("/api/v1/agent-definitions/{definition_id}/export")
    async def export_agent_definition(
        definition_id: UUID,
        version: int | None = Query(default=None, ge=1),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
    ) -> Response:
        definition = await repository.get_for_owner(
            principal.owner_id, definition_id, version=version
        )
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return Response(
            content=definition.source_markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="agent-definition.md"'},
        )

    @application.put(
        "/api/v1/agent-definitions/{definition_id}",
        response_model=AgentDefinitionResponse,
    )
    async def update_agent_definition(
        definition_id: UUID,
        body: AgentDefinitionUpdate,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
        endpoints: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        if not body.model_fields_set:
            raise HTTPException(status_code=422, detail="At least one definition field is required")
        if any(getattr(body, field) is None for field in body.model_fields_set):
            raise HTTPException(status_code=422, detail="Definition fields cannot be null")
        current = await repository.get_for_owner(principal.owner_id, definition_id)
        if current is None or current.archived_at is not None:
            raise HTTPException(status_code=404, detail="Resource not found")
        changes: dict[str, object] = {}
        if "source_markdown" in body.model_fields_set:
            try:
                name, description, unmapped = await run_in_threadpool(
                    parse_agent_markdown,
                    body.source_markdown or "",
                    name=body.name,
                    description=body.description,
                    fallback_name=current.name,
                    fallback_description=current.description,
                )
            except AgentDefinitionInputError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            changes.update(
                name=name,
                description=description,
                source_markdown=body.source_markdown,
                unmapped_metadata=unmapped,
            )
        else:
            if body.name is not None:
                changes["name"] = body.name.strip()
            if body.description is not None:
                changes["description"] = body.description.strip()
                if len(changes["description"]) > 500:
                    raise HTTPException(status_code=422, detail="description must be at most 500 characters")
            if "name" in changes and not changes["name"]:
                raise HTTPException(status_code=422, detail="name must not be blank")
        try:
            if body.configuration is not None:
                configuration = body.configuration.to_domain()
                endpoint = await endpoints.get_for_owner(
                    principal.owner_id, configuration.model_endpoint_id
                )
                if endpoint is None or endpoint.archived_at is not None:
                    raise AgentConfigurationInputError("model_endpoint_id")
                changes["configuration"] = configuration
            definition = await repository.update_for_owner(
                principal.owner_id, definition_id, changes
            )
        except AgentRegistryConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentDefinitionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except AgentConfigurationInputError as error:
            raise HTTPException(
                status_code=422, detail=f"Invalid agent configuration field: {error.reason}"
            ) from error
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_definition_response(definition)

    @application.post(
        "/api/v1/agent-definitions/{definition_id}/archive",
        response_model=AgentDefinitionResponse,
    )
    async def archive_agent_definition(
        definition_id: UUID,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        definition = await repository.archive_for_owner(principal.owner_id, definition_id)
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_definition_response(definition)
    @application.put(
        "/api/v1/agent-definitions/{definition_id}/enabled",
        response_model=AgentDefinitionResponse,
    )
    async def set_agent_definition_enabled(
        definition_id: UUID,
        body: AgentEnabledWrite,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
        endpoints: ModelEndpointRepository = Depends(require_model_endpoint_repository),
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        if body.enabled:
            current = await repository.get_for_owner(principal.owner_id, definition_id)
            if current is None or current.archived_at is not None:
                raise HTTPException(status_code=404, detail="Resource not found")
            if current.configuration is None:
                raise HTTPException(status_code=409, detail="Configure the Agent Definition before enabling it")
            endpoint = await endpoints.get_for_owner(
                principal.owner_id, current.configuration.model_endpoint_id
            )
            if endpoint is None or endpoint.archived_at is not None:
                raise HTTPException(status_code=409, detail="Configure an active Model Endpoint before enabling the definition")
        try:
            definition = await repository.set_enabled_for_owner(
                principal.owner_id, definition_id, body.enabled
            )
        except (AgentRegistryConflictError, AgentDefinitionInputError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_definition_response(definition)


    @application.put(
        "/api/v1/agent-definitions/{definition_id}/skills",
        response_model=AgentDefinitionResponse,
    )
    async def set_agent_definition_skills(
        definition_id: UUID,
        body: DefinitionSkillsRequest,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: AgentRegistryRepository = Depends(require_agent_registry_repository),
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        try:
            definition = await repository.set_skills_for_owner(
                principal.owner_id,
                definition_id,
                [SkillPin(item.skill_id, item.version) for item in body.skills],
            )
        except AgentDefinitionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if definition is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _agent_definition_response(definition)

    @application.post(
        "/api/v1/skills/import",
        status_code=201,
        response_model=SkillResponse,
    )
    async def import_skill(
        body: SkillWriteRequest,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> SkillResponse:
        require_origin(request.headers.get("origin"))
        try:
            content = await run_in_threadpool(
                normalize_skill_content,
                body.name,
                body.description,
                body.instructions,
                body.dependencies,
                [resource.model_dump() for resource in body.resources],
            )
            skill = await repository.create(principal.owner_id, content)
        except SkillInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except NameConflictError as error:
            raise HTTPException(status_code=409, detail="A skill with this name already exists") from error
        return _skill_response(skill)

    @application.get(
        "/api/v1/skills",
        response_model=list[SkillSummaryResponse],
    )
    async def list_skills(
        include_archived: bool = False,
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> list[SkillSummaryResponse]:
        skills = await repository.list_for_owner(
            principal.owner_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return [_skill_summary(skill) for skill in skills]

    @application.get("/api/v1/skills/{skill_id}/export")
    async def export_skill(
        skill_id: UUID,
        version: int | None = Query(default=None, ge=1),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> JSONResponse:
        skill = await repository.get_for_owner(principal.owner_id, skill_id, version=version)
        if skill is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return JSONResponse(
            content=_skill_response(skill).model_dump(mode="json"),
            headers={"Content-Disposition": 'attachment; filename="skill.json"'},
        )

    @application.get(
        "/api/v1/skills/{skill_id}",
        response_model=SkillResponse,
    )
    async def get_skill(
        skill_id: UUID,
        version: int | None = Query(default=None, ge=1),
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> SkillResponse:
        skill = await repository.get_for_owner(principal.owner_id, skill_id, version=version)
        if skill is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _skill_response(skill)

    @application.put(
        "/api/v1/skills/{skill_id}",
        response_model=SkillResponse,
    )
    async def update_skill(
        skill_id: UUID,
        body: SkillWriteRequest,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> SkillResponse:
        require_origin(request.headers.get("origin"))
        try:
            content = await run_in_threadpool(
                normalize_skill_content,
                body.name,
                body.description,
                body.instructions,
                body.dependencies,
                [resource.model_dump() for resource in body.resources],
            )
            skill = await repository.update_for_owner(principal.owner_id, skill_id, content)
        except SkillInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except NameConflictError as error:
            raise HTTPException(status_code=409, detail="A skill with this name already exists") from error
        if skill is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _skill_response(skill)

    @application.post(
        "/api/v1/skills/{skill_id}/archive",
        response_model=SkillResponse,
    )
    async def archive_skill(
        skill_id: UUID,
        request: Request,
        principal: OwnerPrincipal = Depends(require_owner),
        repository: SkillRepository = Depends(require_skill_repository),
    ) -> SkillResponse:
        require_origin(request.headers.get("origin"))
        skill = await repository.archive_for_owner(principal.owner_id, skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return _skill_response(skill)

    @application.post("/api/v1/auth/login")
    async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        require_origin(request.headers.get("origin"))
        client_key = _client_key(request)
        if not await login_limiter.reserve_attempt(client_key):
            raise HTTPException(status_code=429, detail="Too many sign-in attempts")

        reserved = True
        failed: bool | None = None
        try:
            username_matches = hmac.compare_digest(
                body.username.encode("utf-8"), configured.owner_username.encode("utf-8")
            )
            try:
                password_matches = await run_in_threadpool(
                    password_hasher.verify,
                    configured.owner_password_hash,
                    body.password,
                )
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                password_matches = False

            if not username_matches or not password_matches:
                failed = True
                raise HTTPException(status_code=401, detail="Invalid credentials")

            await login_limiter.finish_attempt(client_key, failed=False)
            reserved = False
        finally:
            if reserved:
                await login_limiter.finish_attempt(client_key, failed=failed)

        session_token = await sessions.issue(configured.owner_username)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=session_token,
            max_age=configured.session_ttl_seconds,
            httponly=True,
            secure=configured.session_cookie_secure,
            samesite=configured.session_cookie_samesite,
            path="/",
        )
        return {"username": configured.owner_username}

    @application.get("/api/v1/auth/me")
    async def current_owner(principal: OwnerPrincipal = Depends(require_owner)) -> dict[str, str]:
        return {"owner_id": principal.owner_id, "username": principal.username}

    @application.post("/api/v1/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        require_origin(request.headers.get("origin"))
        await sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(
            key=SESSION_COOKIE,
            path="/",
            secure=configured.session_cookie_secure,
            httponly=True,
            samesite=configured.session_cookie_samesite,
        )
        return {"ok": True}

    @application.websocket("/api/v1/ws")
    async def authenticated_socket(websocket: WebSocket) -> None:
        if websocket.headers.get("origin") != configured.public_origin:
            await websocket.close(code=1008)
            return
        session_token = websocket.cookies.get(SESSION_COOKIE)
        principal = await sessions.authenticate(session_token)
        if principal is None:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        expires_at = await sessions.register_socket(session_token, websocket)
        if expires_at is None:
            await websocket.close(code=1008)
            return

        queue: asyncio.Queue[dict[str, object]] | None = None
        event_cursor = 0
        receive_task = asyncio.create_task(websocket.receive_json())

        async def send_persisted_events(
            after_cursor: int,
            through_cursor: int | None = None,
            *,
            message_type: str = "activity_events",
        ) -> int:
            cursor = after_cursor
            while True:
                events = await repository.list_events_after(
                    principal.owner_id, cursor, limit=200
                )
                if through_cursor is not None:
                    events = [event for event in events if event.cursor <= through_cursor]
                if not events:
                    return cursor
                await websocket.send_json(
                    {
                        "type": message_type,
                        "cursor": events[-1].cursor,
                        "events": [
                            _activity_event_response(event).model_dump(mode="json")
                            for event in events
                        ],
                    }
                )
                cursor = events[-1].cursor
                if len(events) < 200 or (
                    through_cursor is not None and cursor >= through_cursor
                ):
                    return cursor

        try:
            await websocket.send_json({"type": "session_ready", "owner_id": principal.owner_id})
            while True:
                timeout = max(0.0, expires_at - time.monotonic())
                queue_task = asyncio.create_task(queue.get()) if queue is not None else None
                waiters = {receive_task}
                if queue_task is not None:
                    waiters.add(queue_task)
                done, pending = await asyncio.wait(
                    waiters,
                    timeout=min(timeout, 15.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if queue_task is not None and queue_task not in done:
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)
                if not done:
                    if await sessions.authenticate(session_token) is None or timeout <= 15.0:
                        await websocket.close(code=1008)
                        return
                    continue

                if receive_task in done:
                    message = receive_task.result()
                    receive_task = asyncio.create_task(websocket.receive_json())
                    if await sessions.authenticate(session_token) is None:
                        await websocket.close(code=1008)
                        return
                    if message == {"type": "ping"}:
                        await websocket.send_json({"type": "pong"})
                    elif (
                        isinstance(message, dict)
                        and message.get("type") == "subscribe"
                        and set(message) in ({"type"}, {"type", "after_cursor"})
                    ):
                        after_cursor = message.get("after_cursor")
                        if after_cursor is not None and (
                            not isinstance(after_cursor, int)
                            or isinstance(after_cursor, bool)
                            or after_cursor < 0
                        ):
                            await websocket.close(code=1003)
                            return
                        if queue is not None:
                            websocket.app.state.activity_event_hub.unsubscribe(
                                principal.owner_id, queue
                            )
                        hub = getattr(websocket.app.state, "activity_event_hub", None)
                        queue = hub.subscribe(principal.owner_id) if hub is not None else None
                        repository = getattr(
                            websocket.app.state, "activity_event_repository", None
                        )
                        if repository is None:
                            await websocket.send_json(
                                {"type": "activity_unavailable", "cursor": event_cursor}
                            )
                            continue
                        snapshot = await repository.get_snapshot_for_owner(
                            principal.owner_id, limit=200
                        )
                        if after_cursor is not None:
                            await send_persisted_events(
                                min(after_cursor, snapshot.cursor),
                                snapshot.cursor,
                                message_type="activity_replay",
                            )
                        projection_response = ActivitySnapshotResponse(
                            cursor=snapshot.cursor,
                            projections=[
                                _activity_projection_response(item, datetime.now(UTC))
                                for item in snapshot.projections
                            ],
                        )
                        await websocket.send_json(
                            {
                                "type": "activity_snapshot",
                                **projection_response.model_dump(mode="json"),
                            }
                        )
                        event_cursor = snapshot.cursor
                        event_cursor = await send_persisted_events(event_cursor)
                    else:
                        await websocket.close(code=1003)
                        return

                if queue_task is not None and queue_task in done:
                    if await sessions.authenticate(session_token) is None:
                        await websocket.close(code=1008)
                        return
                    raw_event = queue_task.result()
                    event = ActivityEventResponse.model_validate(raw_event)
                    if event.owner_id != principal.owner_id or event.sequence <= event_cursor:
                        continue
                    if event.sequence > event_cursor + 1:
                        event_cursor = await send_persisted_events(
                            event_cursor,
                            event.sequence,
                            message_type="activity_replay",
                        )
                        await websocket.send_json({"type": "activity_replay_complete"})
                    if event.sequence > event_cursor:
                        await websocket.send_json(
                            {
                                "type": "activity_events",
                                "cursor": event.sequence,
                                "events": [event.model_dump(mode="json")],
                            }
                        )
                        event_cursor = event.sequence
        except (WebSocketDisconnect, RuntimeError):
            return
        except (ValueError, TypeError):
            await websocket.close(code=1003)
        finally:
            receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)
            if queue is not None:
                hub = getattr(websocket.app.state, "activity_event_hub", None)
                if hub is not None:
                    hub.unsubscribe(principal.owner_id, queue)
            await sessions.unregister_socket(session_token, websocket)
    return application


