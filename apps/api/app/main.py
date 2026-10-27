from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable

import asyncpg
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, load_settings
from app.domain.evidence import (
    ActivityEventRepository,
    ApprovalRepository,
    ArtifactRepository,
    OfficeSceneRepository,
)
from app.domain.execution import ExecutionRepository
from app.domain.instances import AgentInstanceRepository
from app.domain.projects import ProjectRepository
from app.domain.provider_config import ProviderConfigRepository
from app.domain.registry import AgentRegistryRepository
from app.domain.runs import RunRepository
from app.domain.skills import SkillRepository
from app.domain.tasks import TaskRepository
from app.infrastructure.postgres.evidence import (
    PostgresActivityEventRepository,
    PostgresApprovalRepository,
    PostgresArtifactRepository,
    PostgresOfficeSceneRepository,
)
from app.infrastructure.postgres.execution import PostgresExecutionRepository
from app.infrastructure.postgres.instances import PostgresAgentInstanceRepository
from app.infrastructure.postgres.projects import PostgresProjectRepository
from app.infrastructure.postgres.provider_config import PostgresProviderConfigRepository
from app.infrastructure.postgres.registry import PostgresAgentRegistryRepository
from app.infrastructure.postgres.runs import PostgresRunRepository
from app.infrastructure.postgres.skills import PostgresSkillRepository
from app.infrastructure.postgres.tasks import PostgresTaskRepository
from app.presentation.http.application import create_http_app


async def _check_postgres(database_url: str) -> None:
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn=dsn, timeout=2)
    try:
        await connection.fetchval("SELECT 1")
    finally:
        await connection.close()


def _sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise RuntimeError("DATABASE_URL must use PostgreSQL")



def create_app(
    settings: Settings | None = None,
    database_check: Callable[[], Awaitable[None]] | None = None,
    project_repository: ProjectRepository | None = None,
    agent_registry_repository: AgentRegistryRepository | None = None,
    skill_repository: SkillRepository | None = None,
    agent_instance_repository: AgentInstanceRepository | None = None,
    task_repository: TaskRepository | None = None,
    run_repository: RunRepository | None = None,
    provider_config_repository: ProviderConfigRepository | None = None,
    execution_repository: ExecutionRepository | None = None,
    activity_event_repository: ActivityEventRepository | None = None,
    approval_repository: ApprovalRepository | None = None,
    artifact_repository: ArtifactRepository | None = None,
    office_scene_repository: OfficeSceneRepository | None = None,
) -> FastAPI:
    configured = settings or load_settings()
    verify_database = database_check or (
        lambda: _check_postgres(configured.database_url)
    )
    overrides = (
        ("project_repository", project_repository),
        ("agent_registry_repository", agent_registry_repository),
        ("skill_repository", skill_repository),
        ("agent_instance_repository", agent_instance_repository),
        ("task_repository", task_repository),
        ("run_repository", run_repository),
        ("provider_config_repository", provider_config_repository),
        ("activity_event_repository", activity_event_repository),
        ("execution_repository", execution_repository),
        ("approval_repository", approval_repository),
        ("artifact_repository", artifact_repository),
        ("office_scene_repository", office_scene_repository),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if any(repository is not None for _, repository in overrides):
            for name, repository in overrides:
                setattr(application.state, name, repository)
            yield
            return

        engine = create_async_engine(
            _sqlalchemy_url(configured.database_url),
            pool_size=10,
            max_overflow=0,
            pool_pre_ping=True,
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        application.state.database_engine = engine
        application.state.session_factory = sessions
        application.state.project_repository = PostgresProjectRepository(sessions)
        application.state.agent_registry_repository = PostgresAgentRegistryRepository(sessions)
        application.state.skill_repository = PostgresSkillRepository(sessions)
        application.state.agent_instance_repository = PostgresAgentInstanceRepository(sessions)
        application.state.task_repository = PostgresTaskRepository(sessions)
        application.state.run_repository = PostgresRunRepository(sessions)
        application.state.provider_config_repository = PostgresProviderConfigRepository(sessions)
        application.state.execution_repository = PostgresExecutionRepository(sessions)
        application.state.activity_event_repository = PostgresActivityEventRepository(sessions)
        application.state.approval_repository = PostgresApprovalRepository(sessions)
        application.state.artifact_repository = PostgresArtifactRepository(sessions)
        application.state.office_scene_repository = PostgresOfficeSceneRepository(sessions)
        try:
            yield
        finally:
            await engine.dispose()

    return create_http_app(configured, verify_database, lifespan)


app = create_app()
