from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.evidence import (
    ACTION_DIGEST_RE,
    EVENT_STATUS,
    TERMINAL_RUN_STATES,
    ActivityEvent,
    ActivityEventDraft,
    ActivityEventRepository,
    ActivitySnapshot,
    ApprovalRequest,
    ApprovalRequestDraft,
    ApprovalRepository,
    Artifact,
    ArtifactDraft,
    ArtifactRepository,
    EvidenceInputError,
    MAX_SCENE_CONTENT_BYTES,
    OfficeSceneRepository,
    OfficeSceneVersion,
    RunActivityProjection,
    SCENE_ID_RE,
    _is_newer,
    _same_event,
)
from app.infrastructure.postgres.models import (
    ApprovalDecisionTable,
    ApprovalRequestTable,
    ArtifactTable,
    OfficeSceneVersionTable,
    RunTable,
)
from app.infrastructure.postgres.raw_sql import RawSQLConnectionPool, RawSQLExecutor




class PostgresActivityEventRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._pool = RawSQLConnectionPool(sessions)

    async def append_event(self, event: ActivityEventDraft) -> tuple[ActivityEvent, bool]:
        if event.event_type.removeprefix("run.") in TERMINAL_RUN_STATES:
            raise EvidenceInputError("terminal_transition_required")
        return await self._append_event(event, terminal_runner_id=None)

    async def append_terminal_event(
        self, event: ActivityEventDraft, runner_id: UUID
    ) -> tuple[ActivityEvent, bool]:
        if event.event_type.removeprefix("run.") not in TERMINAL_RUN_STATES:
            raise EvidenceInputError("terminal_event")
        if event.source_id != str(runner_id):
            raise EvidenceInputError("runner")
        return await self._append_event(event, terminal_runner_id=runner_id)

    async def _append_event(
        self, event: ActivityEventDraft, *, terminal_runner_id: UUID | None
    ) -> tuple[ActivityEvent, bool]:
        event.validate()
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                dedupe_key = f"event:{event.owner_id}:{event.source_id}:{event.event_id}"
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", dedupe_key
                )
                existing = await connection.fetchrow(
                    _EVENT_SELECT
                    + " WHERE owner_id = $1 AND source_id = $2 AND event_id = $3",
                    event.owner_id,
                    event.source_id,
                    event.event_id,
                )
                if existing is not None:
                    if not _same_event(existing, event):
                        raise EvidenceInputError("event_id_conflict")
                    if terminal_runner_id is not None:
                        await self._finalize_terminal_dispatch(
                            connection,
                            event,
                            terminal_runner_id,
                            received_at=existing["received_at"],
                        )
                    return _activity_event(existing), False

                scope = await connection.fetchrow(
                    """
                    SELECT 1
                    FROM runs AS run
                    JOIN task_revisions AS revision
                      ON revision.owner_id = run.owner_id
                     AND revision.task_id = run.task_id
                     AND revision.revision = run.task_revision
                     AND revision.project_id = $5
                    JOIN agent_instances AS instance
                      ON instance.owner_id = run.owner_id
                     AND instance.id = $6
                    WHERE run.owner_id = $1 AND run.id = $2
                      AND run.task_id = $3 AND run.task_revision = $4
                      AND run.configuration_snapshot #>> '{task,project_id}' = $5::text
                      AND run.configuration_snapshot #>> '{agent_instance,id}' = $6::text
                    """,
                    event.owner_id,
                    event.run_id,
                    event.task_id,
                    event.task_revision,
                    event.project_id,
                    event.agent_instance_id,
                )
                if scope is None:
                    raise EvidenceInputError("scope")

                if terminal_runner_id is not None:
                    await self._finalize_terminal_dispatch(
                        connection,
                        event,
                        terminal_runner_id,
                        received_at=None,
                    )

                cursor = await connection.fetchval(
                    """
                    INSERT INTO owner_event_cursors (owner_id, cursor)
                    VALUES ($1, 1)
                    ON CONFLICT (owner_id) DO UPDATE
                    SET cursor = owner_event_cursors.cursor + 1
                    RETURNING cursor
                    """,
                    event.owner_id,
                )
                row = await connection.fetchrow(
                    """
                    INSERT INTO activity_events (
                        owner_id, cursor, source_id, event_id, schema_version,
                        source_session_id, workspace_id, project_id, task_id,
                        task_revision, agent_instance_id, run_id, event_type,
                        occurred_at, source_sequence, action_summary
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16)
                    RETURNING owner_id, cursor, source_id, event_id, schema_version,
                              source_session_id, workspace_id, project_id, task_id,
                              task_revision, agent_instance_id, run_id, event_type,
                              occurred_at, received_at, source_sequence, action_summary
                    """,
                    event.owner_id,
                    cursor,
                    event.source_id,
                    event.event_id,
                    event.schema_version,
                    event.source_session_id,
                    event.workspace_id,
                    event.project_id,
                    event.task_id,
                    event.task_revision,
                    event.agent_instance_id,
                    event.run_id,
                    event.event_type,
                    event.occurred_at,
                    event.source_sequence,
                    event.action_summary,
                )
                if row is None:
                    raise RuntimeError("activity event insert returned no row")
                await connection.execute(
                    """
                    INSERT INTO activity_event_outbox (owner_id, cursor)
                    VALUES ($1, $2)
                    """,
                    event.owner_id,
                    cursor,
                )
                await self._project_event(connection, event, cursor)
                return _activity_event(row), True

    async def _finalize_terminal_dispatch(
        self,
        connection: RawSQLExecutor,
        event: ActivityEventDraft,
        runner_id: UUID,
        *,
        received_at: datetime | None,
    ) -> None:
        terminal_state = event.event_type.removeprefix("run.")
        if received_at is None:
            row = await connection.fetchrow(
                """
                UPDATE run_dispatches
                SET state = $4, lease_expires_at = NULL, updated_at = now()
                WHERE owner_id = $1 AND run_id = $2 AND runner_id = $3
                  AND state = 'leased' AND lease_expires_at >= now()
                RETURNING run_id
                """,
                event.owner_id,
                event.run_id,
                runner_id,
                terminal_state,
            )
            if row is None:
                raise EvidenceInputError("lease")
            return

        dispatch = await connection.fetchrow(
            """
            SELECT state, lease_expires_at
            FROM run_dispatches
            WHERE owner_id = $1 AND run_id = $2 AND runner_id = $3
            FOR UPDATE
            """,
            event.owner_id,
            event.run_id,
            runner_id,
        )
        if dispatch is None:
            raise EvidenceInputError("lease")
        if dispatch["state"] == terminal_state:
            return
        if (
            dispatch["state"] not in {"leased", "unknown"}
            or dispatch["lease_expires_at"] is None
            or dispatch["lease_expires_at"] < received_at
        ):
            raise EvidenceInputError("lease")
        row = await connection.fetchrow(
            """
            UPDATE run_dispatches
            SET state = $4, lease_expires_at = NULL, updated_at = now()
            WHERE owner_id = $1 AND run_id = $2 AND runner_id = $3
              AND state IN ('leased', 'unknown') AND lease_expires_at >= $5
            RETURNING run_id
            """,
            event.owner_id,
            event.run_id,
            runner_id,
            terminal_state,
            received_at,
        )
        if row is None:
            raise EvidenceInputError("lease")
    async def has_event_for_runner(
        self, owner_id: str, source_id: str, run_id: UUID, event_id: UUID
    ) -> bool:
        async with self._pool.acquire() as connection:
            return bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM activity_events
                        WHERE owner_id = $1 AND source_id = $2
                          AND run_id = $3 AND event_id = $4
                    )
                    """,
                    owner_id,
                    source_id,
                    run_id,
                    event_id,
                )
            )


    async def _project_event(
        self,
        connection: RawSQLExecutor,
        event: ActivityEventDraft,
        cursor: int,
    ) -> None:
        current = await connection.fetchrow(
            """
            SELECT source_id, status, last_event_cursor, last_event_type,
                   last_event_at, last_sequence, last_heartbeat_at, last_action_summary
            FROM run_activity_projections
            WHERE owner_id = $1 AND run_id = $2
            FOR UPDATE
            """,
            event.owner_id,
            event.run_id,
        )
        if current is not None and current["source_id"] != event.source_id:
            raise EvidenceInputError("source_conflict")

        if event.event_type == "session.heartbeat":
            if current is not None and event.occurred_at > (
                current["last_heartbeat_at"] or datetime.min.replace(tzinfo=timezone.utc)
            ):
                await connection.execute(
                    """
                    UPDATE run_activity_projections
                    SET last_heartbeat_at = $3, updated_at = now()
                    WHERE owner_id = $1 AND run_id = $2
                    """,
                    event.owner_id,
                    event.run_id,
                    event.occurred_at,
                )
            return

        if event.event_type == "run.tool_action":
            if current is None or current["status"] in TERMINAL_RUN_STATES:
                return
            projection = RunActivityProjection(
                owner_id=event.owner_id,
                run_id=event.run_id,
                source_id=current["source_id"],
                task_id=event.task_id,
                task_revision=event.task_revision,
                project_id=event.project_id,
                agent_instance_id=event.agent_instance_id,
                status=current["status"],
                last_event_cursor=current["last_event_cursor"],
                last_event_type=current["last_event_type"],
                last_event_at=current["last_event_at"],
                last_sequence=current["last_sequence"],
                last_heartbeat_at=current["last_heartbeat_at"],
                last_action_summary=current["last_action_summary"],
                updated_at=event.occurred_at,
            )
            if not _is_newer(event, projection):
                return
            await connection.execute(
                """
                UPDATE run_activity_projections
                SET last_event_cursor = $3, last_event_type = $4, last_event_at = $5,
                    last_sequence = $6, last_action_summary = $7, updated_at = now()
                WHERE owner_id = $1 AND run_id = $2
                """,
                event.owner_id,
                event.run_id,
                cursor,
                event.event_type,
                event.occurred_at,
                event.source_sequence,
                event.action_summary,
            )
            return

        mapped_status = EVENT_STATUS.get(event.event_type)
        if mapped_status is None:
            return
        if current is not None:
            if current["status"] in TERMINAL_RUN_STATES:
                return
            projection = RunActivityProjection(
                owner_id=event.owner_id,
                run_id=event.run_id,
                source_id=current["source_id"],
                task_id=event.task_id,
                task_revision=event.task_revision,
                project_id=event.project_id,
                agent_instance_id=event.agent_instance_id,
                status=current["status"],
                last_event_cursor=current["last_event_cursor"],
                last_event_type=current["last_event_type"],
                last_event_at=current["last_event_at"],
                last_sequence=current["last_sequence"],
                last_heartbeat_at=current["last_heartbeat_at"],
                last_action_summary=current["last_action_summary"],
                updated_at=event.occurred_at,
            )
            if not _is_newer(event, projection):
                return
            await connection.execute(
                """
                UPDATE run_activity_projections
                SET status = $3, last_event_cursor = $4, last_event_type = $5,
                    last_event_at = $6, last_sequence = $7, updated_at = now()
                WHERE owner_id = $1 AND run_id = $2
                """,
                event.owner_id,
                event.run_id,
                mapped_status,
                cursor,
                event.event_type,
                event.occurred_at,
                event.source_sequence,
            )
            return

        await connection.execute(
            """
            INSERT INTO run_activity_projections (
                owner_id, run_id, source_id, task_id, task_revision, project_id,
                agent_instance_id, status, last_event_cursor, last_event_type,
                last_event_at, last_sequence, last_action_summary
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            event.owner_id,
            event.run_id,
            event.source_id,
            event.task_id,
            event.task_revision,
            event.project_id,
            event.agent_instance_id,
            mapped_status,
            cursor,
            event.event_type,
            event.occurred_at,
            event.source_sequence,
            event.action_summary,
        )

    async def get_projection_for_run(
        self, owner_id: str, run_id: UUID
    ) -> RunActivityProjection | None:
        row = await self._pool.fetchrow(
            """
            SELECT owner_id, run_id, source_id, task_id, task_revision, project_id,
                   agent_instance_id, status, last_event_cursor, last_event_type,
                   last_event_at, last_sequence, last_heartbeat_at, last_action_summary, updated_at
            FROM run_activity_projections
            WHERE owner_id = $1 AND run_id = $2
            """,
            owner_id,
            run_id,
        )
        return None if row is None else _projection(row)

    async def list_events_for_run(
        self, owner_id: str, run_id: UUID, *, limit: int
    ) -> list[ActivityEvent] | None:
        async with self._pool.acquire() as connection:
            if await connection.fetchval(
                "SELECT 1 FROM runs WHERE owner_id = $1 AND id = $2",
                owner_id,
                run_id,
            ) is None:
                return None
            rows = await connection.fetch(
                _EVENT_SELECT
                + " WHERE owner_id = $1 AND run_id = $2"
                + " ORDER BY cursor DESC LIMIT $3",
                owner_id,
                run_id,
                limit,
            )
        return [_activity_event(row) for row in reversed(rows)]

    async def get_snapshot_for_owner(
        self, owner_id: str, *, limit: int
    ) -> ActivitySnapshot:
        async with self._pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                cursor = await connection.fetchval(
                    "SELECT cursor FROM owner_event_cursors WHERE owner_id = $1", owner_id
                )
                rows = await connection.fetch(
                    """
                    SELECT owner_id, run_id, source_id, task_id, task_revision,
                           project_id, agent_instance_id, status, last_event_cursor,
                           last_event_type, last_event_at, last_sequence,
                           last_heartbeat_at, last_action_summary, updated_at
                    FROM run_activity_projections
                    WHERE owner_id = $1
                    ORDER BY last_event_cursor DESC, run_id
                    LIMIT $2
                    """,
                    owner_id,
                    limit,
                )
        return ActivitySnapshot(
            cursor=0 if cursor is None else cursor,
            projections=tuple(_projection(row) for row in rows),
        )

    async def list_events_after(
        self, owner_id: str, cursor: int, *, limit: int
    ) -> list[ActivityEvent]:
        rows = await self._pool.fetch(
            _EVENT_SELECT
            + " WHERE owner_id = $1 AND cursor > $2 ORDER BY cursor LIMIT $3",
            owner_id,
            cursor,
            limit,
        )
        return [_activity_event(row) for row in rows]

    async def claim_outbox_batch(
        self, claim_id: UUID, *, limit: int, lease_seconds: int
    ) -> list[ActivityEvent]:
        async with self._pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch(
                """
                WITH candidates AS (
                    SELECT pending.owner_id, pending.cursor
                    FROM activity_event_outbox AS pending
                    WHERE pending.published_at IS NULL
                      AND (pending.lease_expires_at IS NULL
                           OR pending.lease_expires_at <= now())
                      AND NOT EXISTS (
                          SELECT 1
                          FROM activity_event_outbox AS earlier
                          WHERE earlier.owner_id = pending.owner_id
                            AND earlier.cursor < pending.cursor
                            AND earlier.published_at IS NULL
                      )
                    ORDER BY pending.created_at, pending.owner_id, pending.cursor
                    FOR UPDATE OF pending SKIP LOCKED
                    LIMIT $3
                ),
                claimed AS (
                    UPDATE activity_event_outbox AS outbox
                    SET claim_id = $1,
                        lease_expires_at = now() + ($2 * interval '1 second'),
                        attempt_count = outbox.attempt_count + 1
                    FROM candidates
                    WHERE outbox.owner_id = candidates.owner_id
                      AND outbox.cursor = candidates.cursor
                    RETURNING outbox.owner_id, outbox.cursor
                )
                SELECT event.owner_id, event.cursor, event.source_id, event.event_id,
                       event.schema_version, event.source_session_id, event.workspace_id,
                       event.project_id, event.task_id, event.task_revision,
                       event.agent_instance_id, event.run_id, event.event_type,
                       event.occurred_at, event.received_at, event.source_sequence,
                       event.action_summary
                FROM activity_events AS event
                JOIN claimed
                  ON claimed.owner_id = event.owner_id AND claimed.cursor = event.cursor
                ORDER BY event.owner_id, event.cursor
                """,
                claim_id,
                lease_seconds,
                limit,
            )
        return [_activity_event(row) for row in rows]

    async def mark_outbox_published(
        self, owner_id: str, cursor: int, claim_id: UUID
    ) -> bool:
        async with self._pool.acquire() as connection, connection.transaction():
            return bool(
                await connection.fetchval(
                    """
                    UPDATE activity_event_outbox
                    SET published_at = now(), claim_id = NULL, lease_expires_at = NULL
                    WHERE owner_id = $1 AND cursor = $2 AND claim_id = $3
                      AND published_at IS NULL
                    RETURNING true
                    """,
                    owner_id,
                    cursor,
                    claim_id,
                )
            )

    async def release_outbox_claim(
        self, owner_id: str, cursor: int, claim_id: UUID
    ) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                """
                UPDATE activity_event_outbox
                SET claim_id = NULL, lease_expires_at = NULL
                WHERE owner_id = $1 AND cursor = $2 AND claim_id = $3
                  AND published_at IS NULL
                """,
                owner_id,
                cursor,
                claim_id,
            )



