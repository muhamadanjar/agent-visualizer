from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

import asyncpg

MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_NAME = re.compile(r"(?P<version>[0-9]{4})_[a-z0-9_]+\.sql\Z")
MIGRATION_LOCK_KEY = 6_734_501_998_711_821_229


async def apply_migrations(connection: asyncpg.Connection) -> list[int]:
    """Apply ordered SQL migrations once, serializing concurrent deploys."""
    await connection.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_KEY)
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_office_schema_migrations (
                version integer PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            int(row["version"]): str(row["checksum"])
            for row in await connection.fetch(
                "SELECT version, checksum FROM agent_office_schema_migrations"
            )
        }
        files: list[tuple[int, Path, str]] = []
        for path in sorted(MIGRATION_DIR.glob("*.sql")):
            match = MIGRATION_NAME.fullmatch(path.name)
            if match is None:
                raise RuntimeError(f"invalid migration filename: {path.name}")
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            files.append((int(match.group("version")), path, checksum))

        versions = [version for version, _, _ in files]
        if len(versions) != len(set(versions)):
            raise RuntimeError("duplicate migration version")
        unknown_versions = applied.keys() - set(versions)
        if unknown_versions:
            raise RuntimeError("database contains migration versions absent from this image")

        completed: list[int] = []
        for version, path, checksum in files:
            previous = applied.get(version)
            if previous is not None:
                if previous != checksum:
                    raise RuntimeError(f"applied migration {version} checksum changed")
                continue
            async with connection.transaction():
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute(
                    """
                    INSERT INTO agent_office_schema_migrations (version, checksum)
                    VALUES ($1, $2)
                    """,
                    version,
                    checksum,
                )
            completed.append(version)
        return completed
    finally:
        await connection.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY)


APP_ROLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")


async def ensure_application_role(
    connection: asyncpg.Connection,
    role_name: str,
    role_password: str,
) -> None:
    """Keep distinct database grants for mutable and immutable application records."""
    if not APP_ROLE_NAME.fullmatch(role_name) or len(role_password) < 32:
        raise RuntimeError("application database role configuration is invalid")

    await connection.fetchval("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_KEY)
    try:
        current_user = await connection.fetchval("SELECT current_user")
        if role_name == current_user:
            raise RuntimeError(
                "application database role must differ from migration administrator"
            )

        statement = await connection.fetchval(
            """
            SELECT format(
                'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
                $1::text,
                $2::text
            )
            """,
            role_name,
            role_password,
        )
        exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
            role_name,
        )
        if exists:
            await connection.execute(statement)
        else:
            await connection.execute(statement.replace("ALTER ROLE", "CREATE ROLE", 1))

        memberships = await connection.fetch(
            """
            SELECT granted.rolname
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = $1
            """,
            role_name,
        )
        for membership in memberships:
            revoke_membership = await connection.fetchval(
                "SELECT format('REVOKE %I FROM %I', $1::text, $2::text)",
                membership["rolname"],
                role_name,
            )
            await connection.execute(revoke_membership)
        owns_application_objects = await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_database
                WHERE datname = current_database()
                  AND datdba = (SELECT oid FROM pg_roles WHERE rolname = $1)
                UNION ALL
                SELECT 1 FROM pg_namespace
                WHERE nspname = 'public'
                  AND nspowner = (SELECT oid FROM pg_roles WHERE rolname = $1)
                UNION ALL
                SELECT 1 FROM pg_class
                WHERE relname = ANY(ARRAY[
                    'projects', 'agent_definitions', 'agent_definition_versions',
                    'skills', 'skill_versions', 'agent_definition_version_skills',
                    'agent_instances', 'tasks', 'task_revisions',
                    'task_revision_dependencies'
                ]::text[])
                  AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
                  AND relowner = (SELECT oid FROM pg_roles WHERE rolname = $1)
            )
            """,
            role_name,
        )
        if owns_application_objects:
            raise RuntimeError(
                "application database role must not own the database, schema, or application tables"
            )


        database_name = await connection.fetchval("SELECT current_database()")
        revoke_public_create = "REVOKE CREATE ON SCHEMA public FROM PUBLIC"
        revoke_app_schema = await connection.fetchval(
            "SELECT format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', $1::text)",
            role_name,
        )
        revoke_app_create = await connection.fetchval(
            "SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', $1::text)",
            role_name,
        )
        revoke_app_tables = await connection.fetchval(
            "SELECT format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', $1::text)",
            role_name,
        )
        revoke_app_sequences = await connection.fetchval(
            "SELECT format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', $1::text)",
            role_name,
        )
        grant_connect = await connection.fetchval(
            "SELECT format('GRANT CONNECT ON DATABASE %I TO %I', $1::text, $2::text)",
            database_name,
            role_name,
        )
        grant_schema = await connection.fetchval(
            "SELECT format('GRANT USAGE ON SCHEMA public TO %I', $1::text)",
            role_name,
        )
        grant_projects = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE projects TO %I', $1::text)",
            role_name,
        )
        grant_definitions = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT, UPDATE ON TABLE agent_definitions TO %I', $1::text)",
            role_name,
        )
        grant_definition_versions = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE agent_definition_versions TO %I', $1::text)",
            role_name,
        )
        grant_skills = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT, UPDATE ON TABLE skills TO %I', $1::text)",
            role_name,
        )
        grant_skill_versions = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE skill_versions TO %I', $1::text)",
            role_name,
        )
        grant_definition_skills = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE agent_definition_version_skills TO %I', $1::text)",
            role_name,
        )
        grant_agent_instances = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT, UPDATE ON TABLE agent_instances TO %I', $1::text)",
            role_name,
        )
        grant_tasks = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT, UPDATE ON TABLE tasks TO %I', $1::text)",
            role_name,
        )
        grant_task_revisions = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE task_revisions TO %I', $1::text)",
            role_name,
        )
        grant_task_dependencies = await connection.fetchval(
            "SELECT format('GRANT SELECT, INSERT ON TABLE task_revision_dependencies TO %I', $1::text)",
            role_name,
        )
        for statement in (
            revoke_public_create,
            revoke_app_schema,
            revoke_app_create,
            revoke_app_tables,
            revoke_app_sequences,
            grant_connect,
            grant_schema,
            grant_projects,
            grant_definitions,
            grant_skills,
            grant_skill_versions,
            grant_definition_skills,
            grant_definition_versions,
            grant_agent_instances,
            grant_tasks,
            grant_task_revisions,
            grant_task_dependencies,
        ):
            await connection.execute(statement)
    finally:
        await connection.fetchval("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_KEY)

async def migrate(
    migration_database_url: str,
    app_role_name: str,
    app_role_password: str,
) -> list[int]:
    dsn = migration_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn=dsn, timeout=5)
    try:
        versions = await apply_migrations(connection)
        await ensure_application_role(connection, app_role_name, app_role_password)
        return versions
    finally:
        await connection.close()


def main() -> None:
    migration_database_url = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    app_role_name = os.environ.get("APP_DB_USER", "").strip()
    app_role_password = os.environ.get("APP_DB_PASSWORD", "")
    if not migration_database_url or not app_role_name or not app_role_password:
        raise SystemExit(
            "MIGRATION_DATABASE_URL, APP_DB_USER, and APP_DB_PASSWORD are required"
        )
    versions = asyncio.run(
        migrate(migration_database_url, app_role_name, app_role_password)
    )
    rendered = ", ".join(str(version) for version in versions) or "none"
    print(f"database migrations ready; newly applied versions: {rendered}")


if __name__ == "__main__":
    main()
