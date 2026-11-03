from __future__ import annotations
from dataclasses import replace

from collections.abc import Mapping
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.model_endpoints import ModelEndpointInputError, validate_provider_binding
from app.domain.agent_configuration import AgentDefinitionConfiguration
from app.domain.runs import Run, RunInputError, RunRepository, WorkflowDelegationBinding
from app.domain.orchestration import MAX_OUTPUT_BYTES, WorkflowLeaseBinding
from app.infrastructure.postgres.models import RunTable, TaskTable
from app.infrastructure.postgres.raw_sql import RawSQLExecutor, require_workflow_lease

_RUN_SELECT = """
SELECT id, owner_id, task_id, task_revision, attempt_number, request_id,
       status, configuration_snapshot, created_at
FROM runs
"""


def _json_value(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value

def _snapshot_model_scope(snapshot: Mapping[str, Any]) -> dict[str, object] | None:
    endpoint = snapshot.get("model_endpoint")
    if not isinstance(endpoint, dict):
        return None
    scope = {
        "model_endpoint_id": endpoint.get("id"),
        "model_id": endpoint.get("model_id"),
        "route": endpoint.get("route"),
        "adapter": endpoint.get("adapter"),
        "endpoint_url": endpoint.get("endpoint_url"),
        "credential_env": endpoint.get("credential_env"),
    }
    return scope if all(value is not None or key == "route" for key, value in scope.items()) else None


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

    async def _check_workflow_delegation(
        self,
        connection: RawSQLExecutor,
        owner_id: str,
        task_id: UUID,
        binding: WorkflowDelegationBinding | None,
        *,
        existing_run_id: UUID | None,
    ) -> None:
        if binding is None:
            delegated = await connection.fetchval(
                "SELECT 1 FROM orchestration_delegations WHERE owner_id=$1 AND child_task_id=$2 LIMIT 1",
                owner_id, task_id,
            )
            if delegated is not None:
                raise RunInputError("workflow_approval")
            return
        row = await connection.fetchrow(
            """
            SELECT d.status, d.child_run_id, d.canonical_digest,
                   EXISTS (
                       SELECT 1
                       FROM approval_requests AS request
                       JOIN approval_decisions AS decision
                         ON decision.owner_id = request.owner_id
                        AND decision.approval_id = request.id
                       WHERE request.owner_id = d.owner_id
                         AND request.id = d.approval_id
                         AND request.run_id = d.parent_run_id
                         AND request.action_type = 'workflow.delegation'
                         AND request.action_digest = $5
                         AND request.resource_ref = $6
                         AND request.expires_at > now()
                         AND decision.decision = 'approved'
                   ) AS approval_valid
            FROM orchestration_delegations AS d
            WHERE d.owner_id=$1 AND d.parent_run_id=$2 AND d.node_id=$3 AND d.child_task_id=$4
            FOR UPDATE
            """,
            owner_id, binding.parent_run_id, binding.node_id, task_id,
            binding.canonical_digest,
            f"workflow:{binding.parent_run_id}:{binding.node_id}",
        )
        if row is None or row["canonical_digest"] != binding.canonical_digest:
            raise RunInputError("workflow_approval")
        if existing_run_id is None:
            if (
                row["status"] != "approved"
                or row["child_run_id"] is not None
                or row["approval_valid"] is not True
            ):
                raise RunInputError("workflow_approval")
            return
        if row["child_run_id"] not in (None, existing_run_id) or row["status"] not in {
            "approved", "queued", "running", "succeeded", "failed", "invalid", "blocked", "unknown",
        }:
            raise RunInputError("workflow_approval")
        if row["child_run_id"] is None:
            await connection.execute(
                """
                UPDATE orchestration_delegations
                SET child_run_id=$4,status='queued',updated_at=now()
                WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$3
                """,
                owner_id, binding.parent_run_id, binding.node_id, existing_run_id,
            )

    async def _attach_workflow_run(
        self,
        connection: RawSQLExecutor,
        owner_id: str,
        binding: WorkflowDelegationBinding,
        task_id: UUID,
        run_id: UUID,
    ) -> None:
        attached = await connection.fetchval(
            """
            UPDATE orchestration_delegations AS delegation
            SET child_run_id=$4,status='queued',updated_at=now()
            WHERE delegation.owner_id=$1 AND delegation.parent_run_id=$2 AND delegation.node_id=$3
              AND delegation.child_task_id=$5 AND delegation.canonical_digest=$6
              AND delegation.status='approved' AND delegation.child_run_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM approval_requests AS request
                  JOIN approval_decisions AS decision
                    ON decision.owner_id=request.owner_id
                   AND decision.approval_id=request.id
                  WHERE request.owner_id=delegation.owner_id
                    AND request.id=delegation.approval_id
                    AND request.run_id=delegation.parent_run_id
                    AND request.action_type='workflow.delegation'
                    AND request.action_digest=$6
                    AND request.resource_ref=$7
                    AND request.expires_at>clock_timestamp()
                    AND decision.decision='approved'
              )
            RETURNING 1
            """,
            owner_id, binding.parent_run_id, binding.node_id, run_id, task_id,
            binding.canonical_digest,
            f"workflow:{binding.parent_run_id}:{binding.node_id}",
        )
        if attached != 1:
            raise RunInputError("workflow_approval")

    async def create_for_task(
        self,
        owner_id: str,
        task_id: UUID,
        request_id: UUID,
        *,
        execution_policy_revision: int | None = None,
        expected_instance_id: UUID | None = None,
        expected_definition_id: UUID | None = None,
        expected_definition_version: int | None = None,
        expected_task_revision: int | None = None,
        expected_model_scope: Mapping[str, object] | None = None,
        dependency_outputs: Mapping[str, object] | None = None,
        workflow_delegation: WorkflowDelegationBinding | None = None,
        workflow_lease: WorkflowLeaseBinding | None = None,
    ) -> tuple[Run, bool]:
        dependency_snapshot = None
        if dependency_outputs is not None:
            if any(not isinstance(key, str) for key in dependency_outputs):
                raise RunInputError("dependency_output")
            dependency_snapshot = dict(dependency_outputs)
            try:
                dependency_size = len(json.dumps(
                    dependency_snapshot, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8"))
            except (TypeError, ValueError, RecursionError) as error:
                raise RunInputError("dependency_output") from error
            if dependency_size > MAX_OUTPUT_BYTES:
                raise RunInputError("dependency_output")
        if (workflow_delegation is None) != (workflow_lease is None):
            raise RunInputError("workflow_lease")
        if (
            workflow_delegation is not None
            and workflow_lease is not None
            and workflow_delegation.parent_run_id != workflow_lease.parent_run_id
        ):
            raise RunInputError("workflow_lease")
        async with self._sessions() as session:
            async with session.begin():
                connection = RawSQLExecutor(session)
                if workflow_lease is not None:
                    await require_workflow_lease(
                        connection, workflow_lease, owner_id=owner_id,
                        parent_run_id=workflow_lease.parent_run_id,
                    )
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"run-request:{owner_id}:{request_id}",
                )
                existing = await connection.fetchrow(
                    _RUN_SELECT + " WHERE owner_id = $1 AND request_id = $2",
                    owner_id,
                    request_id,
                )
                await self._check_workflow_delegation(
                    connection, owner_id, task_id, workflow_delegation,
                    existing_run_id=None if existing is None else existing["id"],
                )
                if existing is not None:
                    if existing["task_id"] != task_id:
                        raise RunInputError("idempotency_conflict")
                    if expected_task_revision is not None and existing["task_revision"] != expected_task_revision:
                        raise RunInputError("task_revision")
                    existing_run = _run(existing)
                    if expected_model_scope is not None and _snapshot_model_scope(existing_run.configuration_snapshot) != dict(expected_model_scope):
                        raise RunInputError("workflow_model_scope")
                    if dependency_snapshot is not None:
                        task_snapshot = existing_run.configuration_snapshot.get("task")
                        if not isinstance(task_snapshot, dict) or task_snapshot.get("dependency_outputs", {}) != dependency_snapshot:
                            raise RunInputError("workflow_dependency_output")
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
                           revision.output_schema, revision.tool_scope,
                           revision.max_duration_minutes, revision.max_child_runs,
                           instance.id AS instance_id, instance.display_name AS instance_name,
                           definition.id AS definition_id,
                           definition.current_version AS definition_version,
                           version.name AS definition_name,
                           version.description AS definition_description,
                           version.source_markdown AS definition_source,
                           version.unmapped_metadata AS definition_metadata,
                           version.configuration AS definition_configuration
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
                     AND definition.enabled IS TRUE
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
                if expected_task_revision is not None and context["current_revision"] != expected_task_revision:
                    raise RunInputError("task_revision")
                if (
                    expected_instance_id is not None and context["instance_id"] != expected_instance_id
                    or expected_definition_id is not None and context["definition_id"] != expected_definition_id
                    or expected_definition_version is not None and context["definition_version"] != expected_definition_version
                ):
                    raise RunInputError("workflow_assignment")

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

                definition_configuration = AgentDefinitionConfiguration.from_json(
                    _json_value(context["definition_configuration"])
                )
                if definition_configuration is None:
                    raise RunInputError("configuration")
                model_endpoint = await connection.fetchrow(
                    """
                    SELECT id, adapter, endpoint_url, credential_env, default_route
                    FROM model_endpoints
                    WHERE owner_id = $1 AND id = $2 AND archived_at IS NULL
                    FOR SHARE
                    """,
                    owner_id,
                    definition_configuration.model_endpoint_id,
                )
                if model_endpoint is None:
                    raise RunInputError("model_endpoint")
                if definition_configuration.route is None and model_endpoint["default_route"] is not None:
                    definition_configuration = replace(
                        definition_configuration, route=model_endpoint["default_route"]
                    )
                try:
                    validate_provider_binding(
                        model_endpoint["credential_env"], model_endpoint["endpoint_url"],
                        definition_configuration.model_id, definition_configuration.route,
                    )
                except ModelEndpointInputError as error:
                    raise RunInputError("model_endpoint") from error
                if expected_model_scope is not None:
                    actual_model_scope = {
                        "model_endpoint_id": str(model_endpoint["id"]),
                        "model_id": definition_configuration.model_id,
                        "route": definition_configuration.route,
                        "adapter": model_endpoint["adapter"],
                        "endpoint_url": model_endpoint["endpoint_url"],
                        "credential_env": model_endpoint["credential_env"],
                    }
                    if actual_model_scope != dict(expected_model_scope):
                        raise RunInputError("workflow_model_scope")
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
                        "output_schema": _json_value(context["output_schema"]),
                        "tool_scope": None if context["tool_scope"] is None else list(context["tool_scope"]),
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
                        "configuration": definition_configuration.to_json(),
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
                    "model_endpoint": {
                        "id": str(model_endpoint["id"]),
                        "adapter": model_endpoint["adapter"],
                        "endpoint_url": model_endpoint["endpoint_url"],
                        "credential_env": model_endpoint["credential_env"],
                        "model_id": definition_configuration.model_id,
                        "route": definition_configuration.route,
                    },
                    "runtime": {"id": definition_configuration.runtime_id},
                }
                if workflow_delegation is not None:
                    snapshot["task"]["workflow_output"] = {
                        "node_id": workflow_delegation.node_id,
                        "output_schema": _json_value(context["output_schema"]),
                    }
                if dependency_snapshot is not None:
                    snapshot["task"]["dependency_outputs"] = dependency_snapshot
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
                if workflow_delegation is not None:
                    await self._attach_workflow_run(
                        connection, owner_id, workflow_delegation, task_id, run.id,
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
    async def get_for_request(self, owner_id: str, request_id: UUID) -> Run | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(RunTable).where(
                    RunTable.owner_id == owner_id,
                    RunTable.request_id == request_id,
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
