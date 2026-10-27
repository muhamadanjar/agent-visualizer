from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.runs import Run, RunInputError, RunRepository
from app.infrastructure.postgres.models import RunTable, TaskTable
from app.infrastructure.postgres.raw_sql import RawSQLExecutor

_RUN_SELECT = """
SELECT id, owner_id, task_id, task_revision, attempt_number, request_id,
       status, configuration_snapshot, created_at
FROM runs
"""


def _json_value(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _run(row: Mapping[str, Any]) -> Run:
    return Run(
        id=row["id"],
        owner_id=row["owner_id"],
        task_id=row["task_id"],
        task_revision=row["task_revision"],
        attempt_number=row["attempt_number"],
        request_id=row["request_id"],
        status=row["status"],
        configuration_snapshot=_json_value(row["configuration_snapshot"]),
        created_at=row["created_at"],
    )

def _run_model(row: RunTable) -> Run:
    return Run(
        id=row.id,
        owner_id=row.owner_id,
        task_id=row.task_id,
        task_revision=row.task_revision,
        attempt_number=row.attempt_number,
        request_id=row.request_id,
        status=row.status,
        configuration_snapshot=row.configuration_snapshot,
        created_at=row.created_at,
    )


class PostgresRunRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _ensure_dispatch(
        self,
        connection: RawSQLExecutor,
        owner_id: str,
        run_id: UUID,
        task_id: UUID,
        project_id: UUID,
        instance_id: UUID,
        policy_revision: int,
    ) -> None:
        existing = await connection.fetchrow(
            "SELECT state FROM run_dispatches WHERE owner_id = $1 AND run_id = $2",
            owner_id,
            run_id,
        )
        if existing is not None:
            return
        policy = await connection.fetchrow(
            """
            SELECT enabled, revision, workspace_path
            FROM project_execution_policies
            WHERE owner_id = $1 AND project_id = $2
            FOR SHARE
            """,
            owner_id,
            project_id,
        )
        if (
            policy is None
            or not policy["enabled"]
            or policy["revision"] != policy_revision
        ):
            raise RunInputError("execution_policy")
        await connection.execute(
            """
            INSERT INTO run_dispatches (
                owner_id, run_id, task_id, project_id, instance_id, policy_revision,
                workspace_path, state
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'queued')
            """,
            owner_id,
            run_id,
            task_id,
            project_id,
            instance_id,
            policy_revision,
            policy["workspace_path"],
        )
    async def create_for_task(
        self,
        owner_id: str,
        task_id: UUID,
        request_id: UUID,
        *,
        execution_policy_revision: int | None = None,
    ) -> tuple[Run, bool]:
        async with self._sessions() as session:
            async with session.begin():
                connection = RawSQLExecutor(session)
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"run-request:{owner_id}:{request_id}",
                )
                existing = await connection.fetchrow(
                    _RUN_SELECT + " WHERE owner_id = $1 AND request_id = $2",
                    owner_id,
                    request_id,
                )
                if existing is not None:
                    if existing["task_id"] != task_id:
                        raise RunInputError("idempotency_conflict")
                    existing_run = _run(existing)
                    if execution_policy_revision is not None:
                        snapshot = existing_run.configuration_snapshot
                        task_snapshot = snapshot["task"]
                        instance_snapshot = snapshot["agent_instance"]
                        await self._ensure_dispatch(
                            connection,
                            owner_id,
                            existing_run.id,
                            task_id,
                            UUID(str(task_snapshot["project_id"])),
                            UUID(str(instance_snapshot["id"])),
                            execution_policy_revision,
                        )
                    return existing_run, False

                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"run-task:{owner_id}:{task_id}",
                )
                context = await connection.fetchrow(
                    """
                    SELECT task.current_revision, revision.project_id, project.name AS project_name,
                           revision.title, revision.goal, revision.input_text,
                           revision.resource_refs, revision.acceptance_criteria,
                           revision.max_duration_minutes, revision.max_child_runs,
                           instance.id AS instance_id, instance.display_name AS instance_name,
                           definition.id AS definition_id,
                           definition.current_version AS definition_version,
                           version.name AS definition_name,
                           version.description AS definition_description,
                           version.source_markdown AS definition_source,
                           version.unmapped_metadata AS definition_metadata
                    FROM tasks AS task
                    JOIN task_revisions AS revision
                      ON revision.owner_id = task.owner_id
                     AND revision.task_id = task.id
                     AND revision.revision = task.current_revision
                    JOIN projects AS project
                      ON project.owner_id = revision.owner_id
                     AND project.id = revision.project_id
                    JOIN agent_instances AS instance
                      ON instance.owner_id = revision.owner_id
                     AND instance.id = revision.agent_instance_id
                     AND instance.archived_at IS NULL
                    JOIN agent_definitions AS definition
                      ON definition.owner_id = instance.owner_id
                     AND definition.id = instance.definition_id
                     AND definition.archived_at IS NULL
                    JOIN agent_definition_versions AS version
                      ON version.owner_id = definition.owner_id
                     AND version.definition_id = definition.id
                     AND version.version = definition.current_version
                    WHERE task.owner_id = $1 AND task.id = $2
                    FOR SHARE OF task, instance, definition
                    """,
                    owner_id,
                    task_id,
                )
                if context is None:
                    task_exists = await connection.fetchval(
                        "SELECT 1 FROM tasks WHERE owner_id = $1 AND id = $2",
                        owner_id,
                        task_id,
                    )
                    if task_exists is None:
                        raise RunInputError("task")
                    raise RunInputError("assignment")

                dependencies = await connection.fetch(
                    """
                    SELECT blocked_by_task_id
                    FROM task_revision_dependencies
                    WHERE owner_id = $1 AND task_id = $2 AND revision = $3
                    ORDER BY blocked_by_task_id
                    """,
                    owner_id,
                    task_id,
                    context["current_revision"],
                )
                pin_count = await connection.fetchval(
                    """
                    SELECT count(*)
                    FROM agent_definition_version_skills
                    WHERE owner_id = $1 AND definition_id = $2 AND definition_version = $3
                    """,
                    owner_id,
                    context["definition_id"],
                    context["definition_version"],
                )
                skill_rows = await connection.fetch(
                    """
                    SELECT pin.skill_id, pin.skill_version, skill.name,
                           skill.description, skill.instructions,
                           skill.dependencies, skill.resources
                    FROM agent_definition_version_skills AS pin
                    JOIN skills AS stable_skill
                      ON stable_skill.owner_id = pin.owner_id
                     AND stable_skill.id = pin.skill_id
                     AND stable_skill.archived_at IS NULL
                    JOIN skill_versions AS skill
                      ON skill.owner_id = pin.owner_id
                     AND skill.skill_id = pin.skill_id
                     AND skill.version = pin.skill_version
                    WHERE pin.owner_id = $1 AND pin.definition_id = $2
                      AND pin.definition_version = $3
                    ORDER BY pin.position
                    """,
                    owner_id,
                    context["definition_id"],
                    context["definition_version"],
                )
                if len(skill_rows) != pin_count:
                    raise RunInputError("skill")

                provider_config = await connection.fetchrow(
                    """
                    SELECT version, adapter, endpoint_url, model_id, route
                    FROM provider_configs
                    WHERE owner_id = $1
                    """,
                    owner_id,
                )
                snapshot = {
                    "task": {
                        "id": str(task_id),
                        "revision": context["current_revision"],
                        "project_id": str(context["project_id"]),
                        "project_name": context["project_name"],
                        "title": context["title"],
                        "goal": context["goal"],
                        "input_text": context["input_text"],
                        "resource_refs": list(context["resource_refs"]),
                        "acceptance_criteria": list(context["acceptance_criteria"]),
                        "blocked_by_task_ids": [str(row["blocked_by_task_id"]) for row in dependencies],
                        "max_duration_minutes": context["max_duration_minutes"],
                        "max_child_runs": context["max_child_runs"],
                    },
                    "agent_instance": {
                        "id": str(context["instance_id"]),
                        "display_name": context["instance_name"],
                    },
                    "agent_definition": {
                        "id": str(context["definition_id"]),
                        "version": context["definition_version"],
                        "name": context["definition_name"],
                        "description": context["definition_description"],
                        "source_markdown": context["definition_source"],
                        "unmapped_metadata": _json_value(context["definition_metadata"]),
                    },
                    "skills": [
                        {
                            "id": str(row["skill_id"]),
                            "version": row["skill_version"],
                            "name": row["name"],
                            "description": row["description"],
                            "instructions": row["instructions"],
                            "dependencies": _json_value(row["dependencies"]),
                            "resources": _json_value(row["resources"]),
                        }
                        for row in skill_rows
                    ],
                    "provider": (
                        None
                        if provider_config is None
                        else {
                            "version": provider_config["version"],
                            "adapter": provider_config["adapter"],
                            "endpoint_url": provider_config["endpoint_url"],
                            "model_id": provider_config["model_id"],
                            "route": provider_config["route"],
                        }
                    ),
                    "runtime": None,
                }
                attempt_number = await connection.fetchval(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM runs "
                    "WHERE owner_id = $1 AND task_id = $2",
                    owner_id,
                    task_id,
                )
                run = RunTable(
                    id=uuid4(),
                    owner_id=owner_id,
                    task_id=task_id,
                    task_revision=context["current_revision"],
                    attempt_number=attempt_number,
                    request_id=request_id,
                    definition_id=context["definition_id"],
                    definition_version=context["definition_version"],
                    configuration_snapshot=snapshot,
                )
                session.add(run)
                await session.flush()
                if execution_policy_revision is not None:
                    await self._ensure_dispatch(
                        connection,
                        owner_id,
                        run.id,
                        task_id,
                        context["project_id"],
                        context["instance_id"],
                        execution_policy_revision,
                    )
                return _run_model(run), True

    async def get_for_owner(self, owner_id: str, run_id: UUID) -> Run | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(RunTable).where(
                    RunTable.owner_id == owner_id,
                    RunTable.id == run_id,
                )
            )
            return _run_model(row) if row is not None else None

    async def list_for_task(
        self, owner_id: str, task_id: UUID, *, limit: int, offset: int
    ) -> list[Run] | None:
        async with self._sessions() as session:
            if await session.scalar(
                select(TaskTable.id).where(
                    TaskTable.owner_id == owner_id,
                    TaskTable.id == task_id,
                )
            ) is None:
                return None
            rows = await session.scalars(
                select(RunTable)
                .where(RunTable.owner_id == owner_id, RunTable.task_id == task_id)
                .order_by(RunTable.attempt_number)
                .limit(limit)
                .offset(offset)
            )
            return [_run_model(row) for row in rows]
