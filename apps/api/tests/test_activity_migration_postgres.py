from __future__ import annotations

import asyncio
import os
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.infrastructure.postgres.migrate import (
    ALEMBIC_CONFIG,
    apply_alembic_migrations,
    apply_migrations,
)

_PHASE_EVENT_TYPES = ("run.working", "run.tool_started", "run.reviewing")


def test_postgres_activity_phase_migration_round_trips_safely() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    if not unquote(urlsplit(database_url).path.lstrip("/")).endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL *_test database")

    admin_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    database_name = f"agent_office_activity_{uuid4().hex}_test"
    parsed_url = urlsplit(database_url)
    isolated_url = urlunsplit(parsed_url._replace(path=f"/{database_name}"))
    isolated_dsn = isolated_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    isolated_async_url = (
        isolated_url
        if isolated_url.startswith("postgresql+asyncpg://")
        else isolated_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    )

    def alembic_downgrade(connection) -> None:
        config = Config(str(ALEMBIC_CONFIG))
        config.attributes["connection"] = connection
        command.downgrade(config, "gateway_0018")

    async def verify() -> None:
        admin_connection = await asyncpg.connect(dsn=admin_dsn, timeout=5)
        try:
            await admin_connection.execute(f'CREATE DATABASE "{database_name}"')
        finally:
            await admin_connection.close()

        try:
            migration_connection = await asyncpg.connect(dsn=isolated_dsn, timeout=5)
            try:
                await apply_migrations(migration_connection)
            finally:
                await migration_connection.close()
            await apply_alembic_migrations(isolated_url)

            engine = create_async_engine(isolated_async_url, poolclass=NullPool)
            try:
                async with engine.connect() as connection:
                    version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                    assert version == "office_area_0020"
                    constraint = await connection.scalar(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = 'activity_events'::regclass "
                            "AND conname = 'activity_events_event_type_check'"
                        )
                    )
                    assert isinstance(constraint, str)
                    assert all(event_type in constraint for event_type in _PHASE_EVENT_TYPES)

                    phase_events_exist = await connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM activity_events "
                            "WHERE event_type IN ('run.working', 'run.tool_started', 'run.reviewing'))"
                        )
                    )
                    assert phase_events_exist is False

                    await connection.run_sync(alembic_downgrade)
                    await connection.commit()
                    assert await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    ) == "gateway_0018"
                    old_constraint = await connection.scalar(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = 'activity_events'::regclass "
                            "AND conname = 'activity_events_event_type_check'"
                        )
                    )
                    assert isinstance(old_constraint, str)
                    assert all(event_type not in old_constraint for event_type in _PHASE_EVENT_TYPES)

                await apply_alembic_migrations(isolated_url)
                async with engine.connect() as connection:
                    assert await connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    ) == "office_area_0020"
                    upgraded_constraint = await connection.scalar(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conrelid = 'activity_events'::regclass "
                            "AND conname = 'activity_events_event_type_check'"
                        )
                    )
                    assert isinstance(upgraded_constraint, str)
                    assert all(event_type in upgraded_constraint for event_type in _PHASE_EVENT_TYPES)
            finally:
                await engine.dispose()
        finally:
            admin_connection = await asyncpg.connect(dsn=admin_dsn, timeout=5)
            try:
                await admin_connection.execute(
                    f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
                )
            finally:
                await admin_connection.close()

    asyncio.run(verify())
