from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import secrets
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
import httpx
from app.main import create_app
from test_api import ORIGIN, TEST_PASSWORD, database_ready, settings
from app.domain import SkillPin
from app.instances import AgentInstanceInputError, PostgresAgentInstanceRepository
from app.migrate import apply_migrations, ensure_application_role
from app.projects import PostgresProjectRepository
from app.registry import PostgresAgentRegistryRepository
from app.runs import PostgresRunRepository, RunInputError
from app.skills import PostgresSkillRepository, SkillContent, TextResource
from app.tasks import PostgresTaskRepository, TaskInputError, TaskSpec


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
        api_project_name = f"Task API restricted role {uuid4().hex}"
        restricted_pool: asyncpg.Pool | None = None
        try:
            await apply_migrations(connection)
            assert await apply_migrations(connection) == []
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
            ):
                for privilege in privileges:
                    assert await connection.fetchval(
                        "SELECT has_table_privilege($1, $2, $3)",
                        app_role_name, f"public.{table}", privilege,
                    )
                update_allowed = table == "tasks"
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
            restricted_projects = PostgresProjectRepository(restricted_pool)
            restricted_definitions = PostgresAgentRegistryRepository(restricted_pool)
            restricted_instances = PostgresAgentInstanceRepository(restricted_pool)
            restricted_project = await restricted_projects.create(
                restricted_owner_id, "restricted project", "grant smoke"
            )
            restricted_definition = await restricted_definitions.create(
                restricted_owner_id, "Restricted", "", "# Restricted", {}
            )
            restricted_skills = PostgresSkillRepository(restricted_pool)
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
            task_repository = PostgresTaskRepository(restricted_pool)
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
            run_repository = PostgresRunRepository(restricted_pool)
            run_request_id = uuid4()
            first_run, created = await run_repository.create_for_task(
                restricted_owner_id, first_task.id, run_request_id
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
            assert first_run.configuration_snapshot["provider"] is None
            assert first_run.configuration_snapshot["runtime"] is None
            replayed_run, created = await run_repository.create_for_task(
                restricted_owner_id, first_task.id, run_request_id
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
            next_definition = await restricted_definitions.update_for_owner(
                restricted_owner_id,
                restricted_definition.id,
                {"name": "Restricted v2"},
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
            api_app = create_app(
                settings(),
                database_check=database_ready,
                project_repository=restricted_projects,
                agent_registry_repository=restricted_definitions,
                agent_instance_repository=restricted_instances,
                task_repository=task_repository,
                run_repository=run_repository,
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
                    project_response = await client.post(
                        "/api/v1/projects",
                        json={"name": api_project_name, "summary": "Integration"},
                        headers={"origin": ORIGIN},
                    )
                    assert project_response.status_code == 201
                    api_project_id = UUID(project_response.json()["id"])
                    api_definition = await restricted_definitions.create(
                        "owner", f"Public API Agent {uuid4().hex}", "", "# API Agent", {}
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
                    assert api_run["attempt_number"] == 1
                    assert api_run["configuration_snapshot"]["task"]["revision"] == 2
                    assert api_run["configuration_snapshot"]["agent_definition"]["version"] == 1
                    assert api_run["configuration_snapshot"]["provider"] is None
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
            archived_instance = await restricted_instances.archive_for_owner(
                restricted_owner_id, restricted_instance.id
            )
            assert archived_instance is not None
            assert archived_instance.archived_at is not None
        finally:
            if restricted_pool is not None:
                await restricted_pool.close()
                async with connection.transaction():
                    if api_project_id is not None:
                        if api_task_id is not None:
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
                        await connection.execute(
                            "DELETE FROM projects WHERE owner_id = 'owner' AND id = $1",
                            api_project_id,
                        )
                    await connection.execute(
                        "DELETE FROM runs WHERE owner_id = $1",
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
        owner_id = f"test-owner-{uuid4()}"
        foreign_owner_id = f"test-owner-{uuid4()}"
        project_repository = PostgresProjectRepository(pool)
        definition_repository = PostgresAgentRegistryRepository(pool)
        repository = PostgresAgentInstanceRepository(pool)
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

            own_definition = await definition_repository.create(
                owner_id, "Researcher", "", "# Researcher", {}
            )
            foreign_definition = await definition_repository.create(
                foreign_owner_id, "Private role", "", "# Private", {}
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
                        "DELETE FROM projects WHERE owner_id = ANY($1::text[])",
                        [owner_id, foreign_owner_id],
                    )
            await pool.close()

    asyncio.run(verify())
