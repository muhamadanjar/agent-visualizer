from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.domain.execution import (
    RunExecutionJob,
    RunnerRunContext,
    RunnerStatus,
    WorkspacePolicy,
    WorkspacePolicyDraft,
)
from app.infrastructure.postgres.models import (
    ProjectExecutionPolicyTable,
    RunDispatchTable,
    RunTable,
    RunnerConnectionTable,
)


def _workspace_policy(row: ProjectExecutionPolicyTable) -> WorkspacePolicy:
    return WorkspacePolicy(
        owner_id=row.owner_id,
        project_id=row.project_id,
        workspace_path=row.workspace_path,
        allowed_tools=tuple(row.allowed_tools),
        max_duration_seconds=row.max_duration_seconds,
        max_inference_turns=row.max_inference_turns,
        max_tool_calls=row.max_tool_calls,
        enabled=row.enabled,
        revision=row.revision,
        updated_at=row.updated_at,
    )


class PostgresExecutionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_policy_for_project(
        self, owner_id: str, project_id: UUID
    ) -> WorkspacePolicy | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProjectExecutionPolicyTable).where(
                    ProjectExecutionPolicyTable.owner_id == owner_id,
                    ProjectExecutionPolicyTable.project_id == project_id,
                )
            )
            return None if row is None else _workspace_policy(row)

    async def save_policy(
        self, owner_id: str, project_id: UUID, draft: WorkspacePolicyDraft
    ) -> WorkspacePolicy | None:
        statement = (
            insert(ProjectExecutionPolicyTable)
            .values(
                owner_id=owner_id,
                project_id=project_id,
                workspace_path=draft.workspace_path,
                allowed_tools=list(draft.allowed_tools),
                max_duration_seconds=draft.max_duration_seconds,
                max_inference_turns=draft.max_inference_turns,
                max_tool_calls=draft.max_tool_calls,
                enabled=draft.enabled,
                revision=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    ProjectExecutionPolicyTable.owner_id,
                    ProjectExecutionPolicyTable.project_id,
                ],
                set_={
                    "workspace_path": draft.workspace_path,
                    "allowed_tools": list(draft.allowed_tools),
                    "max_duration_seconds": draft.max_duration_seconds,
                    "max_inference_turns": draft.max_inference_turns,
                    "max_tool_calls": draft.max_tool_calls,
                    "enabled": draft.enabled,
                    "revision": ProjectExecutionPolicyTable.revision + 1,
                    "updated_at": func.now(),
                },
            )
            .returning(ProjectExecutionPolicyTable)
        )
        async with self._sessions.begin() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        return None if row is None else _workspace_policy(row)

    async def get_ready_runner(self, *, max_age_seconds: int) -> RunnerStatus | None:
        threshold = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        async with self._sessions() as session:
            row = await session.scalar(
                select(RunnerConnectionTable)
                .where(
                    RunnerConnectionTable.last_seen_at >= threshold,
                    RunnerConnectionTable.provider_ready.is_(True),
                )
                .order_by(RunnerConnectionTable.last_seen_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            return RunnerStatus(
                runner_id=row.runner_id,
                last_seen_at=row.last_seen_at,
                provider_ready=row.provider_ready,
            )

    async def get_runner_status(
        self, runner_id: UUID, *, max_age_seconds: int
    ) -> RunnerStatus | None:
        threshold = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        async with self._sessions() as session:
            row = await session.scalar(
                select(RunnerConnectionTable).where(
                    RunnerConnectionTable.runner_id == runner_id,
                    RunnerConnectionTable.last_seen_at >= threshold,
                )
            )
            if row is None:
                return None
            return RunnerStatus(
                runner_id=row.runner_id,
                last_seen_at=row.last_seen_at,
                provider_ready=row.provider_ready,
            )

    async def register_runner(self, runner_id: UUID, *, provider_ready: bool) -> None:
        statement = (
            insert(RunnerConnectionTable)
            .values(runner_id=runner_id, provider_ready=provider_ready)
            .on_conflict_do_update(
                index_elements=[RunnerConnectionTable.runner_id],
                set_={"provider_ready": provider_ready, "last_seen_at": func.now()},
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)


    async def claim_next(
        self, runner_id: UUID, *, lease_seconds: int
    ) -> RunExecutionJob | None:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        async with self._sessions.begin() as session:
            # Keep claim transactions short and serialized: two workers must not
            # select different queued Runs for one workspace before either leases it.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended("agent-office:workspace-claim", 0)
                    )
                )
            )
            await session.execute(
                update(RunDispatchTable)
                .where(
                    RunDispatchTable.state == "leased",
                    RunDispatchTable.lease_expires_at < now,
                )
                .values(state="unknown", updated_at=now)
            )
            active = aliased(RunDispatchTable)
            dispatch = await session.scalar(
                select(RunDispatchTable)
                .where(
                    RunDispatchTable.state == "queued",
                    ~select(active.run_id)
                    .where(
                        active.owner_id == RunDispatchTable.owner_id,
                        active.workspace_path == RunDispatchTable.workspace_path,
                        active.state == "leased",
                    )
                    .exists(),
                )
                .order_by(RunDispatchTable.created_at, RunDispatchTable.run_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if dispatch is None:
                return None
            policy = await session.scalar(
                select(ProjectExecutionPolicyTable).where(
                    ProjectExecutionPolicyTable.owner_id == dispatch.owner_id,
                    ProjectExecutionPolicyTable.project_id == dispatch.project_id,
                )
            )
            if (
                policy is None
                or not policy.enabled
                or policy.revision != dispatch.policy_revision
                or policy.workspace_path != dispatch.workspace_path
            ):
                dispatch.state = "blocked"
                dispatch.updated_at = now
                return None
            run = await session.scalar(
                select(RunTable).where(
                    RunTable.owner_id == dispatch.owner_id,
                    RunTable.id == dispatch.run_id,
                )
            )
            if run is None:
                dispatch.state = "blocked"
                dispatch.updated_at = now
                return None
            dispatch.state = "leased"
            dispatch.runner_id = runner_id
            dispatch.lease_expires_at = lease_expires_at
            dispatch.updated_at = now
            job = RunExecutionJob(
                owner_id=dispatch.owner_id,
                run_id=dispatch.run_id,
                task_id=dispatch.task_id,
                project_id=dispatch.project_id,
                instance_id=dispatch.instance_id,
                runner_id=runner_id,
                configuration_snapshot=dict(run.configuration_snapshot),
                policy=_workspace_policy(policy),
                lease_expires_at=lease_expires_at,
            )
        return job

    async def heartbeat_run(
        self, runner_id: UUID, run_id: UUID, *, lease_seconds: int
    ) -> bool:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(RunDispatchTable)
                .where(
                    RunDispatchTable.run_id == run_id,
                    RunDispatchTable.runner_id == runner_id,
                    RunDispatchTable.state == "leased",
                    RunDispatchTable.lease_expires_at >= now,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            return bool(result.rowcount)

    async def runner_owns_run(self, runner_id: UUID, run_id: UUID) -> bool:
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(RunDispatchTable.run_id).where(
                        RunDispatchTable.run_id == run_id,
                        RunDispatchTable.runner_id == runner_id,
                        RunDispatchTable.state.in_(("leased", "unknown", "succeeded", "failed", "canceled")),
                    )
                )
            )

    async def get_run_context_for_runner(
        self, runner_id: UUID, run_id: UUID
    ) -> RunnerRunContext | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(RunDispatchTable, RunTable.task_revision)
                    .join(
                        RunTable,
                        (RunTable.owner_id == RunDispatchTable.owner_id)
                        & (RunTable.id == RunDispatchTable.run_id),
                    )
                    .where(
                        RunDispatchTable.run_id == run_id,
                        RunDispatchTable.runner_id == runner_id,
                        RunDispatchTable.state.in_(
                            ("leased", "unknown", "succeeded", "failed", "canceled")
                        ),
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            dispatch, task_revision = row
            return RunnerRunContext(
                owner_id=dispatch.owner_id,
                run_id=dispatch.run_id,
                task_id=dispatch.task_id,
                project_id=dispatch.project_id,
                instance_id=dispatch.instance_id,
                task_revision=task_revision,
            )
