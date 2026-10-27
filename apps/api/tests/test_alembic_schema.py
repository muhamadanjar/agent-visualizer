from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel

import app.infrastructure.postgres.models  # noqa: F401
from app.infrastructure.postgres.migrate import (
    MIGRATION_DIR,
    MIGRATION_NAME,
    apply_migrations,
)


EXPECTED_APPLICATION_TABLES = {
    "projects",
    "agent_definitions",
    "agent_definition_versions",
    "skills",
    "skill_versions",
    "agent_definition_version_skills",
    "agent_instances",
    "tasks",
    "task_revisions",
    "task_revision_dependencies",
    "runs",
    "provider_configs",
    "owner_event_cursors",
    "activity_events",
    "run_activity_projections",
    "approval_requests",
    "approval_decisions",
    "artifacts",
    "office_scene_versions",
    "project_execution_policies",
    "runner_connections",
    "run_dispatches",
}


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _MigrationConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, _query: str, *_args: object) -> None:
        return None

    async def fetch(self, _query: str) -> list[dict[str, Any]]:
        return []

    async def execute(self, query: str, *args: object) -> None:
        self.statements.append((query, args))

    def transaction(self) -> _Transaction:
        return _Transaction()


def test_sqlmodel_metadata_covers_the_existing_application_schema() -> None:
    assert set(SQLModel.metadata.tables) == EXPECTED_APPLICATION_TABLES


def test_alembic_history_starts_from_the_immutable_legacy_sql_baseline() -> None:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    script = ScriptDirectory.from_config(Config(str(config_path)))

    baseline = script.get_revision("legacy_0008")
    assert baseline is not None
    assert baseline.down_revision is None
    heads = script.get_heads()
    assert heads
    for head in heads:
        ancestry = script.iterate_revisions(head, "base")
        assert any(revision.revision == baseline.revision for revision in ancestry)


def test_checksum_runner_still_applies_the_historical_raw_sql_migrations() -> None:
    connection = _MigrationConnection()

    applied = asyncio.run(apply_migrations(connection))

    migration_files = sorted(MIGRATION_DIR.glob("*.sql"))
    matches = [MIGRATION_NAME.fullmatch(path.name) for path in migration_files]
    assert all(match is not None for match in matches)
    expected_versions = sorted(
        int(match.group("version")) for match in matches if match is not None
    )
    assert set(range(1, 9)) <= set(expected_versions)
    assert applied == expected_versions
    migration_sql = [
        sql for sql, args in connection.statements
        if not args and sql.lstrip().startswith(("CREATE TABLE", "ALTER TABLE"))
    ]
    assert any("CREATE TABLE projects" in sql for sql in migration_sql)
    assert any("CREATE TABLE activity_events" in sql for sql in migration_sql)
