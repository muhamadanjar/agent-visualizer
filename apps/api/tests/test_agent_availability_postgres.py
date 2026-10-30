from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.availability import AgentAvailabilityState
from app.infrastructure.postgres.execution import PostgresExecutionRepository
from app.infrastructure.postgres.migrate import apply_alembic_migrations, apply_migrations


def test_postgres_availability_is_owner_scoped_and_excludes_archived_instances() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    async_database_url = (
        database_url
        if "postgresql+asyncpg://" in database_url
        else database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    owner_id = f"availability-test-{uuid4()}"
    definition_id = uuid4()
    active_instance_id = uuid4()
    archived_instance_id = uuid4()
    foreign_instance_id = uuid4()
    now = datetime.now(UTC)

    async def verify() -> None:
        migration_connection = await asyncpg.connect(dsn=dsn, timeout=5)
        try:
            await apply_migrations(migration_connection)
            await apply_alembic_migrations(dsn)
        finally:
            await migration_connection.close()

        engine = create_async_engine(async_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text(
                            "INSERT INTO agent_definitions (id, owner_id, slug, current_version) "
                            "VALUES (:id, :owner_id, 'availability-test', 1)"
                        ),
                        {"id": definition_id, "owner_id": owner_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO agent_definition_versions "
                            "(definition_id, owner_id, version, name, description, source_markdown, unmapped_metadata) "
                            "VALUES (:definition_id, :owner_id, 1, 'Availability test', '', '# Test', '{}'::jsonb)"
                        ),
                        {"definition_id": definition_id, "owner_id": owner_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO agent_instances (id, owner_id, display_name, definition_id) "
                            "VALUES (:active_id, :owner_id, 'Active availability test', :definition_id), "
                            "(:archived_id, :owner_id, 'Archived availability test', :definition_id)"
                        ),
                        {
                            "active_id": active_instance_id,
                            "owner_id": owner_id,
                            "definition_id": definition_id,
                            "archived_id": archived_instance_id,
                        },
                    )
                    await connection.execute(
                        text("UPDATE agent_instances SET archived_at = :now WHERE id = :id"),
                        {"now": now, "id": archived_instance_id},
                    )

                    sessions = async_sessionmaker(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    )
                    repository = PostgresExecutionRepository(sessions)
                    records = await repository.list_agent_availability(
                        owner_id,
                        [active_instance_id, foreign_instance_id, archived_instance_id],
                        now=now,
                    )
                    actual: dict[UUID, AgentAvailabilityState] = {
                        record.instance_id: record.availability for record in records
                    }
                    assert actual == {
                        active_instance_id: "idle",
                        foreign_instance_id: "unknown",
                        archived_instance_id: "unknown",
                    }
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(verify())
