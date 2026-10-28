from __future__ import annotations

import asyncio
import os
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.common import SkillPin
from app.domain.evidence import (
    ActivityEventDraft,
    ApprovalRequestDraft,
    ArtifactDraft,
    EvidenceInputError,
    action_digest,
)
from app.domain.instances import AgentInstanceInputError
from app.domain.registry import AgentRegistryConflictError
from app.infrastructure.postgres.evidence import (
    PostgresActivityEventRepository,
    PostgresApprovalRepository,
    PostgresArtifactRepository,
    PostgresOfficeSceneRepository,
)
from app.infrastructure.postgres.instances import PostgresAgentInstanceRepository
from app.main import create_app
from app.infrastructure.postgres.projects import PostgresProjectRepository
from app.domain.agent_configuration import AgentDefinitionConfiguration
from app.domain.model_endpoints import ModelEndpointDraft
from app.infrastructure.postgres.model_endpoints import PostgresModelEndpointRepository
from app.infrastructure.postgres.registry import PostgresAgentRegistryRepository
from app.domain.execution import WorkspacePolicyDraft
from app.infrastructure.postgres.execution import PostgresExecutionRepository
from app.domain.runs import RunInputError
from app.infrastructure.postgres.runs import PostgresRunRepository
from app.domain.skills import SkillContent, TextResource
from app.infrastructure.postgres.skills import PostgresSkillRepository
from app.domain.tasks import TaskInputError, TaskSpec
from app.infrastructure.postgres.tasks import PostgresTaskRepository
from app.infrastructure.postgres.migrate import (
    apply_alembic_migrations,
    apply_migrations,
    ensure_application_role,
)
from test_api import ORIGIN, TEST_PASSWORD, database_ready, settings


