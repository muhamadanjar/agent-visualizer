from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.run_results import RunResult, RunResultConflictError
from app.infrastructure.postgres.models import RunResultTable

MAX_RUN_RESULT_BYTES = 1_048_576


def _result(row: RunResultTable) -> RunResult:
    return RunResult(
        owner_id=row.owner_id,
        run_id=row.run_id,
        text=row.result_text,
        byte_length=row.byte_length,
        created_at=row.created_at,
    )


class PostgresRunResultRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_for_run(
        self, owner_id: str, run_id: UUID, text: str
    ) -> tuple[RunResult, bool]:
        if not text.strip():
            raise ValueError("Run Result must not be blank")
        byte_length = len(text.encode("utf-8"))
        if byte_length > MAX_RUN_RESULT_BYTES:
            raise ValueError("Run Result exceeded size limit")
        statement = (
            insert(RunResultTable)
            .values(
                owner_id=owner_id,
                run_id=run_id,
                result_text=text,
                byte_length=byte_length,
            )
            .on_conflict_do_nothing(
                index_elements=[RunResultTable.owner_id, RunResultTable.run_id]
            )
            .returning(RunResultTable)
        )
        async with self._sessions.begin() as session:
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return _result(inserted), True
            existing = await session.scalar(
                select(RunResultTable).where(
                    RunResultTable.owner_id == owner_id,
                    RunResultTable.run_id == run_id,
                )
            )
            if existing is None:
                raise RuntimeError("Run Result conflict did not resolve")
            if existing.result_text != text:
                raise RunResultConflictError("Run Result already differs")
            return _result(existing), False

    async def get_for_owner(self, owner_id: str, run_id: UUID) -> RunResult | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(RunResultTable).where(
                    RunResultTable.owner_id == owner_id,
                    RunResultTable.run_id == run_id,
                )
            )
            return None if row is None else _result(row)
