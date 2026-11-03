from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.orchestration import OrchestrationInputError
from app.infrastructure.postgres.orchestration import PostgresOrchestrationRepository


def test_active_workflow_scan_includes_missing_and_unsettled_checkpoints() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")
    async_url = (
        database_url
        if "postgresql+asyncpg://" in database_url
        else database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )

    async def verify() -> None:
        engine = create_async_engine(async_url, pool_size=1)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PostgresOrchestrationRepository(sessions)
        active_missing = (f"active-missing-{uuid4()}", uuid4())
        terminal_missing = (f"terminal-missing-{uuid4()}", uuid4())
        pending_terminal = (f"pending-terminal-{uuid4()}", uuid4())
        malformed = (f"malformed-{uuid4()}", uuid4())
        try:
            async with sessions() as session:
                await session.execute(text("""
                    CREATE TEMP TABLE orchestration_delegations (
                        owner_id text, parent_run_id uuid, status text
                    )
                """))
                await session.execute(text("""
                    CREATE TEMP TABLE orchestration_checkpoints (
                        owner_id text, parent_run_id uuid, state jsonb
                    )
                """))
                await session.execute(text("""
                    INSERT INTO orchestration_delegations(owner_id,parent_run_id,status) VALUES
                        (:active_owner,:active_run,'waiting_approval'),
                        (:terminal_owner,:terminal_run,'succeeded'),
                        (:pending_owner,:pending_run,'succeeded'),
                        (:malformed_owner,:malformed_run,'succeeded')
                """), {
                    "active_owner": active_missing[0], "active_run": active_missing[1],
                    "terminal_owner": terminal_missing[0], "terminal_run": terminal_missing[1],
                    "pending_owner": pending_terminal[0], "pending_run": pending_terminal[1],
                    "malformed_owner": malformed[0], "malformed_run": malformed[1],
                })
                await session.execute(text("""
                    INSERT INTO orchestration_checkpoints(owner_id,parent_run_id,state) VALUES
                        (:pending_owner,:pending_run,
                            '{"status":"succeeded","pending_status_event":{"status":"succeeded"}}'::jsonb),
                        (:malformed_owner,:malformed_run,'[]'::jsonb)
                """), {
                    "pending_owner": pending_terminal[0], "pending_run": pending_terminal[1],
                    "malformed_owner": malformed[0], "malformed_run": malformed[1],
                })
                await session.commit()

            recovered = set(await repository.active_parent_runs())
            assert recovered == {active_missing, pending_terminal, malformed}
            with pytest.raises(OrchestrationInputError, match="workflow_active"):
                await repository.persist_plan_with_tasks(
                    active_missing[0],
                    SimpleNamespace(parent_run_id=uuid4()),
                    {},
                )
        finally:
            await engine.dispose()

    asyncio.run(verify())
