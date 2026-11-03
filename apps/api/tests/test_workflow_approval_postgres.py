from __future__ import annotations

import asyncio
import os
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.runs import RunInputError, WorkflowDelegationBinding
from app.infrastructure.postgres.raw_sql import RawSQLExecutor
from app.infrastructure.postgres.runs import PostgresRunRepository


def test_workflow_child_creation_requires_current_bound_approval() -> None:
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
        engine = create_async_engine(async_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PostgresRunRepository(sessions)
        owner_id = f"workflow-approval-test-{uuid4()}"
        parent_run_id = uuid4()
        child_task_id = uuid4()
        approval_id = uuid4()
        child_run_id = uuid4()
        node_id = "child"
        canonical_digest = "a" * 64
        binding = WorkflowDelegationBinding(parent_run_id, node_id, canonical_digest)
        try:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(text("""
                        CREATE TEMP TABLE orchestration_delegations (
                            owner_id text, parent_run_id uuid, node_id text,
                            child_task_id uuid, status text, child_run_id uuid,
                            canonical_digest text, approval_id uuid, updated_at timestamptz
                        ) ON COMMIT DROP
                    """))
                    await session.execute(text("""
                        CREATE TEMP TABLE approval_requests (
                            owner_id text, id uuid, run_id uuid, action_type text,
                            action_digest text, resource_ref text, expires_at timestamptz
                        ) ON COMMIT DROP
                    """))
                    await session.execute(text("""
                        CREATE TEMP TABLE approval_decisions (
                            owner_id text, approval_id uuid, decision text
                        ) ON COMMIT DROP
                    """))
                    await session.execute(text("""
                        INSERT INTO orchestration_delegations
                            (owner_id,parent_run_id,node_id,child_task_id,status,canonical_digest,approval_id)
                        VALUES (:owner,:parent,:node,:task,'approved',:digest,:approval)
                    """), {
                        "owner": owner_id, "parent": parent_run_id, "node": node_id,
                        "task": child_task_id, "digest": canonical_digest, "approval": approval_id,
                    })
                    await session.execute(text("""
                        INSERT INTO approval_requests
                            (owner_id,id,run_id,action_type,action_digest,resource_ref,expires_at)
                        VALUES (:owner,:approval,:parent,'workflow.delegation',:digest,:resource,now()+interval '1 hour')
                    """), {
                        "owner": owner_id, "approval": approval_id, "parent": parent_run_id,
                        "digest": canonical_digest,
                        "resource": f"workflow:{parent_run_id}:{node_id}",
                    })
                    await session.execute(text("""
                        INSERT INTO approval_decisions(owner_id,approval_id,decision)
                        VALUES (:owner,:approval,'approved')
                    """), {"owner": owner_id, "approval": approval_id})
                    connection = RawSQLExecutor(session)

                    await repository._check_workflow_delegation(
                        connection, owner_id, child_task_id, binding, existing_run_id=None,
                    )
                    await session.execute(text(
                        "UPDATE approval_requests SET expires_at=clock_timestamp()-interval '1 second'"
                    ))
                    with pytest.raises(RunInputError, match="workflow_approval"):
                        await repository._attach_workflow_run(
                            connection, owner_id, binding, child_task_id, child_run_id,
                        )

                    await session.execute(text(
                        "UPDATE approval_requests SET expires_at=clock_timestamp()+interval '1 hour'"
                    ))
                    await repository._attach_workflow_run(
                        connection, owner_id, binding, child_task_id, child_run_id,
                    )
                    assert await connection.fetchval(
                        "SELECT child_run_id FROM orchestration_delegations "
                        "WHERE owner_id=$1 AND parent_run_id=$2 AND node_id=$3",
                        owner_id, parent_run_id, node_id,
                    ) == child_run_id
                    await session.execute(text(
                        "UPDATE approval_requests SET expires_at=clock_timestamp()-interval '1 second'"
                    ))
                    await repository._check_workflow_delegation(
                        connection, owner_id, child_task_id, binding,
                        existing_run_id=child_run_id,
                    )
        finally:
            await engine.dispose()

    asyncio.run(verify())