def test_postgres_migrations_and_project_owner_scope() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")

    database_name = unquote(urlsplit(database_url).path.lstrip("/"))
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def verify() -> None:
        connection = await asyncpg.connect(dsn=dsn, timeout=5)
        app_role_name = f"agent_office_test_{uuid4().hex}"
        app_role_password = secrets.token_urlsafe(32)
        restricted_owner_id = f"test-restricted-{uuid4()}"
        api_project_id: UUID | None = None
        api_task_id: UUID | None = None
        api_definition_id: UUID | None = None
        api_instance_id: UUID | None = None
        api_endpoint_id: UUID | None = None
        api_archive_endpoint_id: UUID | None = None
        runner_ids: list[UUID] = []
        api_project_name = f"Task API restricted role {uuid4().hex}"
        restricted_pool: asyncpg.Pool | None = None
        restricted_engine = None
        restricted_sessions = None
        try:
            await apply_migrations(connection)
            await connection.execute(
                """
                INSERT INTO provider_configs (owner_id, version, adapter, endpoint_url, model_id, route)
                VALUES ($1, 2, 'openai_compatible_chat_completions',
                        'http://localhost:20128/v1', 'openrouter/migrated-model:free', 'free')
                """,
                restricted_owner_id,
            )
            legacy_definition_id = uuid4()
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO agent_definitions (id, owner_id, slug, current_version)
                    VALUES ($1, $2, 'legacy-unconfigured', 1)
                    """,
                    legacy_definition_id,
                    restricted_owner_id,
                )
                await connection.execute(
                    """
                    INSERT INTO agent_definition_versions (
                        definition_id, owner_id, version, name, description,
                        source_markdown, unmapped_metadata
                    ) VALUES ($1, $2, 1, 'Legacy unconfigured', '', '# Legacy', '{}'::jsonb)
                    """,
                    legacy_definition_id,
                    restricted_owner_id,
                )
            assert await apply_migrations(connection) == []
            await apply_alembic_migrations(dsn)
            await apply_alembic_migrations(dsn)
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == "events_0014"
            migrated_endpoint = await connection.fetchrow(
                "SELECT name, credential_env, default_model_id, default_route "
                "FROM model_endpoints WHERE owner_id = $1",
                restricted_owner_id,
            )
            assert migrated_endpoint is not None
            assert migrated_endpoint["name"] == "Migrated endpoint"
            assert migrated_endpoint["credential_env"] == "NINEROUTER_KEY"
            assert migrated_endpoint["default_model_id"] == "openrouter/migrated-model:free"
            assert migrated_endpoint["default_route"] == "free"
            assert await connection.fetchval(
                "SELECT enabled FROM agent_definitions WHERE owner_id = $1 AND id = $2",
                restricted_owner_id,
                legacy_definition_id,
            ) is False
            assert await connection.fetchval(
                "SELECT configuration IS NULL FROM agent_definition_versions "
                "WHERE owner_id = $1 AND definition_id = $2 AND version = 1",
                restricted_owner_id,
                legacy_definition_id,
            )
            await ensure_application_role(
                connection,
                app_role_name,
                app_role_password,
            )
            role = await connection.fetchrow(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls
                FROM pg_roles WHERE rolname = $1
                """,
                app_role_name,
            )
            assert role is not None
            assert role["rolsuper"] is False
            assert role["rolcreatedb"] is False
            assert role["rolcreaterole"] is False
            assert role["rolinherit"] is False
            assert role["rolbypassrls"] is False
            assert await connection.fetchval(
                "SELECT has_database_privilege($1, current_database(), 'CONNECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'INSERT')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definitions', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definitions', 'INSERT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definitions', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definitions', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_versions', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_versions', 'INSERT')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_versions', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_versions', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skills', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skills', 'INSERT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skills', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skills', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skill_versions', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skill_versions', 'INSERT')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skill_versions', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.skill_versions', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_version_skills', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_version_skills', 'INSERT')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_version_skills', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_definition_version_skills', 'DELETE')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_instances', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_instances', 'INSERT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_instances', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.agent_instances', 'DELETE')",
                app_role_name,
            )
            for table, privileges in (
                ("tasks", ("SELECT", "INSERT", "UPDATE")),
                ("task_revisions", ("SELECT", "INSERT")),
                ("task_revision_dependencies", ("SELECT", "INSERT")),
                ("runs", ("SELECT", "INSERT")),
                ("provider_configs", ("SELECT", "INSERT", "UPDATE")),
                ("model_endpoints", ("SELECT", "INSERT", "UPDATE")),
            ):
                for privilege in privileges:
                    assert await connection.fetchval(
                        "SELECT has_table_privilege($1, $2, $3)",
                        app_role_name, f"public.{table}", privilege,
                    )
                update_allowed = table in {"tasks", "provider_configs", "model_endpoints"}
                assert await connection.fetchval(
                    "SELECT has_table_privilege($1, $2, 'UPDATE')",
                    app_role_name, f"public.{table}",
                ) is update_allowed
                assert not await connection.fetchval(
                    "SELECT has_table_privilege($1, $2, 'DELETE')",
                    app_role_name, f"public.{table}",
                )
            assert not await connection.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')",
                app_role_name,
            )
            restricted_pool = await asyncpg.create_pool(
                dsn=dsn,
                user=app_role_name,
                password=app_role_password,
                min_size=1,
                max_size=2,
            )

            restricted_url = make_url(dsn).set(
                drivername="postgresql+asyncpg",
                username=app_role_name,
                password=app_role_password,
            )
            restricted_engine = create_async_engine(restricted_url)
            restricted_sessions = async_sessionmaker(
                restricted_engine, expire_on_commit=False
            )
            restricted_projects = PostgresProjectRepository(restricted_sessions)
            restricted_definitions = PostgresAgentRegistryRepository(restricted_sessions)
            restricted_endpoints = PostgresModelEndpointRepository(restricted_sessions)
            restricted_instances = PostgresAgentInstanceRepository(restricted_sessions)
            restricted_project = await restricted_projects.create(
                restricted_owner_id, "restricted project", "grant smoke"
            )
            restricted_endpoint = await restricted_endpoints.create(
                restricted_owner_id,
                ModelEndpointDraft.create(
                    "Restricted endpoint",
                    "http://localhost:20128/v1",
                    "NINEROUTER_KEY",
                    "openrouter/cohere/north-mini-code:free",
                    "free",
                ),
            )
            assert await restricted_endpoints.get_for_owner("another-owner", restricted_endpoint.id) is None
            restricted_configuration = AgentDefinitionConfiguration.create(
                role="Restricted worker",
                model_endpoint_id=restricted_endpoint.id,
                model_id="openrouter/cohere/north-mini-code:free",
                route="free",
                allowed_tools=["list_files", "read_file", "write_file"],
                concurrency_limit=1,
            )
            restricted_definition = await restricted_definitions.create(
                restricted_owner_id, "Restricted", "", "# Restricted", {}, restricted_configuration
            )
            duplicate_definition = await restricted_definitions.create(
                restricted_owner_id, "Restricted", "", "# Another Restricted", {}, restricted_configuration
            )
            assert duplicate_definition.id != restricted_definition.id
            enable_race_endpoint = await restricted_endpoints.create(
                restricted_owner_id,
                ModelEndpointDraft.create(
                    "Enable race endpoint",
                    "http://localhost:20128/v1",
                    "NINEROUTER_KEY",
                    "openrouter/cohere/north-mini-code:free",
                    "free",
                ),
            )
            enable_race_configuration = AgentDefinitionConfiguration.create(
                role="Enable race worker",
                model_endpoint_id=enable_race_endpoint.id,
                model_id=enable_race_endpoint.default_model_id,
                route=enable_race_endpoint.default_route,
            )
            enable_race_definition = await restricted_definitions.create(
                restricted_owner_id,
                "Enable race",
                "",
                "# Enable race",
                {},
                enable_race_configuration,
            )
            disabled_enable_definition = (
                await restricted_definitions.set_enabled_for_owner(
                    restricted_owner_id, enable_race_definition.id, False
                )
            )
            assert disabled_enable_definition is not None
            assert disabled_enable_definition.enabled is False
            enable_attempt: asyncio.Task[object] | None = None
            endpoint_lock_observed = False
            async with restricted_pool.acquire() as archive_connection:
                async with archive_connection.transaction():
                    await archive_connection.execute(
                        """
                        UPDATE model_endpoints SET archived_at = now()
                        WHERE owner_id = $1 AND id = $2
                        """,
                        restricted_owner_id,
                        enable_race_endpoint.id,
                    )
                    enable_attempt = asyncio.create_task(
                        restricted_definitions.set_enabled_for_owner(
                            restricted_owner_id, enable_race_definition.id, True
                        )
                    )
                    for _ in range(100):
                        wait_event = await connection.fetchval(
                            """
                            SELECT wait_event_type
                            FROM pg_stat_activity
                            WHERE usename = $1 AND datname = current_database()
                              AND state = 'active' AND query ILIKE '%model_endpoints%'
                              AND query ILIKE '%FOR SHARE%'
                              AND wait_event_type = 'Lock'
                            LIMIT 1
                            """,
                            app_role_name,
                        )
                        if wait_event == "Lock":
                            endpoint_lock_observed = True
                            break
                        await asyncio.sleep(0.01)
            assert enable_attempt is not None
            if not endpoint_lock_observed:
                await asyncio.wait_for(enable_attempt, timeout=5)
                raise AssertionError(
                    "Definition enable did not wait for Model Endpoint archive"
                )
            try:
                await asyncio.wait_for(enable_attempt, timeout=5)
            except AgentRegistryConflictError as error:
                assert "active Model Endpoint" in str(error)
            else:
                raise AssertionError(
                    "Definition was enabled against an archived Model Endpoint"
                )
            current_enable_race_definition = await restricted_definitions.get_for_owner(
                restricted_owner_id, enable_race_definition.id
            )
            assert current_enable_race_definition is not None
            assert current_enable_race_definition.enabled is False
            create_race_endpoint = await restricted_endpoints.create(
                restricted_owner_id,
                ModelEndpointDraft.create(
                    "Create race endpoint",
                    "http://localhost:20128/v1",
                    "NINEROUTER_KEY",
                    "openrouter/cohere/north-mini-code:free",
                    "free",
                ),
            )
            create_race_configuration = AgentDefinitionConfiguration.create(
                role="Create race worker",
                model_endpoint_id=create_race_endpoint.id,
                model_id=create_race_endpoint.default_model_id,
                route=create_race_endpoint.default_route,
            )
            create_attempt: asyncio.Task[object] | None = None
            create_lock_observed = False
            async with restricted_pool.acquire() as archive_connection:
                async with archive_connection.transaction():
                    await archive_connection.execute(
                        "UPDATE model_endpoints SET archived_at = now() "
                        "WHERE owner_id = $1 AND id = $2",
                        restricted_owner_id,
                        create_race_endpoint.id,
                    )
                    create_attempt = asyncio.create_task(
                        restricted_definitions.create(
                            restricted_owner_id,
                            "Create race",
                            "",
                            "# Create race",
                            {},
                            create_race_configuration,
                        )
                    )
                    for _ in range(100):
                        wait_event = await connection.fetchval(
                            """
                            SELECT wait_event_type FROM pg_stat_activity
                            WHERE usename = $1 AND datname = current_database()
                              AND state = 'active' AND query ILIKE '%model_endpoints%'
                              AND query ILIKE '%FOR SHARE%' AND wait_event_type = 'Lock'
                            LIMIT 1
                            """,
                            app_role_name,
                        )
                        if wait_event == "Lock":
                            create_lock_observed = True
                            break
                        await asyncio.sleep(0.01)
            assert create_attempt is not None
            if not create_lock_observed:
                await asyncio.wait_for(create_attempt, timeout=5)
                raise AssertionError("Definition create did not wait for Endpoint archive")
            try:
                await asyncio.wait_for(create_attempt, timeout=5)
            except AgentRegistryConflictError as error:
                assert "active Model Endpoint" in str(error)
            else:
                raise AssertionError("Definition was created with an archived Endpoint")
            assert not any(
                definition.name == "Create race"
                for definition in await restricted_definitions.list_for_owner(
                    restricted_owner_id,
                    include_archived=True,
                    limit=100,
                    offset=0,
                )
            )

            update_race_endpoint = await restricted_endpoints.create(
                restricted_owner_id,
                ModelEndpointDraft.create(
                    "Update race endpoint",
                    "http://localhost:20128/v1",
                    "NINEROUTER_KEY",
                    "openrouter/cohere/north-mini-code:free",
                    "free",
                ),
            )
            update_race_configuration = AgentDefinitionConfiguration.create(
                role="Update race worker",
                model_endpoint_id=update_race_endpoint.id,
                model_id=update_race_endpoint.default_model_id,
                route=update_race_endpoint.default_route,
            )
            update_race_definition = await restricted_definitions.create(
                restricted_owner_id,
                "Update race",
                "",
                "# Update race",
                {},
                restricted_configuration,
            )
            update_attempt: asyncio.Task[object] | None = None
            update_lock_observed = False
            async with restricted_pool.acquire() as archive_connection:
                async with archive_connection.transaction():
                    await archive_connection.execute(
                        "UPDATE model_endpoints SET archived_at = now() "
                        "WHERE owner_id = $1 AND id = $2",
                        restricted_owner_id,
                        update_race_endpoint.id,
                    )
                    update_attempt = asyncio.create_task(
                        restricted_definitions.update_for_owner(
                            restricted_owner_id,
                            update_race_definition.id,
                            {"configuration": update_race_configuration},
                        )
                    )
                    for _ in range(100):
                        wait_event = await connection.fetchval(
                            """
                            SELECT wait_event_type FROM pg_stat_activity
                            WHERE usename = $1 AND datname = current_database()
                              AND state = 'active' AND query ILIKE '%model_endpoints%'
                              AND query ILIKE '%FOR SHARE%' AND wait_event_type = 'Lock'
                            LIMIT 1
                            """,
                            app_role_name,
                        )
                        if wait_event == "Lock":
                            update_lock_observed = True
                            break
                        await asyncio.sleep(0.01)
            assert update_attempt is not None
            if not update_lock_observed:
                await asyncio.wait_for(update_attempt, timeout=5)
                raise AssertionError("Definition update did not wait for Endpoint archive")
            try:
                await asyncio.wait_for(update_attempt, timeout=5)
            except AgentRegistryConflictError as error:
                assert "active Model Endpoint" in str(error)
            else:
                raise AssertionError("Definition version used an archived Endpoint")
            current_update_race_definition = await restricted_definitions.get_for_owner(
                restricted_owner_id, update_race_definition.id
            )
            assert current_update_race_definition is not None
            assert current_update_race_definition.version == update_race_definition.version
            assert current_update_race_definition.configuration == restricted_configuration


            race_definition = await restricted_definitions.create(
                restricted_owner_id,
                "Disable race",
                "",
                "# Disable race",
                {},
                restricted_configuration,
            )
            assignment_attempt: asyncio.Task[object] | None = None
            definition_lock_observed = False
            async with restricted_pool.acquire() as disable_connection:
                async with disable_connection.transaction():
                    await disable_connection.execute(
                        """
                        UPDATE agent_definitions
                        SET enabled = false, updated_at = now()
                        WHERE owner_id = $1 AND id = $2
                        """,
                        restricted_owner_id,
                        race_definition.id,
                    )
                    assignment_attempt = asyncio.create_task(
                        restricted_instances.create(
                            restricted_owner_id,
                            "Racing disabled assignment",
                            race_definition.id,
                            None,
                        )
                    )
                    for _ in range(100):
                        wait_event = await connection.fetchval(
                            """
                            SELECT wait_event_type
                            FROM pg_stat_activity
                            WHERE usename = $1 AND datname = current_database()
                              AND state = 'active' AND query ILIKE '%FOR SHARE%'
                              AND wait_event_type = 'Lock'
                            LIMIT 1
                            """,
                            app_role_name,
                        )
                        if wait_event == "Lock":
                            definition_lock_observed = True
                            break
                        await asyncio.sleep(0.01)
            assert assignment_attempt is not None
            if not definition_lock_observed:
                await asyncio.wait_for(assignment_attempt, timeout=5)
                raise AssertionError("Instance assignment did not wait for Definition disable")
            try:
                await asyncio.wait_for(assignment_attempt, timeout=5)
            except AgentInstanceInputError:
                pass
            else:
                raise AssertionError("Instance assignment accepted a concurrently disabled Definition")
            restricted_skills = PostgresSkillRepository(restricted_sessions)
            restricted_skill = await restricted_skills.create(
                restricted_owner_id,
                SkillContent(
                    name="Snapshot skill",
                    description="Pinned capability.",
                    instructions="Use the captured skill version.",
                    dependencies=("tool:read",),
                    resources=(TextResource("notes/skill.md", "captured content"),),
                ),
            )
            linked_definition = await restricted_definitions.set_skills_for_owner(
                restricted_owner_id,
                restricted_definition.id,
                [SkillPin(restricted_skill.id, restricted_skill.version)],
            )
            assert linked_definition is not None and linked_definition.version == 2
            restricted_instance = await restricted_instances.create(
                restricted_owner_id,
                "Restricted instance",
                restricted_definition.id,
                restricted_project.id,
            )
            assert restricted_instance.project_id == restricted_project.id
            assert restricted_instance.project_name == "restricted project"
            renamed_instance = await restricted_instances.update_for_owner(
                restricted_owner_id,
                restricted_instance.id,
                {"display_name": "Restricted instance updated"},
            )
            assert renamed_instance is not None
            assert renamed_instance.display_name == "Restricted instance updated"
            task_repository = PostgresTaskRepository(restricted_sessions)
            first_spec = TaskSpec(
                project_id=restricted_project.id,
                agent_instance_id=restricted_instance.id,
                title="Persist first backlog task",
                goal="Verify task persistence through the restricted database role.",
                input_text="",
                resource_refs=("notes/acceptance.md",),
                acceptance_criteria=("Task and revision persist.",),
                blocked_by_task_ids=(),
                max_duration_minutes=30,
                max_child_runs=0,
            )
            first_task = await task_repository.create(restricted_owner_id, first_spec)
            second_spec = replace(
                first_spec,
                agent_instance_id=None,
                title="Persist dependent task",
                blocked_by_task_ids=(first_task.id,),
            )
            second_task = await task_repository.create(restricted_owner_id, second_spec)
            assert first_task.stage == "Backlog"
            assert first_task.agent_instance_name == "Restricted instance updated"
            assert second_task.blocked_by_task_ids == (first_task.id,)
            try:
                await task_repository.update_for_owner(
                    restricted_owner_id,
                    first_task.id,
                    replace(first_spec, blocked_by_task_ids=(second_task.id,)),
                )
            except TaskInputError as error:
                assert error.reason == "cycle"
            else:
                raise AssertionError("cyclic task dependency was accepted")
            unchanged = await task_repository.get_for_owner(restricted_owner_id, first_task.id)
            assert unchanged is not None and unchanged.revision == 1
            revised_task = await task_repository.update_for_owner(
                restricted_owner_id,
                first_task.id,
                replace(first_spec, title="Persist first task revision"),
            )
            assert revised_task is not None and revised_task.revision == 2
            history = await task_repository.list_revisions_for_owner(
                restricted_owner_id, first_task.id, limit=10, offset=0
            )
            assert history is not None
            assert [(revision.revision, revision.title) for revision in history] == [
                (1, "Persist first backlog task"),
                (2, "Persist first task revision"),
            ]
            run_repository = PostgresRunRepository(restricted_sessions)
            execution_repository = PostgresExecutionRepository(restricted_sessions)
            execution_policy = await execution_repository.save_policy(
                restricted_owner_id,
                restricted_project.id,
                WorkspacePolicyDraft.create(
                    workspace_path="restricted-project",
                    allowed_tools=["list_files", "read_file", "write_file"],
                    max_duration_seconds=300,
                    max_inference_turns=6,
                    max_tool_calls=15,
                ),
            )
            assert execution_policy is not None and execution_policy.revision == 1
            run_request_id = uuid4()
            first_run, created = await run_repository.create_for_task(
                restricted_owner_id,
                first_task.id,
                run_request_id,
                execution_policy_revision=execution_policy.revision,
            )
            assert created is True
            assert first_run.attempt_number == 1
            assert first_run.status == "created"
            assert first_run.configuration_snapshot["task"]["revision"] == 2
            assert first_run.configuration_snapshot["task"]["title"] == "Persist first task revision"
            assert first_run.configuration_snapshot["agent_definition"]["version"] == 2
            assert first_run.configuration_snapshot["skills"][0]["version"] == 1
            assert (
                first_run.configuration_snapshot["skills"][0]["instructions"]
                == "Use the captured skill version."
            )
            assert first_run.configuration_snapshot["agent_definition"]["configuration"] == restricted_configuration.to_json()
            assert first_run.configuration_snapshot["model_endpoint"] == {
                "id": str(restricted_endpoint.id),
                "adapter": "openai_compatible_chat_completions",
                "endpoint_url": "http://localhost:20128/v1",
                "credential_env": "NINEROUTER_KEY",
            }
            assert first_run.configuration_snapshot["runtime"] == {"id": "langchain"}
            replayed_run, created = await run_repository.create_for_task(
                restricted_owner_id,
                first_task.id,
                run_request_id,
                execution_policy_revision=execution_policy.revision,
            )
            assert created is False
            assert replayed_run.id == first_run.id
            assert replayed_run.configuration_snapshot == first_run.configuration_snapshot
            try:
                await run_repository.create_for_task(
                    restricted_owner_id, second_task.id, run_request_id
                )
            except RunInputError as error:
                assert error.reason == "idempotency_conflict"
            else:
                raise AssertionError("an idempotency key was accepted for another task")
            runner_id = uuid4()
            runner_ids.append(runner_id)
            await execution_repository.register_runner(runner_id, provider_ready=True)
            unready_runner_id = uuid4()
            runner_ids.append(unready_runner_id)
            await execution_repository.register_runner(
                unready_runner_id, provider_ready=False
            )
            ready_status = await execution_repository.get_ready_runner(max_age_seconds=30)
            assert ready_status is not None and ready_status.runner_id == runner_id
            unready_status = await execution_repository.get_runner_status(
                unready_runner_id, max_age_seconds=30
            )
            assert unready_status is not None and not unready_status.provider_ready
            workspace_instance = await restricted_instances.create(
                restricted_owner_id,
                "Second workspace instance",
                restricted_definition.id,
                restricted_project.id,
            )
            workspace_task = await task_repository.create(
                restricted_owner_id,
                replace(
                    first_spec,
                    agent_instance_id=workspace_instance.id,
                    title="Run in the same configured workspace",
                ),
            )
            second_run, second_created = await run_repository.create_for_task(
                restricted_owner_id,
                workspace_task.id,
                uuid4(),
                execution_policy_revision=execution_policy.revision,
            )
            assert second_created is True
            workspace_run_id = second_run.id
            dispatch_paths = await connection.fetch(
                "SELECT run_id, workspace_path FROM run_dispatches "
                "WHERE owner_id = $1 AND run_id = ANY($2::uuid[])",
                restricted_owner_id,
                [first_run.id, workspace_run_id],
            )
            assert len(dispatch_paths) == 2
            assert {row["workspace_path"] for row in dispatch_paths} == {
                "restricted-project"
            }
            concurrent_jobs = await asyncio.gather(
                execution_repository.claim_next(runner_id, lease_seconds=60),
                execution_repository.claim_next(runner_id, lease_seconds=60),
            )
            claimed_jobs = [job for job in concurrent_jobs if job is not None]
            assert len(claimed_jobs) == 1
            first_job = claimed_jobs[0]
            assert first_job.run_id == first_run.id
            assert first_job.policy.revision == execution_policy.revision
            assert await connection.fetchval(
                "SELECT workspace_path FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                first_run.id,
            ) == "restricted-project"
            parallel_project = await restricted_projects.create(
                restricted_owner_id, "parallel project", "Concurrency boundary"
            )
            parallel_policy = await execution_repository.save_policy(
                restricted_owner_id,
                parallel_project.id,
                WorkspacePolicyDraft.create(
                    workspace_path="parallel-project",
                    allowed_tools=["list_files", "read_file", "write_file"],
                    max_duration_seconds=300,
                    max_inference_turns=6,
                    max_tool_calls=15,
                ),
            )
            assert parallel_policy is not None
            parallel_instance = await restricted_instances.create(
                restricted_owner_id,
                "Parallel instance",
                restricted_definition.id,
                parallel_project.id,
            )
            parallel_task = await task_repository.create(
                restricted_owner_id,
                replace(
                    first_spec,
                    project_id=parallel_project.id,
                    agent_instance_id=parallel_instance.id,
                    title="Definition concurrency limit across workspaces",
                ),
            )
            parallel_run, parallel_created = await run_repository.create_for_task(
                restricted_owner_id,
                parallel_task.id,
                uuid4(),
                execution_policy_revision=parallel_policy.revision,
            )
            assert parallel_created is True
            assert await execution_repository.claim_next(runner_id, lease_seconds=60) is None
            assert await execution_repository.heartbeat_run(
                runner_id, first_run.id, lease_seconds=60
            )

            evidence_repository = PostgresActivityEventRepository(restricted_sessions)
            event_time = datetime.now(timezone.utc)
            event_draft = ActivityEventDraft(
                owner_id=restricted_owner_id,
                source_id=str(runner_id),
                event_id=uuid4(),
                schema_version=1,
                source_session_id=uuid4(),
                workspace_id=None,
                project_id=first_spec.project_id,
                task_id=first_run.task_id,
                task_revision=first_run.task_revision,
                agent_instance_id=restricted_instance.id,
                run_id=first_run.id,
                event_type="run.started",
                occurred_at=event_time,
                source_sequence=1,
            )
            started_event, inserted = await evidence_repository.append_event(event_draft)
            assert inserted is True
            outbox_claim_id = uuid4()
            claimed_events = await evidence_repository.claim_outbox_batch(
                outbox_claim_id, limit=100, lease_seconds=30
            )
            claimed_start = next(
                item for item in claimed_events if item.event_id == started_event.event_id
            )
            assert await evidence_repository.mark_outbox_published(
                restricted_owner_id, claimed_start.cursor, outbox_claim_id
            )
            assert await evidence_repository.has_event_for_runner(
                restricted_owner_id,
                str(runner_id),
                first_run.id,
                started_event.event_id,
            )
            action_draft = replace(
                event_draft,
                event_id=uuid4(),
                event_type="run.tool_action",
                occurred_at=event_time + timedelta(seconds=1),
                source_sequence=2,
                action_summary="Wrote a project file",
            )
            action_event, inserted = await evidence_repository.append_event(action_draft)
            assert inserted is True and action_event.action_summary == "Wrote a project file"
            duplicate_event, inserted = await evidence_repository.append_event(event_draft)
            assert inserted is False
            assert duplicate_event.cursor == started_event.cursor
            assert duplicate_event.event_id == started_event.event_id
            try:
                await evidence_repository.append_event(
                    replace(event_draft, event_type="run.waiting_input")
                )
            except EvidenceInputError as error:
                assert error.reason == "event_id_conflict"
            else:
                raise AssertionError("event ID reuse with different content was accepted")
            try:
                await evidence_repository.append_event(
                    replace(event_draft, event_id=uuid4(), project_id=uuid4())
                )
            except EvidenceInputError as error:
                assert error.reason == "scope"
            else:
                raise AssertionError("event with a project outside the Run snapshot was accepted")

            succeeded_draft = replace(
                event_draft,
                event_id=uuid4(),
                event_type="run.succeeded",
                occurred_at=event_time + timedelta(seconds=2),
                source_sequence=3,
            )
            await evidence_repository.append_terminal_event(
                succeeded_draft, runner_id
            )
            late_failure = replace(
                event_draft,
                event_id=uuid4(),
                event_type="run.failed",
                occurred_at=event_time + timedelta(seconds=3),
                source_sequence=4,
            )
            try:
                await evidence_repository.append_terminal_event(
                    late_failure, runner_id
                )
            except EvidenceInputError as error:
                assert error.reason == "lease"
            else:
                raise AssertionError("a second terminal transition was accepted")
            projection = await evidence_repository.get_projection_for_run(
                restricted_owner_id, first_run.id
            )
            assert projection is not None and projection.status == "succeeded"
            assert projection.last_action_summary == "Wrote a project file"
            assert projection.last_event_type == "run.succeeded"
            run_events = await evidence_repository.list_events_for_run(
                restricted_owner_id, first_run.id, limit=10
            )
            assert run_events is not None
            assert [item.event_id for item in run_events] == [
                started_event.event_id,
                action_event.event_id,
                succeeded_draft.event_id,
            ]
            owner_snapshot = await evidence_repository.get_snapshot_for_owner(
                restricted_owner_id, limit=10
            )
            assert owner_snapshot.projections[0].run_id == first_run.id
            assert await evidence_repository.get_snapshot_for_owner(
                "another-owner", limit=10
            ) == type(owner_snapshot)(cursor=0, projections=())
            second_job = await execution_repository.claim_next(runner_id, lease_seconds=1)
            assert second_job is not None and second_job.run_id == second_run.id
            await asyncio.sleep(1.1)
            assert not await execution_repository.heartbeat_run(
                runner_id, second_run.id, lease_seconds=60
            )
            parallel_job = await execution_repository.claim_next(runner_id, lease_seconds=1)
            assert parallel_job is not None and parallel_job.run_id == parallel_run.id
            await asyncio.sleep(1.1)
            assert not await execution_repository.heartbeat_run(
                runner_id, parallel_run.id, lease_seconds=60
            )
            assert await execution_repository.claim_next(runner_id, lease_seconds=60) is None
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                parallel_run.id,
            ) == "unknown"
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                second_run.id,
            ) == "unknown"

            terminal_task = await task_repository.create(
                restricted_owner_id,
                replace(first_spec, title="Persist terminal event atomically"),
            )
            terminal_run, terminal_created = await run_repository.create_for_task(
                restricted_owner_id,
                terminal_task.id,
                uuid4(),
                execution_policy_revision=execution_policy.revision,
            )
            assert terminal_created is True
            terminal_job = await execution_repository.claim_next(
                runner_id, lease_seconds=60
            )
            assert terminal_job is not None and terminal_job.run_id == terminal_run.id
            evidence_repository = PostgresActivityEventRepository(restricted_sessions)
            terminal_draft = ActivityEventDraft(
                owner_id=restricted_owner_id,
                source_id=str(runner_id),
                event_id=uuid4(),
                schema_version=1,
                source_session_id=None,
                workspace_id=None,
                project_id=restricted_project.id,
                task_id=terminal_task.id,
                task_revision=terminal_run.task_revision,
                agent_instance_id=restricted_instance.id,
                run_id=terminal_run.id,
                event_type="run.succeeded",
                occurred_at=datetime.now(timezone.utc),
                source_sequence=1,
            )
            trigger_suffix = uuid4().hex
            function_name = f"reject_terminal_event_{trigger_suffix}"
            trigger_name = f"reject_terminal_insert_{trigger_suffix}"
            await connection.execute(
                f"""
                CREATE FUNCTION {function_name}() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'forced terminal-event insert failure';
                    END;
                $$
                """
            )
            await connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE INSERT ON activity_events
                FOR EACH ROW
                WHEN (NEW.owner_id = '{restricted_owner_id}'
                  AND NEW.event_id = '{terminal_draft.event_id}'::uuid)
                EXECUTE FUNCTION {function_name}()
                """
            )
            try:
                with pytest.raises(DBAPIError):
                    await evidence_repository.append_terminal_event(
                        terminal_draft, runner_id
                    )
            finally:
                await connection.execute(
                    f"DROP TRIGGER {trigger_name} ON activity_events"
                )
                await connection.execute(f"DROP FUNCTION {function_name}()")
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                terminal_run.id,
            ) == "leased"
            assert not await evidence_repository.has_event_for_runner(
                restricted_owner_id,
                str(runner_id),
                terminal_run.id,
                terminal_draft.event_id,
            )

            terminal_event, terminal_inserted = (
                await evidence_repository.append_terminal_event(
                    terminal_draft, runner_id
                )
            )
            assert terminal_inserted is True
            assert terminal_event.event_type == "run.succeeded"
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                terminal_run.id,
            ) == "succeeded"
            replayed_terminal, terminal_inserted = (
                await evidence_repository.append_terminal_event(
                    terminal_draft, runner_id
                )
            )
            assert terminal_inserted is False
            assert replayed_terminal.cursor == terminal_event.cursor

            expired_terminal = replace(
                terminal_draft,
                event_id=uuid4(),
                task_id=workspace_task.id,
                task_revision=second_run.task_revision,
                agent_instance_id=workspace_instance.id,
                run_id=second_run.id,
                event_type="run.failed",
                occurred_at=datetime.now(timezone.utc),
            )
            try:
                await evidence_repository.append_terminal_event(
                    expired_terminal, runner_id
                )
            except EvidenceInputError as error:
                assert error.reason == "lease"
            else:
                raise AssertionError("terminal event from an expired lease was accepted")
            assert not await evidence_repository.has_event_for_runner(
                restricted_owner_id,
                str(runner_id),
                second_run.id,
                expired_terminal.event_id,
            )

            approval_repository = PostgresApprovalRepository(restricted_sessions)
            approval_digest = action_digest(
                {"action": "write", "resource": "workspace/report.txt"}
            )
            approval_draft = ApprovalRequestDraft(
                owner_id=restricted_owner_id,
                run_id=first_run.id,
                request_id=uuid4(),
                action_type="workspace.write",
                action_digest=approval_digest,
                resource_ref="workspace/report.txt",
                expires_at=event_time + timedelta(minutes=5),
            )
            approval, inserted = await approval_repository.create_request(approval_draft)
            assert inserted is True and approval.decision is None
            replayed_approval, inserted = await approval_repository.create_request(
                approval_draft
            )
            assert inserted is False and replayed_approval.id == approval.id
            decision_time = datetime.now(timezone.utc)
            try:
                await approval_repository.resolve(
                    restricted_owner_id,
                    approval.id,
                    "0" * 64,
                    "approved",
                    decision_time,
                )
            except EvidenceInputError as error:
                assert error.reason == "action_digest_mismatch"
            else:
                raise AssertionError("approval accepted a decision for a different action digest")
            rejected = await approval_repository.resolve(
                restricted_owner_id,
                approval.id,
                approval_digest,
                "rejected",
                decision_time,
            )
            assert rejected.decision == "rejected"

            artifact_repository = PostgresArtifactRepository(restricted_sessions)
            artifact = await artifact_repository.create(
                ArtifactDraft(
                    owner_id=restricted_owner_id,
                    run_id=first_run.id,
                    artifact_type="text",
                    display_name="result.txt",
                    media_type="text/plain",
                    byte_length=12,
                    sha256="a" * 64,
                    storage_key=uuid4(),
                )
            )
            listed_artifacts = await artifact_repository.list_for_run(
                restricted_owner_id, first_run.id
            )
            assert listed_artifacts == [artifact]

            scene_repository = PostgresOfficeSceneRepository(restricted_sessions)
            first_scene = await scene_repository.create_version(
                restricted_owner_id, "main-office", {"layout": "fixed"}, None
            )
            assert first_scene.version == 1
            second_scene = await scene_repository.create_version(
                restricted_owner_id,
                "main-office",
                {"layout": "fixed", "revision": 2},
                1,
            )
            assert second_scene.version == 2
            try:
                await scene_repository.create_version(
                    restricted_owner_id, "main-office", {"layout": "stale"}, 1
                )
            except EvidenceInputError as error:
                assert error.reason == "scene_version_conflict"
            else:
                raise AssertionError("stale office scene version was accepted")
            next_task = await task_repository.update_for_owner(
                restricted_owner_id,
                first_task.id,
                replace(first_spec, title="Run after task edit"),
            )
            assert next_task is not None and next_task.revision == 3
            updated_skill = await restricted_skills.update_for_owner(
                restricted_owner_id,
                restricted_skill.id,
                SkillContent(
                    name="Snapshot skill",
                    description="Updated capability.",
                    instructions="Use the new skill version.",
                    dependencies=("tool:read", "tool:search"),
                    resources=(TextResource("notes/skill.md", "new content"),),
                ),
            )
            assert updated_skill is not None and updated_skill.version == 2
            second_configuration = AgentDefinitionConfiguration.create(
                role="Restricted worker",
                model_endpoint_id=restricted_endpoint.id,
                model_id="openrouter/another-model:free",
                allowed_tools=["list_files", "read_file", "write_file"],
            )
            next_definition = await restricted_definitions.update_for_owner(
                restricted_owner_id,
                restricted_definition.id,
                {"name": "Restricted v2", "configuration": second_configuration},
            )
            assert next_definition is not None and next_definition.version == 3
            pinned_definition = await restricted_definitions.set_skills_for_owner(
                restricted_owner_id,
                restricted_definition.id,
                [SkillPin(restricted_skill.id, updated_skill.version)],
            )
            assert pinned_definition is not None and pinned_definition.version == 4
            second_run, created = await run_repository.create_for_task(
                restricted_owner_id, first_task.id, uuid4()
            )
            assert created is True
            assert second_run.attempt_number == 2
            assert second_run.configuration_snapshot["task"]["revision"] == 3
            assert second_run.configuration_snapshot["agent_definition"]["version"] == 4
            second_snapshot_configuration = second_run.configuration_snapshot["agent_definition"]["configuration"]
            assert second_snapshot_configuration["model_id"] == "openrouter/another-model:free"
            assert second_snapshot_configuration["route"] == "free"
            assert second_run.configuration_snapshot["model_endpoint"]["id"] == str(restricted_endpoint.id)
            assert second_run.configuration_snapshot["skills"][0]["version"] == 2
            assert (
                second_run.configuration_snapshot["skills"][0]["instructions"]
                == "Use the new skill version."
            )
            assert second_run.configuration_snapshot["agent_definition"]["name"] == "Restricted v2"
            assert first_run.configuration_snapshot["task"]["revision"] == 2
            assert first_run.configuration_snapshot["agent_definition"]["version"] == 2
            assert first_run.configuration_snapshot["skills"][0]["version"] == 1
            assert (
                first_run.configuration_snapshot["skills"][0]["instructions"]
                == "Use the captured skill version."
            )
            assert await run_repository.get_for_owner("another-owner", first_run.id) is None

            assignment_attempt: asyncio.Task[object] | None = None
            archive_lock_observed = False
            async with restricted_pool.acquire() as archive_connection:
                async with archive_connection.transaction():
                    await archive_connection.execute(
                        """
                        UPDATE agent_instances
                        SET archived_at = now(), updated_at = now()
                        WHERE owner_id = $1 AND id = $2
                        """,
                        restricted_owner_id,
                        restricted_instance.id,
                    )
                    assignment_attempt = asyncio.create_task(
                        task_repository.create(
                            restricted_owner_id,
                            replace(first_spec, title="Reject archived assignment"),
                        )
                    )
                    for _ in range(100):
                        wait_event = await connection.fetchval(
                            """
                            SELECT wait_event_type
                            FROM pg_stat_activity
                            WHERE usename = $1 AND datname = current_database()
                              AND state = 'active' AND query ILIKE '%FOR SHARE%'
                              AND wait_event_type = 'Lock'
                            LIMIT 1
                            """,
                            app_role_name,
                        )
                        if wait_event == "Lock":
                            archive_lock_observed = True
                            break
                        await asyncio.sleep(0.01)
            assert assignment_attempt is not None
            if not archive_lock_observed:
                await asyncio.wait_for(assignment_attempt, timeout=5)
                raise AssertionError("task assignment did not wait for concurrent archive")
            try:
                await asyncio.wait_for(assignment_attempt, timeout=5)
            except TaskInputError as error:
                assert error.reason == "reference"
            else:
                raise AssertionError("task assignment to the archived instance was accepted")
            api_endpoint = await restricted_endpoints.create(
                "owner",
                ModelEndpointDraft.create(
                    "Public API endpoint",
                    "http://localhost:20128/v1",
                    "NINEROUTER_KEY",
                    "openrouter/cohere/north-mini-code:free",
                    "free",
                ),
            )
            api_endpoint_id = api_endpoint.id
            api_configuration = AgentDefinitionConfiguration.create(
                role="API task worker",
                model_endpoint_id=api_endpoint.id,
                model_id="openrouter/cohere/north-mini-code:free",
                route="free",
                allowed_tools=["list_files", "read_file", "write_file"],
            )
            api_runner_token = "phase6-terminal-test-token-0123456789"
            api_app = create_app(
                replace(settings(), runner_shared_token=api_runner_token),
                database_check=database_ready,
                project_repository=restricted_projects,
                agent_registry_repository=restricted_definitions,
                model_endpoint_repository=restricted_endpoints,
                agent_instance_repository=restricted_instances,
                task_repository=task_repository,
                run_repository=run_repository,
                execution_repository=PostgresExecutionRepository(restricted_sessions),
                activity_event_repository=PostgresActivityEventRepository(restricted_sessions),
            )
            async with api_app.router.lifespan_context(api_app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=api_app),
                    base_url=ORIGIN,
                ) as client:
                    login_response = await client.post(
                        "/api/v1/auth/login",
                        json={"username": "owner", "password": TEST_PASSWORD},
                        headers={"origin": ORIGIN},
                    )
                    assert login_response.status_code == 200
                    api_archive_endpoint = await restricted_endpoints.create(
                        "owner",
                        ModelEndpointDraft.create(
                            "API archive regression",
                            "http://localhost:20128/v1",
                            "NINEROUTER_KEY",
                            "openrouter/cohere/north-mini-code:free",
                            "free",
                        ),
                    )
                    api_archive_endpoint_id = api_archive_endpoint.id
                    archive_response = await client.post(
                        f"/api/v1/model-endpoints/{api_archive_endpoint.id}/archive",
                        json={},
                        headers={"origin": ORIGIN},
                    )
                    assert archive_response.status_code == 200
                    assert archive_response.json()["archived"] is True
                    project_response = await client.post(
                        "/api/v1/projects",
                        json={"name": api_project_name, "summary": "Integration"},
                        headers={"origin": ORIGIN},
                    )
                    assert project_response.status_code == 201
                    api_project_id = UUID(project_response.json()["id"])
                    execution_repository = PostgresExecutionRepository(restricted_sessions)
                    api_policy = await execution_repository.save_policy(
                        "owner",
                        api_project_id,
                        WorkspacePolicyDraft.create(
                            workspace_path=f"api-{api_project_id.hex[:8]}",
                            allowed_tools=["list_files", "read_file", "write_file"],
                            max_duration_seconds=300,
                            max_inference_turns=6,
                            max_tool_calls=15,
                        ),
                    )
                    assert api_policy is not None
                    api_runner_id = uuid4()
                    runner_ids.append(api_runner_id)
                    await execution_repository.register_runner(api_runner_id, provider_ready=True)
                    api_definition = await restricted_definitions.create(
                        "owner", f"Public API Agent {uuid4().hex}", "", "# API Agent", {}, api_configuration
                    )
                    api_definition_id = api_definition.id
                    api_instance = await restricted_instances.create(
                        "owner", "Public API Instance", api_definition.id, api_project_id
                    )
                    api_instance_id = api_instance.id
                    payload = {
                        "project_id": str(api_project_id),
                        "agent_instance_id": str(api_instance.id),
                        "title": "Public API task",
                        "goal": "Persist through the authenticated public API.",
                        "input_text": "",
                        "resource_refs": [],
                        "acceptance_criteria": ["The task is returned from PostgreSQL."],
                        "blocked_by_task_ids": [],
                        "max_duration_minutes": 15,
                        "max_child_runs": 0,
                    }
                    create_response = await client.post(
                        "/api/v1/tasks", json=payload, headers={"origin": ORIGIN}
                    )
                    assert create_response.status_code == 201
                    api_task = create_response.json()
                    assert api_task["stage"] == "Backlog"
                    assert api_task["revision"] == 1
                    assert api_task["project_name"] == api_project_name
                    assert api_task["agent_instance_id"] == str(api_instance.id)
                    assert "owner_id" not in api_task
                    api_task_id = UUID(api_task["id"])
                    assert [
                        item["id"]
                        for item in (
                            await client.get(
                                "/api/v1/tasks", params={"project_id": str(api_project_id)}
                            )
                        ).json()
                    ] == [str(api_task_id)]
                    update_response = await client.put(
                        f"/api/v1/tasks/{api_task_id}",
                        json={**payload, "title": "Public API task revision"},
                        headers={"origin": ORIGIN},
                    )
                    assert update_response.status_code == 200
                    assert update_response.json()["revision"] == 2
                    history_response = await client.get(
                        f"/api/v1/tasks/{api_task_id}/revisions"
                    )
                    assert [item["title"] for item in history_response.json()] == [
                        "Public API task",
                        "Public API task revision",
                    ]
                    run_request_id = uuid4()
                    run_response = await client.post(
                        f"/api/v1/tasks/{api_task_id}/runs",
                        json={"request_id": str(run_request_id)},
                        headers={"origin": ORIGIN},
                    )
                    assert run_response.status_code == 201
                    api_run = run_response.json()
                    assert api_run["status"] == "created"
                    runner_headers = {
                        "authorization": f"Bearer {api_runner_token}"
                    }
                    claim_response = await client.post(
                        "/internal/runner/claim",
                        json={"runner_id": str(api_runner_id)},
                        headers=runner_headers,
                    )
                    assert claim_response.status_code == 200
                    assert claim_response.json()["run_id"] == api_run["id"]
                    terminal_event_payload = {
                        "runner_id": str(api_runner_id),
                        "run_id": api_run["id"],
                        "event_id": str(uuid4()),
                        "event_type": "run.succeeded",
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "source_sequence": 1,
                    }
                    terminal_response = await client.post(
                        "/internal/runner/events",
                        json=terminal_event_payload,
                        headers=runner_headers,
                    )
                    assert terminal_response.status_code == 200
                    assert terminal_response.json()["acknowledged"] is True
                    terminal_replay = await client.post(
                        "/internal/runner/events",
                        json=terminal_event_payload,
                        headers=runner_headers,
                    )
                    assert terminal_replay.status_code == 200
                    assert terminal_replay.json()["replayed"] is True
                    assert await connection.fetchval(
                        "SELECT state FROM run_dispatches WHERE owner_id = 'owner' AND run_id = $1",
                        UUID(api_run["id"]),
                    ) == "succeeded"
                    assert await connection.fetchval(
                        "SELECT status FROM run_activity_projections "
                        "WHERE owner_id = 'owner' AND run_id = $1",
                        UUID(api_run["id"]),
                    ) == "succeeded"
                    assert api_run["attempt_number"] == 1
                    assert api_run["configuration_snapshot"]["task"]["revision"] == 2
                    assert api_run["configuration_snapshot"]["model_endpoint"]["endpoint_url"] == "http://localhost:20128/v1"
                    assert "owner_id" not in api_run
                    replay_response = await client.post(
                        f"/api/v1/tasks/{api_task_id}/runs",
                        json={"request_id": str(run_request_id)},
                        headers={"origin": ORIGIN},
                    )
                    assert replay_response.status_code == 200
                    assert replay_response.json()["id"] == api_run["id"]
                    assert (
                        await client.get(f"/api/v1/runs/{api_run['id']}")
                    ).json() == api_run
                    assert [
                        item["id"]
                        for item in (
                            await client.get(f"/api/v1/tasks/{api_task_id}/runs")
                        ).json()
                    ] == [api_run["id"]]
                    assert (
                        await client.get(f"/api/v1/runs/{first_run.id}")
                    ).status_code == 404
            queued_legacy_instance = await restricted_instances.create(
                restricted_owner_id,
                "Legacy queue instance",
                duplicate_definition.id,
                restricted_project.id,
            )
            queued_legacy_task = await task_repository.create(
                restricted_owner_id,
                replace(
                    first_spec,
                    agent_instance_id=queued_legacy_instance.id,
                    title="Legacy queued Registry snapshot",
                ),
            )
            queued_legacy_run, queued_legacy_created = await run_repository.create_for_task(
                restricted_owner_id,
                queued_legacy_task.id,
                uuid4(),
                execution_policy_revision=execution_policy.revision,
            )
            assert queued_legacy_created is True
            malformed_legacy_task = await task_repository.create(
                restricted_owner_id,
                replace(
                    first_spec,
                    agent_instance_id=queued_legacy_instance.id,
                    title="Malformed queued Registry snapshot",
                ),
            )
            malformed_legacy_run, malformed_legacy_created = (
                await run_repository.create_for_task(
                    restricted_owner_id,
                    malformed_legacy_task.id,
                    uuid4(),
                    execution_policy_revision=execution_policy.revision,
                )
            )
            assert malformed_legacy_created is True
            valid_registry_task = await task_repository.create(
                restricted_owner_id,
                replace(
                    first_spec,
                    agent_instance_id=queued_legacy_instance.id,
                    title="Valid queued Registry snapshot",
                ),
            )
            valid_registry_run, valid_registry_created = (
                await run_repository.create_for_task(
                    restricted_owner_id,
                    valid_registry_task.id,
                    uuid4(),
                    execution_policy_revision=execution_policy.revision,
                )
            )
            assert valid_registry_created is True
            archived_instance = await restricted_instances.archive_for_owner(
                restricted_owner_id, restricted_instance.id
            )
            assert archived_instance is not None
            assert archived_instance.archived_at is not None
            if restricted_pool is not None:
                await restricted_pool.close()
                restricted_pool = None
            if restricted_engine is not None:
                await restricted_engine.dispose()

            await connection.execute(
                """
                UPDATE runs
                SET configuration_snapshot =
                    configuration_snapshot #- '{agent_definition,configuration}'
                                             #- '{model_endpoint}'
                WHERE owner_id = $1 AND id = $2
                """,
                restricted_owner_id,
                queued_legacy_run.id,
            )
            await connection.execute(
                """
                UPDATE runs
                SET configuration_snapshot = jsonb_set(
                    jsonb_set(
                        configuration_snapshot,
                        '{agent_definition,configuration}',
                        '{}'::jsonb
                    ),
                    '{model_endpoint}',
                    '{}'::jsonb
                )
                WHERE owner_id = $1 AND id = $2
                """,
                restricted_owner_id,
                malformed_legacy_run.id,
            )
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                queued_legacy_run.id,
            ) == "queued"

            first_lease_expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
            second_lease_expiry = datetime.now(timezone.utc) + timedelta(minutes=1)
            async with connection.transaction():
                await connection.execute("DROP INDEX run_dispatches_active_workspace_idx")
                await connection.execute("ALTER TABLE run_dispatches DROP COLUMN workspace_path")
                await connection.execute("DROP TABLE activity_event_outbox")
                await connection.execute("DROP TABLE run_results")
                await connection.execute(
                    "ALTER TABLE run_dispatches "
                    "DROP CONSTRAINT run_dispatches_max_duration_check, "
                    "DROP COLUMN cancellation_requested_at, "
                    "DROP COLUMN max_duration_seconds"
                )
                await connection.execute(
                    "CREATE UNIQUE INDEX run_dispatches_active_instance_idx "
                    "ON run_dispatches (owner_id, instance_id) WHERE state = 'leased'"
                )
                for run_id, expiry in (
                    (first_run.id, first_lease_expiry),
                    (workspace_run_id, second_lease_expiry),
                ):
                    await connection.execute(
                        """
                        UPDATE run_dispatches
                        SET state = 'leased', runner_id = $3,
                            lease_expires_at = $4, updated_at = now()
                        WHERE owner_id = $1 AND run_id = $2
                        """,
                        restricted_owner_id,
                        run_id,
                        runner_id,
                        expiry,
                    )
                await connection.execute(
                    "UPDATE alembic_version SET version_num = 'execution_0009'"
                )
            await apply_alembic_migrations(dsn)
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                first_run.id,
            ) == "leased"
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                workspace_run_id,
            ) == "unknown"
            assert await connection.fetchval(
                """
                SELECT event_type FROM activity_events
                WHERE owner_id = $1 AND run_id = $2
                  AND source_id = 'migration-execution-0010'
                """,
                restricted_owner_id,
                workspace_run_id,
            ) == "run.blocked"
            assert await connection.fetchval(
                "SELECT status FROM run_activity_projections WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                workspace_run_id,
            ) == "blocked"
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                queued_legacy_run.id,
            ) == "blocked"
            assert await connection.fetchval(
                """
                SELECT event_type FROM activity_events
                WHERE owner_id = $1 AND run_id = $2
                  AND source_id = 'migration-agent-registry-0012'
                """,
                restricted_owner_id,
                queued_legacy_run.id,
            ) == "run.blocked"
            assert await connection.fetchval(
                "SELECT status FROM run_activity_projections WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                queued_legacy_run.id,
            ) == "blocked"
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                valid_registry_run.id,
            ) == "queued"
            assert not await connection.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM activity_events
                    WHERE owner_id = $1 AND run_id = $2
                      AND source_id = 'migration-agent-registry-0012'
                )
                """,
                restricted_owner_id,
                valid_registry_run.id,
            )
            assert await connection.fetchval(
                "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                malformed_legacy_run.id,
            ) == "blocked"
            assert await connection.fetchval(
                """
                SELECT event_type FROM activity_events
                WHERE owner_id = $1 AND run_id = $2
                  AND source_id = 'migration-agent-registry-0012'
                """,
                restricted_owner_id,
                malformed_legacy_run.id,
            ) == "run.blocked"
            assert await connection.fetchval(
                "SELECT status FROM run_activity_projections WHERE owner_id = $1 AND run_id = $2",
                restricted_owner_id,
                malformed_legacy_run.id,
            ) == "blocked"
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == "events_0014"
        finally:
            if restricted_pool is not None:
                await restricted_pool.close()
            if restricted_engine is not None:
                await restricted_engine.dispose()
                async with connection.transaction():
                    if api_project_id is not None:
                        if api_task_id is not None:
                            await connection.execute(
                                "DELETE FROM run_dispatches WHERE owner_id = 'owner' AND run_id IN "
                                "(SELECT id FROM runs WHERE owner_id = 'owner' AND task_id = $1)",
                                api_task_id,
                            )
                            await connection.execute(
                                "DELETE FROM activity_event_outbox "
                                "WHERE owner_id = 'owner' AND cursor IN "
                                "(SELECT cursor FROM activity_events "
                                "WHERE owner_id = 'owner' AND run_id IN "
                                "(SELECT id FROM runs WHERE owner_id = 'owner' AND task_id = $1))",
                                api_task_id,
                            )
                            for table in (
                                "run_activity_projections",
                                "activity_events",
                            ):
                                await connection.execute(
                                    f"DELETE FROM {table} WHERE owner_id = 'owner' "
                                    "AND run_id IN (SELECT id FROM runs "
                                    "WHERE owner_id = 'owner' AND task_id = $1)",
                                    api_task_id,
                                )
                            await connection.execute(
                                "DELETE FROM runs WHERE owner_id = 'owner' AND task_id = $1",
                                api_task_id,
                            )
                            await connection.execute(
                                "DELETE FROM task_revision_dependencies WHERE owner_id = 'owner' AND task_id = $1",
                                api_task_id,
                            )
                            await connection.execute(
                                "DELETE FROM task_revisions WHERE owner_id = 'owner' AND task_id = $1",
                                api_task_id,
                            )
                            await connection.execute(
                                "DELETE FROM tasks WHERE owner_id = 'owner' AND id = $1",
                                api_task_id,
                            )
                        if api_instance_id is not None:
                            await connection.execute(
                                "DELETE FROM agent_instances WHERE owner_id = 'owner' AND id = $1",
                                api_instance_id,
                            )
                        if api_definition_id is not None:
                            await connection.execute(
                                "DELETE FROM agent_definition_versions WHERE owner_id = 'owner' AND definition_id = $1",
                                api_definition_id,
                            )
                            await connection.execute(
                                "DELETE FROM agent_definitions WHERE owner_id = 'owner' AND id = $1",
                                api_definition_id,
                            )
                        if api_endpoint_id is not None:
                            await connection.execute(
                                "DELETE FROM model_endpoints WHERE owner_id = 'owner' AND id = $1",
                                api_endpoint_id,
                            )
                        if api_archive_endpoint_id is not None:
                            await connection.execute(
                                "DELETE FROM model_endpoints WHERE owner_id = 'owner' AND id = $1",
                                api_archive_endpoint_id,
                            )
                        await connection.execute(
                            "DELETE FROM projects WHERE owner_id = 'owner' AND id = $1",
                            api_project_id,
                        )
                    await connection.execute(
                        "DELETE FROM owner_event_cursors WHERE owner_id = 'owner' "
                        "AND NOT EXISTS (SELECT 1 FROM activity_events "
                        "WHERE owner_id = 'owner')"
                    )
                    for table in (
                        "approval_decisions",
                        "approval_requests",
                        "artifacts",
                        "run_activity_projections",
                        "activity_event_outbox",
                        "activity_events",
                        "owner_event_cursors",
                    ):
                        await connection.execute(
                            f"DELETE FROM {table} WHERE owner_id = $1",
                            restricted_owner_id,
                        )
                    await connection.execute(
                        "DELETE FROM run_dispatches WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    if runner_ids:
                        await connection.execute(
                            "DELETE FROM runner_connections WHERE runner_id = ANY($1::uuid[])",
                            runner_ids,
                        )
                    await connection.execute(
                        "DELETE FROM runs WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM provider_configs WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM model_endpoints WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "SET CONSTRAINTS agent_definitions_current_version_fk, skills_current_version_fk DEFERRED"
                    )
                    await connection.execute(
                        "DELETE FROM task_revision_dependencies WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM task_revisions WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM tasks WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM agent_instances WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM agent_definition_version_skills WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM skill_versions WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM skills WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM agent_definition_versions WHERE definition_id IN "
                        "(SELECT id FROM agent_definitions WHERE owner_id = $1)",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM agent_definitions WHERE owner_id = $1",
                        restricted_owner_id,
                    )
                    await connection.execute(
                        "DELETE FROM projects WHERE owner_id = $1",
                        restricted_owner_id,
                    )
            role_exists = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
                app_role_name,
            )
            if role_exists:
                drop_owned = await connection.fetchval(
                    "SELECT format('DROP OWNED BY %I', $1::text)",
                    app_role_name,
                )
                await connection.execute(drop_owned)
                drop_role = await connection.fetchval(
                    "SELECT format('DROP ROLE %I', $1::text)",
                    app_role_name,
                )
                await connection.execute(drop_role)
            await connection.close()
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        engine = create_async_engine(
            dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = f"test-owner-{uuid4()}"
        foreign_owner_id = f"test-owner-{uuid4()}"
        project_repository = PostgresProjectRepository(sessions)
        definition_repository = PostgresAgentRegistryRepository(sessions)
        repository = PostgresAgentInstanceRepository(sessions)
        try:
            own = await project_repository.create(owner_id, "owned project", "scope test")
            foreign = await project_repository.create(
                foreign_owner_id,
                "foreign project",
                "must remain private",
            )
            assert [project.id for project in await project_repository.list_for_owner(
                owner_id, limit=10, offset=0
            )] == [own.id]
            assert await project_repository.get_for_owner(owner_id, own.id) == own
            assert await project_repository.get_for_owner(owner_id, foreign.id) is None

            endpoint_repository = PostgresModelEndpointRepository(sessions)
            own_endpoint = await endpoint_repository.create(
                owner_id,
                ModelEndpointDraft.create(
                    "Research endpoint",
                    "https://provider.example/v1",
                    "NINEROUTER_KEY",
                    "openrouter/model:free",
                ),
            )
            archivable_endpoint = await endpoint_repository.create(
                owner_id,
                ModelEndpointDraft.create(
                    "Endpoint archive regression",
                    "https://provider.example/v1",
                    "NINEROUTER_KEY",
                    "openrouter/model:free",
                ),
            )
            archived_endpoint = await endpoint_repository.archive_for_owner(
                owner_id, archivable_endpoint.id
            )
            assert archived_endpoint is not None
            assert archived_endpoint.archived_at is not None
            active_endpoint_ids = {
                endpoint.id
                for endpoint in await endpoint_repository.list_for_owner(
                    owner_id, include_archived=False, limit=10, offset=0
                )
            }
            all_endpoint_ids = {
                endpoint.id
                for endpoint in await endpoint_repository.list_for_owner(
                    owner_id, include_archived=True, limit=10, offset=0
                )
            }
            assert archivable_endpoint.id not in active_endpoint_ids
            assert archivable_endpoint.id in all_endpoint_ids
            foreign_endpoint = await endpoint_repository.create(
                foreign_owner_id,
                ModelEndpointDraft.create(
                    "Private endpoint",
                    "https://provider.example/v1",
                    "NINEROUTER_KEY",
                    "openrouter/model:free",
                ),
            )
            own_configuration = AgentDefinitionConfiguration.create(
                role="Researcher",
                model_endpoint_id=own_endpoint.id,
                model_id=own_endpoint.default_model_id,
                allowed_tools=["read_file"],
            )
            foreign_configuration = AgentDefinitionConfiguration.create(
                role="Private role",
                model_endpoint_id=foreign_endpoint.id,
                model_id=foreign_endpoint.default_model_id,
                allowed_tools=["read_file"],
            )
            own_definition = await definition_repository.create(
                owner_id, "Researcher", "", "# Researcher", {}, own_configuration
            )
            foreign_definition = await definition_repository.create(
                foreign_owner_id,
                "Private role",
                "",
                "# Private",
                {},
                foreign_configuration,
            )
            instance = await repository.create(
                owner_id, "Mira", own_definition.id, own.id
            )
            assert instance.definition_id == own_definition.id
            assert instance.definition_name == "Researcher"
            assert instance.project_id == own.id
            assert instance.project_name == "owned project"
            assert await repository.get_for_owner(foreign_owner_id, instance.id) is None
            assert await repository.list_for_owner(
                owner_id, include_archived=False, limit=10, offset=0
            ) == [instance]

            await definition_repository.update_for_owner(
                owner_id, own_definition.id, {"name": "Researcher v2"}
            )
            linked = await repository.get_for_owner(owner_id, instance.id)
            assert linked is not None
            assert linked.definition_id == own_definition.id
            assert linked.definition_name == "Researcher v2"

            unassigned = await repository.update_for_owner(
                owner_id, instance.id, {"display_name": "Mira 2", "project_id": None}
            )
            assert unassigned is not None
            assert unassigned.display_name == "Mira 2"
            assert unassigned.project_id is None
            assert unassigned.project_name is None

            with pytest.raises(AgentInstanceInputError):
                await repository.create(
                    owner_id, "Foreign project", own_definition.id, foreign.id
                )
            with pytest.raises(AgentInstanceInputError):
                await repository.create(
                    owner_id, "Foreign definition", foreign_definition.id, own.id
                )

            archived = await repository.archive_for_owner(owner_id, instance.id)
            assert archived is not None and archived.archived_at is not None
            assert await repository.list_for_owner(
                owner_id, include_archived=False, limit=10, offset=0
            ) == []
            assert await repository.list_for_owner(
                owner_id, include_archived=True, limit=10, offset=0
            ) == [archived]
            assert await repository.get_for_owner(owner_id, foreign.id) is None
            archived_definition = await definition_repository.archive_for_owner(
                owner_id, own_definition.id
            )
            assert archived_definition is not None
            assert archived_definition.archived_at is not None
            assert await definition_repository.list_for_owner(
                owner_id, include_archived=False, limit=10, offset=0
            ) == []
            archived_definitions = await definition_repository.list_for_owner(
                owner_id, include_archived=True, limit=10, offset=0
            )
            assert [definition.id for definition in archived_definitions] == [
                own_definition.id
            ]
            historical_definition = await definition_repository.get_for_owner(
                owner_id, own_definition.id, version=1
            )
            assert historical_definition is not None
            assert historical_definition.archived_at is not None
        finally:
            async with pool.acquire() as cleanup_connection:
                async with cleanup_connection.transaction():
                    await cleanup_connection.execute(
                        "SET CONSTRAINTS agent_definitions_current_version_fk DEFERRED"
                    )
                    await cleanup_connection.execute(
                        "DELETE FROM agent_instances WHERE owner_id = ANY($1::text[])",
                        [owner_id, foreign_owner_id],
                    )
                    await cleanup_connection.execute(
                        "DELETE FROM agent_definition_versions WHERE definition_id IN "
                        "(SELECT id FROM agent_definitions WHERE owner_id = ANY($1::text[]))",
                        [owner_id, foreign_owner_id],
                    )
                    await cleanup_connection.execute(
                        "DELETE FROM agent_definitions WHERE owner_id = ANY($1::text[])",
                        [owner_id, foreign_owner_id],
                    )
                    await cleanup_connection.execute(
                        "DELETE FROM model_endpoints WHERE owner_id = ANY($1::text[])",
                        [owner_id, foreign_owner_id],
                    )
                    await cleanup_connection.execute(
                        "DELETE FROM projects WHERE owner_id = ANY($1::text[])",
                        [owner_id, foreign_owner_id],
                    )
            await pool.close()
            await engine.dispose()

    asyncio.run(verify())