_APPROVAL_SELECT = """
SELECT request.owner_id, request.id, request.run_id, request.request_id,
       request.action_type, request.action_digest, request.resource_ref,
       request.created_at, request.expires_at, decision.decision, decision.decided_at
FROM approval_requests AS request
LEFT JOIN approval_decisions AS decision
  ON decision.owner_id = request.owner_id AND decision.approval_id = request.id
"""


def _approval(
    request: ApprovalRequestTable,
    decision: ApprovalDecisionTable | None = None,
) -> ApprovalRequest:
    return ApprovalRequest(
        owner_id=request.owner_id,
        id=request.id,
        run_id=request.run_id,
        request_id=request.request_id,
        action_type=request.action_type,
        action_digest=request.action_digest,
        resource_ref=request.resource_ref,
        created_at=request.created_at,
        expires_at=request.expires_at,
        decision=decision.decision if decision is not None else None,
        decided_at=decision.decided_at if decision is not None else None,
    )


class PostgresApprovalRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_request(
        self, draft: ApprovalRequestDraft
    ) -> tuple[ApprovalRequest, bool]:
        draft.validate()
        async with self._sessions.begin() as session:
            raw = RawSQLExecutor(session)
            await raw.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"approval:{draft.owner_id}:{draft.run_id}:{draft.request_id}",
            )
            existing = (
                await session.execute(
                    select(ApprovalRequestTable, ApprovalDecisionTable)
                    .outerjoin(
                        ApprovalDecisionTable,
                        (ApprovalDecisionTable.owner_id == ApprovalRequestTable.owner_id)
                        & (ApprovalDecisionTable.approval_id == ApprovalRequestTable.id),
                    )
                    .where(
                        ApprovalRequestTable.owner_id == draft.owner_id,
                        ApprovalRequestTable.run_id == draft.run_id,
                        ApprovalRequestTable.request_id == draft.request_id,
                    )
                )
            ).one_or_none()
            if existing is not None:
                request, decision = existing
                if (
                    request.action_digest != draft.action_digest
                    or request.action_type != draft.action_type
                    or request.resource_ref != draft.resource_ref
                ):
                    raise EvidenceInputError("approval_request_conflict")
                return _approval(request, decision), False

            request = ApprovalRequestTable(
                owner_id=draft.owner_id,
                id=uuid4(),
                run_id=draft.run_id,
                request_id=draft.request_id,
                action_type=draft.action_type,
                action_digest=draft.action_digest,
                resource_ref=draft.resource_ref,
                expires_at=draft.expires_at,
            )
            session.add(request)
            await session.flush()
            return _approval(request), True

    async def resolve(
        self,
        owner_id: str,
        approval_id: UUID,
        action_digest: str,
        decision: str,
        now: datetime,
    ) -> ApprovalRequest:
        if decision not in {"approved", "rejected", "expired"}:
            raise EvidenceInputError("decision")
        if ACTION_DIGEST_RE.fullmatch(action_digest) is None:
            raise EvidenceInputError("action_digest")
        if now.tzinfo is None or now.utcoffset() is None:
            raise EvidenceInputError("decided_at")
        async with self._sessions.begin() as session:
            await RawSQLExecutor(session).execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"approval:{owner_id}:{approval_id}",
            )
            request = await session.scalar(
                select(ApprovalRequestTable).where(
                    ApprovalRequestTable.owner_id == owner_id,
                    ApprovalRequestTable.id == approval_id,
                )
            )
            if request is None:
                raise EvidenceInputError("approval")
            if request.action_digest != action_digest:
                raise EvidenceInputError("action_digest_mismatch")
            current = await session.scalar(
                select(ApprovalDecisionTable).where(
                    ApprovalDecisionTable.owner_id == owner_id,
                    ApprovalDecisionTable.approval_id == approval_id,
                )
            )
            if current is not None:
                if current.decision != decision:
                    raise EvidenceInputError("decision_conflict")
                return _approval(request, current)
            if request.expires_at <= now and decision != "expired":
                raise EvidenceInputError("approval_expired")
            if request.expires_at > now and decision == "expired":
                raise EvidenceInputError("approval_not_expired")
            resolved = ApprovalDecisionTable(
                owner_id=owner_id,
                approval_id=approval_id,
                decision=decision,
                decided_at=now,
            )
            session.add(resolved)
            await session.flush()
            return _approval(request, resolved)

    async def get_for_owner(
        self, owner_id: str, approval_id: UUID
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequestTable, ApprovalDecisionTable)
            .outerjoin(
                ApprovalDecisionTable,
                (ApprovalDecisionTable.owner_id == ApprovalRequestTable.owner_id)
                & (ApprovalDecisionTable.approval_id == ApprovalRequestTable.id),
            )
            .where(
                ApprovalRequestTable.owner_id == owner_id,
                ApprovalRequestTable.id == approval_id,
            )
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).one_or_none()
            return None if row is None else _approval(row[0], row[1])

    async def list_for_run(
        self, owner_id: str, run_id: UUID, *, limit: int
    ) -> list[ApprovalRequest] | None:
        async with self._sessions() as session:
            if await session.scalar(
                select(RunTable.id).where(
                    RunTable.owner_id == owner_id,
                    RunTable.id == run_id,
                )
            ) is None:
                return None
            statement = (
                select(ApprovalRequestTable, ApprovalDecisionTable)
                .outerjoin(
                    ApprovalDecisionTable,
                    (ApprovalDecisionTable.owner_id == ApprovalRequestTable.owner_id)
                    & (ApprovalDecisionTable.approval_id == ApprovalRequestTable.id),
                )
                .where(
                    ApprovalRequestTable.owner_id == owner_id,
                    ApprovalRequestTable.run_id == run_id,
                )
                .order_by(ApprovalRequestTable.created_at, ApprovalRequestTable.id)
                .limit(limit)
            )
            rows = (await session.execute(statement)).all()
            return [_approval(request, decision) for request, decision in rows]


