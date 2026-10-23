from __future__ import annotations

import asyncio
import os
import secrets
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import asyncpg
import pytest

from app.migrate import apply_migrations, ensure_application_role
from app.projects import PostgresProjectRepository


def test_postgres_migrations_and_project_owner_scope() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to an isolated PostgreSQL *_test database")

    database_name = unquote(urlsplit(database_url).path.lstrip("/"))
    if not database_name.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must target a disposable database ending in _test")

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def verify() -> None:
        connection = await asyncpg.connect(dsn=dsn, timeout=5)
        app_role_name = f"agent_office_test_{uuid4().hex}"
        app_role_password = secrets.token_urlsafe(32)
        try:
            await apply_migrations(connection)
            assert await apply_migrations(connection) == []
            await ensure_application_role(
                connection,
                app_role_name,
                app_role_password,
            )
            role = await connection.fetchrow(
                """
                SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls
                FROM pg_roles WHERE rolname = $1
                """,
                app_role_name,
            )
            assert role is not None
            assert role["rolsuper"] is False
            assert role["rolcreatedb"] is False
            assert role["rolcreaterole"] is False
            assert role["rolinherit"] is False
            assert role["rolbypassrls"] is False
            assert await connection.fetchval(
                "SELECT has_database_privilege($1, current_database(), 'CONNECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'SELECT')",
                app_role_name,
            )
            assert await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'INSERT')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'UPDATE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_table_privilege($1, 'public.projects', 'DELETE')",
                app_role_name,
            )
            assert not await connection.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')",
                app_role_name,
            )
        finally:
            role_exists = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
                app_role_name,
            )
            if role_exists:
                drop_owned = await connection.fetchval(
                    "SELECT format('DROP OWNED BY %I', $1::text)",
                    app_role_name,
                )
                await connection.execute(drop_owned)
                drop_role = await connection.fetchval(
                    "SELECT format('DROP ROLE %I', $1::text)",
                    app_role_name,
                )
                await connection.execute(drop_role)
            await connection.close()
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        owner_id = f"test-owner-{uuid4()}"
        foreign_owner_id = f"test-owner-{uuid4()}"
        repository = PostgresProjectRepository(pool)
        try:
            own = await repository.create(owner_id, "owned project", "scope test")
            foreign = await repository.create(
                foreign_owner_id,
                "foreign project",
                "must remain private",
            )
            assert [project.id for project in await repository.list_for_owner(
                owner_id, limit=10, offset=0
            )] == [own.id]
            assert await repository.get_for_owner(owner_id, own.id) == own
            assert await repository.get_for_owner(owner_id, foreign.id) is None
        finally:
            await pool.execute(
                "DELETE FROM projects WHERE owner_id = ANY($1::text[])",
                [owner_id, foreign_owner_id],
            )
            await pool.close()

    asyncio.run(verify())
