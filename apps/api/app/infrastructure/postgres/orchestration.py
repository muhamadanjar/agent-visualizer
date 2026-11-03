"""Transactional PostgreSQL adapter for workflow orchestration."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.orchestration import DelegationSpec, OrchestrationInputError, OrchestrationRepository, WorkflowLeaseBinding, WorkflowLimits, WorkflowPlan, digest, parent_runtime_checkpoint, validate_output
from app.domain.tasks import TaskSpec
from app.infrastructure.postgres.models import TaskRevisionDependencyTable, TaskRevisionTable, TaskTable
from app.infrastructure.postgres.raw_sql import RawSQLConnectionPool, RawSQLExecutor, require_workflow_lease


_ACTIVE_WORKFLOW_FILTER = """
    delegation.status NOT IN ('succeeded', 'failed', 'rejected', 'expired', 'invalid', 'blocked', 'unknown')
    OR (
        checkpoint.parent_run_id IS NOT NULL
        AND (
            COALESCE(checkpoint.state->>'status', '') NOT IN ('succeeded', 'failed', 'canceled')
            OR (
                jsonb_typeof(checkpoint.state) = 'object'
                AND checkpoint.state ? 'pending_status_event'
            )
        )
    )
"""

def _artifact_ids(value: Any) -> list[UUID]:
    found: list[UUID] = []
    if isinstance(value, dict):
        artifact_id = value.get("artifact_id")
        if artifact_id is not None:
            found.append(UUID(str(artifact_id)))
        for child in value.values():
            found.extend(_artifact_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_artifact_ids(child))
    return found


class PostgresOrchestrationRepository(OrchestrationRepository):
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._pool = RawSQLConnectionPool(sessions)

    @asynccontextmanager
    async def workflow_start_lock(self, owner_id: str) -> AsyncIterator[None]:
        lock_key = f"orchestration:start:{owner_id}"
        async with self._sessions() as session:
            raw = RawSQLExecutor(session)
            try:
                await raw.fetchval(
                    "SELECT pg_advisory_lock(hashtextextended($1, 0))", lock_key
                )
            except BaseException:
                await session.invalidate()
                raise
            try:
                yield
            finally:
                async def release_lock() -> None:
                    released = await raw.fetchval(
                        "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                        lock_key,
                    )
                    if released is not True:
                        raise RuntimeError("workflow start lock was not held")
                    await session.commit()

                release_task = asyncio.create_task(release_lock())
                try:
                    await asyncio.shield(release_task)
                except asyncio.CancelledError:
                    try:
                        await release_task
                    except BaseException:
                        await session.invalidate()
                        raise
                    raise
                except BaseException:
                    await session.invalidate()
                    raise

    async def persist_plan_with_tasks(self, owner_id: str, plan: WorkflowPlan, task_specs: Mapping[UUID, TaskSpec]) -> None:
        async with self._sessions.begin() as session:
            raw = RawSQLExecutor(session)
            await raw.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"orchestration:active:{owner_id}")
            active = await raw.fetchval(
                f"""
                SELECT 1
                FROM orchestration_delegations AS delegation
                LEFT JOIN orchestration_checkpoints AS checkpoint
                  ON checkpoint.owner_id = delegation.owner_id
                 AND checkpoint.parent_run_id = delegation.parent_run_id
                WHERE delegation.owner_id=$1
                  AND delegation.parent_run_id<>$2
                  AND ({_ACTIVE_WORKFLOW_FILTER})
                LIMIT 1
                """,
                owner_id, plan.parent_run_id,
            )
            if active:
                raise OrchestrationInputError("workflow_active")
            await raw.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"orchestration:{owner_id}:{plan.parent_run_id}")
            for node in plan.nodes:
                spec = task_specs[node.child_task_id]
                session.add(TaskTable(id=node.child_task_id, owner_id=owner_id, current_revision=1))
                await session.flush()
                session.add(TaskRevisionTable(
                    task_id=node.child_task_id, owner_id=owner_id, revision=1,
                    project_id=spec.project_id, agent_instance_id=spec.agent_instance_id,
                    title=spec.title, goal=spec.goal, input_text=spec.input_text,
                    resource_refs=list(spec.resource_refs), acceptance_criteria=list(spec.acceptance_criteria),
                    max_duration_minutes=spec.max_duration_minutes, max_child_runs=spec.max_child_runs,
                    output_schema=spec.output_schema,
                    tool_scope=None if spec.tool_scope is None else list(spec.tool_scope),
                ))
                await session.flush()
                session.add_all(TaskRevisionDependencyTable(owner_id=owner_id, task_id=node.child_task_id, revision=1, blocked_by_task_id=dep) for dep in spec.blocked_by_task_ids)
            await session.flush()
            for node in plan.nodes:
                await raw.execute("""
                    INSERT INTO orchestration_delegations(owner_id,id,parent_run_id,node_id,revision,child_task_id,child_revision,definition_id,definition_version,instance_id,project_id,workspace_path,input_payload,resource_refs,dependencies,output_schema,model_scope,tool_scope,limits,canonical_digest,plan_digest,status)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,'waiting_approval')
                """, owner_id, uuid4(), plan.parent_run_id, node.node_id, plan.revision, node.child_task_id, node.child_revision, node.definition_id, node.definition_version, node.instance_id, node.project_id, node.workspace_path, node.input_payload, list(node.resource_refs), list(node.dependencies), node.output_schema, node.model_scope, list(node.tool_scope), node.limits, node.canonical_digest(), plan.plan_digest)
            await raw.execute(
                "INSERT INTO orchestration_checkpoints(owner_id,parent_run_id,version,state) VALUES ($1,$2,1,$3)",
                owner_id, plan.parent_run_id,
                parent_runtime_checkpoint({
                    "plan_digest": plan.plan_digest,
                    "plan_revision": plan.revision,
                    "active_children": 0,
                    "spent_child_runs": 0,
                    "status": "waiting_approval",
                    "status_sequence": -1,
                }),
            )

    async def save_plan(self, owner_id: str, plan: WorkflowPlan) -> None:
        raise RuntimeError("workflow plans must be persisted with child task revisions")

    async def get_plan(self, owner_id: str, parent_run_id: UUID) -> WorkflowPlan | None:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch("SELECT * FROM orchestration_delegations WHERE owner_id=$1 AND parent_run_id=$2 ORDER BY node_id", owner_id, parent_run_id)
        if not rows:
            return None
        first = rows[0]
        nodes = tuple(DelegationSpec(node_id=row["node_id"], child_task_id=row["child_task_id"], child_revision=row["child_revision"], definition_id=row["definition_id"], definition_version=row["definition_version"], instance_id=row["instance_id"], project_id=row["project_id"], workspace_path=row["workspace_path"], input_payload=dict(row["input_payload"]), resource_refs=tuple(row["resource_refs"]), dependencies=tuple(row["dependencies"]), output_schema=row["output_schema"], model_scope=dict(row["model_scope"]), tool_scope=tuple(row["tool_scope"]), limits=dict(row["limits"])) for row in rows)
        limits = dict(first["limits"])
        plan = WorkflowPlan(parent_run_id=parent_run_id, plan_digest=first["plan_digest"], revision=first["revision"], nodes=nodes, limits=WorkflowLimits(max_child_runs=limits.get("max_child_runs"), max_duration_minutes=limits.get("max_duration_minutes")), created_at=first["created_at"])
        if plan.plan_digest != digest({"nodes": sorted(node.canonical_digest() for node in plan.nodes)}):
            raise RuntimeError("workflow plan digest mismatch")
        return plan

    async def active_parent_runs(self) -> list[tuple[str, UUID]]:
        rows = await self._pool.fetch(f"""
            SELECT DISTINCT delegation.owner_id, delegation.parent_run_id
            FROM orchestration_delegations AS delegation
            LEFT JOIN orchestration_checkpoints AS checkpoint
              ON checkpoint.owner_id = delegation.owner_id
             AND checkpoint.parent_run_id = delegation.parent_run_id
            WHERE {_ACTIVE_WORKFLOW_FILTER}
        """)
        return [(str(row["owner_id"]), row["parent_run_id"]) for row in rows]

    async def bind_approval(
        self, owner_id: str, parent_run_id: UUID, node_id: str, approval_id: UUID,
        *, workflow_lease: WorkflowLeaseBinding | None = None,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                if workflow_lease is not None:
                    await require_workflow_lease(
                        connection, workflow_lease,
                        owner_id=owner_id, parent_run_id=parent_run_id,
                    )
                await connection.execute(
                    "UPDATE orchestration_delegations SET approval_id=$4,updated_at=now() WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$3",
                    owner_id, parent_run_id, node_id, approval_id,
                )

    async def node_statuses(self, owner_id: str, parent_run_id: UUID) -> dict[str, str]:
        rows = await self._pool.fetch("SELECT node_id,status FROM orchestration_delegations WHERE owner_id=$1 AND parent_run_id=$2", owner_id, parent_run_id)
        return {str(row["node_id"]): str(row["status"]) for row in rows}
    async def delegation_states(self, owner_id: str, parent_run_id: UUID) -> list[Mapping[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT node_id,status,approval_id,child_run_id,output FROM orchestration_delegations WHERE owner_id=$1 AND parent_run_id=$2 ORDER BY node_id",
            owner_id, parent_run_id,
        )
        return [dict(row) for row in rows]


    async def reconcile_child_runs(
        self, owner_id: str, parent_run_id: UUID, *,
        workflow_lease: WorkflowLeaseBinding,
    ) -> tuple[dict[str, str], int, int]:
        statuses: dict[str, str] = {}
        active = spent = 0
        terminal = {"succeeded", "failed", "rejected", "expired", "invalid", "blocked", "unknown"}
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await require_workflow_lease(
                    connection, workflow_lease, owner_id=owner_id,
                    parent_run_id=parent_run_id,
                )
                rows = await connection.fetch("""
                    SELECT d.node_id, d.status, d.child_run_id, d.output, d.output_schema,
                           dispatch.state AS dispatch_state, result.result_text
                    FROM orchestration_delegations AS d
                    LEFT JOIN run_dispatches AS dispatch
                      ON dispatch.owner_id=d.owner_id AND dispatch.run_id=d.child_run_id
                    LEFT JOIN run_results AS result
                      ON result.owner_id=d.owner_id AND result.run_id=d.child_run_id
                    WHERE d.owner_id=$1 AND d.parent_run_id=$2 ORDER BY d.node_id
                """, owner_id, parent_run_id)
                for row in rows:
                    node_id = str(row["node_id"])
                    status = str(row["status"])
                    if row["child_run_id"] is None:
                        statuses[node_id] = status
                        continue
                    spent += 1
                    dispatch_state = row["dispatch_state"]
                    if dispatch_state in {"queued", "leased"}:
                        active += 1
                    if status in terminal:
                        statuses[node_id] = status
                        continue
                    if dispatch_state in {"queued", "leased"}:
                        status = "queued"
                    elif dispatch_state in {"unknown", None}:
                        status = "unknown"
                    elif dispatch_state == "succeeded":
                        try:
                            output = row["output"]
                            if output is None:
                                if not isinstance(row["result_text"], str):
                                    raise ValueError("missing result")
                                output = json.loads(row["result_text"])
                            valid = validate_output(output, row["output_schema"])
                            for artifact_id in _artifact_ids(valid):
                                exists = await connection.fetchval(
                                    "SELECT 1 FROM artifacts WHERE owner_id=$1 AND run_id=$2 AND id=$3",
                                    owner_id, row["child_run_id"], artifact_id,
                                )
                                if exists is None:
                                    raise ValueError("artifact is outside child Run")
                            await connection.execute(
                                "UPDATE orchestration_delegations SET output=$3,status='succeeded',updated_at=now() WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$4",
                                owner_id, parent_run_id, valid, node_id,
                            )
                            status = "succeeded"
                        except (ValueError, TypeError, OrchestrationInputError, json.JSONDecodeError, RecursionError):
                            status = "invalid"
                    elif dispatch_state == "blocked":
                        status = "blocked"
                    elif dispatch_state in {"failed", "canceled"}:
                        status = "failed"
                    else:
                        status = "unknown"
                    statuses[node_id] = status
                    await connection.execute(
                        "UPDATE orchestration_delegations SET status=$4,updated_at=now() WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$3 AND status<>$4",
                        owner_id, parent_run_id, node_id, status,
                    )
        return statuses, active, spent

    async def expire_delegations(
        self, owner_id: str, parent_run_id: UUID, *,
        workflow_lease: WorkflowLeaseBinding,
    ) -> int:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await require_workflow_lease(
                    connection, workflow_lease, owner_id=owner_id,
                    parent_run_id=parent_run_id,
                )
                rows = await connection.fetch("""
                    UPDATE orchestration_delegations AS d SET status='expired',updated_at=now()
                    FROM approval_decisions AS decision
                    WHERE d.owner_id=$1 AND d.parent_run_id=$2 AND d.approval_id=decision.approval_id
                      AND decision.owner_id=d.owner_id AND decision.decision='expired' AND d.status='waiting_approval'
                    RETURNING 1
                """, owner_id, parent_run_id)
                return len(rows)

    async def set_node_status(
        self, owner_id: str, parent_run_id: UUID, node_id: str, status: str, *,
        expected_status: str,
        workflow_lease: WorkflowLeaseBinding,
    ) -> bool:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await require_workflow_lease(
                    connection, workflow_lease, owner_id=owner_id,
                    parent_run_id=parent_run_id,
                )
                value = await connection.fetchval(
                    "UPDATE orchestration_delegations SET status=$5,updated_at=now() WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$3 AND status=$4 RETURNING 1",
                    owner_id, parent_run_id, node_id, expected_status, status,
                )
                return value == 1


    async def save_checkpoint(
        self, owner_id: str, parent_run_id: UUID, state: Mapping[str, Any], *,
        terminal: bool = False, workflow_lease: WorkflowLeaseBinding,
    ) -> None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await require_workflow_lease(
                    connection, workflow_lease, owner_id=owner_id,
                    parent_run_id=parent_run_id,
                )
                if terminal:
                    await connection.execute(
                        "DELETE FROM orchestration_checkpoints WHERE owner_id=$1 AND parent_run_id=$2",
                        owner_id, parent_run_id,
                    )
                    return
                await connection.execute("""
                    INSERT INTO orchestration_checkpoints(owner_id,parent_run_id,version,state)
                    VALUES ($1,$2,COALESCE((SELECT version+1 FROM orchestration_checkpoints WHERE owner_id=$1 AND parent_run_id=$2),1),$3)
                    ON CONFLICT (owner_id,parent_run_id) DO UPDATE SET version=orchestration_checkpoints.version+1,state=EXCLUDED.state,updated_at=now()
                """, owner_id, parent_run_id, parent_runtime_checkpoint(state))

    async def get_checkpoint(self, owner_id: str, parent_run_id: UUID) -> Any | None:
        row = await self._pool.fetchrow("SELECT state FROM orchestration_checkpoints WHERE owner_id=$1 AND parent_run_id=$2", owner_id, parent_run_id)
        return None if row is None else row["state"]

    async def acquire_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, *, seconds: int) -> int | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow("""
                    INSERT INTO orchestration_leases(owner_id,parent_run_id,holder_id,fencing_token,expires_at) VALUES ($1,$2,$3,1,now()+($4 * interval '1 second'))
                    ON CONFLICT (owner_id) DO UPDATE SET parent_run_id=EXCLUDED.parent_run_id,holder_id=EXCLUDED.holder_id,fencing_token=orchestration_leases.fencing_token+1,expires_at=EXCLUDED.expires_at,updated_at=now()
                    WHERE (orchestration_leases.expires_at <= now() OR orchestration_leases.holder_id=$3) AND (orchestration_leases.parent_run_id=$2 OR orchestration_leases.expires_at <= now()) RETURNING fencing_token
                """, owner_id, parent_run_id, holder_id, seconds)
                return None if row is None else int(row["fencing_token"])

    async def renew_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, fencing_token: int, *, seconds: int) -> bool:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                value = await connection.fetchval(
                    "UPDATE orchestration_leases SET expires_at=now()+($5 * interval '1 second'),updated_at=now() WHERE owner_id=$1 AND parent_run_id=$2 AND holder_id=$3 AND fencing_token=$4 AND expires_at>now() RETURNING 1",
                    owner_id, parent_run_id, holder_id, fencing_token, seconds,
                )
                return value == 1

    async def release_lease(self, owner_id: str, parent_run_id: UUID, holder_id: UUID, fencing_token: int) -> bool:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                value = await connection.fetchval(
                    "DELETE FROM orchestration_leases WHERE owner_id=$1 AND parent_run_id=$2 AND holder_id=$3 AND fencing_token=$4 RETURNING 1",
                    owner_id, parent_run_id, holder_id, fencing_token,
                )
                return value == 1
