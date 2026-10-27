from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.domain.common import NameConflictError
from app.infrastructure.postgres.errors import translate_unique_violation

def test_raw_sql_executor_translates_positional_binds_with_postgres_casts() -> None:
    from app.infrastructure.postgres.raw_sql import RawSQLExecutor

    statement, parameters = RawSQLExecutor._statement(
        "SELECT unnest($2::uuid[]) WHERE owner_id = $1::text",
        ("owner", ["123e4567-e89b-12d3-a456-426614174000"]),
    )

    assert str(statement) == (
        "SELECT unnest(CAST(:p2 AS uuid[])) WHERE owner_id = CAST(:p1 AS text)"
    )
    assert parameters == {
        "p1": "owner",
        "p2": ["123e4567-e89b-12d3-a456-426614174000"],
    }


def test_unique_constraint_failure_becomes_domain_name_conflict() -> None:
    @translate_unique_violation
    async def insert_name() -> None:
        raise asyncpg.UniqueViolationError("duplicate name")

    with pytest.raises(NameConflictError) as error:
        asyncio.run(insert_name())

    assert isinstance(error.value.__cause__, asyncpg.UniqueViolationError)

def test_sqlalchemy_unique_constraint_failure_becomes_domain_name_conflict() -> None:
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError

    @translate_unique_violation
    async def insert_name() -> None:
        raise IntegrityError("INSERT", {}, SimpleNamespace(sqlstate="23505"))

    with pytest.raises(NameConflictError) as error:
        asyncio.run(insert_name())

    assert isinstance(error.value.__cause__, IntegrityError)
