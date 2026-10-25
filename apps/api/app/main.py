"""Private owner API for authenticated sessions and scoped domain resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
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
from typing import AsyncIterator, Awaitable, Callable
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.config import Settings, load_settings
from app.domain import SkillPin
from app.skills import (
    PostgresSkillRepository,
    Skill,
    SkillInputError,
    SkillRepository,
    SkillSummary,
    normalize_skill_content,
)
from app.instances import (
    AgentInstance,
    AgentInstanceInputError,
    AgentInstanceRepository,
    PostgresAgentInstanceRepository,
)
from app.projects import PostgresProjectRepository, ProjectRepository
from app.tasks import (
    PostgresTaskRepository,
    Task,
    TaskInputError,
    TaskRepository,
    TaskSpec,
)
from app.runs import PostgresRunRepository, Run, RunInputError, RunRepository
from app.registry import (
    AgentDefinition,
    AgentDefinitionInputError,
    AgentRegistryRepository,
    PostgresAgentRegistryRepository,
    parse_agent_markdown,
)

SESSION_COOKIE = "agent_office_session"
CORRELATION_HEADER = "X-Correlation-ID"
CORRELATION_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
logger = logging.getLogger("agent_office.api")


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


class AgentDefinitionImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_markdown: StrictStr = Field(min_length=1)
    name: StrictStr | None = Field(default=None, max_length=120)
    description: StrictStr | None = Field(default=None, max_length=500)


class AgentDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_markdown: StrictStr | None = Field(default=None, min_length=1)
    name: StrictStr | None = Field(default=None, max_length=120)
    description: StrictStr | None = Field(default=None, max_length=500)


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
    archived: bool
    created_at: datetime
    skills: list[DefinitionSkillReferenceResponse] = Field(default_factory=list)


class AgentDefinitionSummary(BaseModel):
    id: UUID
    version: int
    name: str
    description: str
    archived: bool
    created_at: datetime


def _agent_definition_summary(definition: AgentDefinition) -> AgentDefinitionSummary:
    return AgentDefinitionSummary(
        id=definition.id,
        version=definition.version,
        name=definition.name,
        description=definition.description,
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



async def _check_postgres(database_url: str) -> None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn=dsn, timeout=2)
    try:
        await connection.fetchval("SELECT 1")
    finally:
        await connection.close()


def create_app(
    settings: Settings | None = None,
    database_check: Callable[[], Awaitable[None]] | None = None,
    project_repository: ProjectRepository | None = None,
    agent_registry_repository: AgentRegistryRepository | None = None,
    skill_repository: SkillRepository | None = None,
    agent_instance_repository: AgentInstanceRepository | None = None,
    task_repository: TaskRepository | None = None,
    run_repository: RunRepository | None = None,
) -> FastAPI:
    configured = settings or load_settings()
    _configure_logging(configured.log_level)
    sessions = OwnerSessionStore(configured.session_secret, configured.session_ttl_seconds)
    password_hasher = PasswordHasher()
    login_limiter = LoginRateLimiter(
        configured.login_rate_limit_attempts,
        configured.login_rate_limit_window_seconds,
    )
    verify_database = database_check or (lambda: _check_postgres(configured.database_url))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if (
            project_repository is not None
            or agent_registry_repository is not None
            or skill_repository is not None
            or agent_instance_repository is not None
            or task_repository is not None
            or run_repository is not None
        ):
            application.state.project_repository = project_repository
            application.state.agent_registry_repository = agent_registry_repository
            application.state.skill_repository = skill_repository
            application.state.agent_instance_repository = agent_instance_repository
            application.state.task_repository = task_repository
            application.state.run_repository = run_repository
            yield
            return
        dsn = configured.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
        application.state.project_repository = PostgresProjectRepository(pool)
        application.state.agent_registry_repository = PostgresAgentRegistryRepository(pool)
        application.state.skill_repository = PostgresSkillRepository(pool)
        application.state.agent_instance_repository = PostgresAgentInstanceRepository(pool)
        application.state.task_repository = PostgresTaskRepository(pool)
        application.state.run_repository = PostgresRunRepository(pool)
        try:
            yield
        finally:
            await pool.close()

    application = FastAPI(
        title="Agent Office API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
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

    async def validate_task_references(
        principal: OwnerPrincipal,
        body: TaskWrite,
        projects: ProjectRepository,
        instances: AgentInstanceRepository | None,
    ) -> None:
        project = await projects.get_for_owner(principal.owner_id, body.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if body.agent_instance_id is not None:
            if instances is None:
                raise HTTPException(status_code=503, detail="Agent instance storage is not ready")
            instance = await instances.get_for_owner(principal.owner_id, body.agent_instance_id)
            if instance is None or instance.archived_at is not None:
                raise HTTPException(status_code=404, detail="Resource not found")

    def raise_task_input_error(error: TaskInputError) -> None:
        if error.reason == "cycle":
            raise HTTPException(status_code=409, detail="Task dependency would create a cycle") from error
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
        await validate_task_references(principal, body, projects, instances)
        try:
            task = await repository.create(principal.owner_id, _task_spec(body))
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
        await validate_task_references(principal, body, projects, instances)
        try:
            task = await repository.update_for_owner(principal.owner_id, task_id, _task_spec(body))
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
        repository: RunRepository = Depends(require_run_repository),
    ) -> RunResponse:
        require_origin(request.headers.get("origin"))
        try:
            run, created = await repository.create_for_task(
                principal.owner_id, task_id, body.request_id
            )
        except RunInputError as error:
            if error.reason == "task":
                raise HTTPException(status_code=404, detail="Resource not found") from error
            if error.reason == "assignment":
                raise HTTPException(
                    status_code=409,
                    detail="Task needs an active assigned agent instance before a run can be recorded",
                ) from error
            if error.reason == "skill":
                raise HTTPException(
                    status_code=409,
                    detail="A pinned skill is unavailable for a new run",
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
    ) -> AgentDefinitionResponse:
        require_origin(request.headers.get("origin"))
        try:
            name, description, unmapped = await run_in_threadpool(
                parse_agent_markdown,
                body.source_markdown,
                name=body.name,
                description=body.description,
            )
            definition = await repository.create(
                principal.owner_id,
                name,
                description,
                body.source_markdown,
                unmapped,
            )
        except AgentDefinitionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except asyncpg.UniqueViolationError as error:
            raise HTTPException(status_code=409, detail="An agent definition with this name already exists") from error
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
            definition = await repository.update_for_owner(
                principal.owner_id, definition_id, changes
            )
        except AgentDefinitionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except asyncpg.UniqueViolationError as error:
            raise HTTPException(status_code=409, detail="An agent definition with this name already exists") from error
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
        except asyncpg.UniqueViolationError as error:
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
        except asyncpg.UniqueViolationError as error:
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

        try:
            await websocket.send_json({"type": "session_ready", "owner_id": principal.owner_id})
            while True:
                timeout = max(0.0, expires_at - time.monotonic())
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=timeout)
                except asyncio.TimeoutError:
                    await websocket.close(code=1008)
                    return
                if await sessions.authenticate(session_token) is None:
                    await websocket.close(code=1008)
                    return
                if message != {"type": "ping"}:
                    await websocket.close(code=1003)
                    return
                await websocket.send_json({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            return
        except (ValueError, TypeError):
            await websocket.close(code=1003)
        finally:
            await sessions.unregister_socket(session_token, websocket)
    return application


app = create_app()
