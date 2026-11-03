from __future__ import annotations

import asyncio
import os
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.postgres.orchestration import PostgresOrchestrationRepository


def test_workflow_start_lock_serializes_competing_database_sessions() -> None:
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
        engine = create_async_engine(async_url, pool_size=2)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        first = PostgresOrchestrationRepository(sessions)
        second = PostgresOrchestrationRepository(sessions)
        owner_id = f"workflow-lock-test-{uuid4()}"
        attempting = asyncio.Event()
        acquired = asyncio.Event()

        async def contender() -> None:
            attempting.set()
            async with second.workflow_start_lock(owner_id):
                acquired.set()

        task: asyncio.Task[None] | None = None
        try:
            async with first.workflow_start_lock(owner_id):
                task = asyncio.create_task(contender())
                await asyncio.wait_for(attempting.wait(), timeout=1)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(acquired.wait(), timeout=0.05)
            assert task is not None
            await asyncio.wait_for(task, timeout=2)
            assert acquired.is_set()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()

    asyncio.run(verify())