def _artifact(row: ArtifactTable) -> Artifact:
    return Artifact(
        owner_id=row.owner_id,
        id=row.id,
        run_id=row.run_id,
        artifact_type=row.artifact_type,
        display_name=row.display_name,
        media_type=row.media_type,
        byte_length=row.byte_length,
        sha256=row.sha256,
        storage_key=row.storage_key,
        created_at=row.created_at,
    )


class PostgresArtifactRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, draft: ArtifactDraft) -> Artifact:
        draft.validate()
        artifact = ArtifactTable(
            owner_id=draft.owner_id,
            id=uuid4(),
            run_id=draft.run_id,
            artifact_type=draft.artifact_type,
            display_name=draft.display_name.strip(),
            media_type=draft.media_type,
            byte_length=draft.byte_length,
            sha256=draft.sha256,
            storage_key=draft.storage_key,
        )
        async with self._sessions.begin() as session:
            session.add(artifact)
            await session.flush()
            return _artifact(artifact)

    async def list_for_run(self, owner_id: str, run_id: UUID) -> list[Artifact] | None:
        async with self._sessions() as session:
            if await session.scalar(
                select(RunTable.id).where(RunTable.owner_id == owner_id, RunTable.id == run_id)
            ) is None:
                return None
            rows = await session.scalars(
                select(ArtifactTable)
                .where(ArtifactTable.owner_id == owner_id, ArtifactTable.run_id == run_id)
                .order_by(ArtifactTable.created_at, ArtifactTable.id)
            )
            return [_artifact(row) for row in rows]


class PostgresOfficeSceneRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_version(
        self,
        owner_id: str,
        scene_id: str,
        contents: dict[str, object],
        expected_previous_version: int | None,
    ) -> OfficeSceneVersion:
        if SCENE_ID_RE.fullmatch(scene_id) is None:
            raise EvidenceInputError("scene_id")
        if expected_previous_version is not None and expected_previous_version < 0:
            raise EvidenceInputError("expected_previous_version")
        try:
            encoded = json.dumps(contents, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            serialized_contents = json.loads(encoded)
        except (TypeError, ValueError, RecursionError) as error:
            raise EvidenceInputError("contents") from error
        if len(encoded.encode("utf-8")) > MAX_SCENE_CONTENT_BYTES:
            raise EvidenceInputError("contents_size")
        async with self._sessions.begin() as session:
            raw = RawSQLExecutor(session)
            await raw.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"scene:{owner_id}:{scene_id}",
            )
            previous = await session.scalar(
                select(func.coalesce(func.max(OfficeSceneVersionTable.version), 0)).where(
                    OfficeSceneVersionTable.owner_id == owner_id,
                    OfficeSceneVersionTable.scene_id == scene_id,
                )
            )
            if expected_previous_version is not None and previous != expected_previous_version:
                raise EvidenceInputError("scene_version_conflict")
            scene = OfficeSceneVersionTable(
                owner_id=owner_id,
                scene_id=scene_id,
                version=previous + 1,
                contents=serialized_contents,
            )
            session.add(scene)
            await session.flush()
            return OfficeSceneVersion(
                owner_id=scene.owner_id,
                scene_id=scene.scene_id,
                version=scene.version,
                contents=scene.contents,
                created_at=scene.created_at,
            )

    async def get_version(
        self, owner_id: str, scene_id: str, version: int | None = None
    ) -> OfficeSceneVersion | None:
        if SCENE_ID_RE.fullmatch(scene_id) is None:
            raise EvidenceInputError("scene_id")
        statement = select(OfficeSceneVersionTable).where(
            OfficeSceneVersionTable.owner_id == owner_id,
            OfficeSceneVersionTable.scene_id == scene_id,
        )
        if version is not None:
            statement = statement.where(OfficeSceneVersionTable.version == version)
        statement = statement.order_by(OfficeSceneVersionTable.version.desc()).limit(1)
        async with self._sessions() as session:
            scene = await session.scalar(statement)
            if scene is None:
                return None
            return OfficeSceneVersion(
                owner_id=scene.owner_id,
                scene_id=scene.scene_id,
                version=scene.version,
                contents=scene.contents,
                created_at=scene.created_at,
            )




_EVENT_SELECT = """
SELECT owner_id, cursor, source_id, event_id, schema_version,
       source_session_id, workspace_id, project_id, task_id, task_revision,
       agent_instance_id, run_id, event_type, occurred_at, received_at,
       source_sequence, action_summary
FROM activity_events
"""


def _activity_event(row: Mapping[str, Any]) -> ActivityEvent:
    return ActivityEvent(**dict(row))


def _projection(row: Mapping[str, Any]) -> RunActivityProjection:
    return RunActivityProjection(**dict(row))


