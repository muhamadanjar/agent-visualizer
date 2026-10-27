from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from typing import Any
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_POSITIONAL_BIND = re.compile(r"\$(\d+)")
_BIND_CAST = re.compile(r":(p\d+)::([a-zA-Z][a-zA-Z0-9_]*(?:\[\])?)")


class RawSQLExecutor:
    """Execute specialized PostgreSQL SQL through an existing async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _statement(
        query: str, values: Sequence[object]
    ) -> tuple[Any, dict[str, object]]:
        parameters: dict[str, object] = {}

        def replace(match: re.Match[str]) -> str:
            index = int(match.group(1))
            if index < 1 or index > len(values):
                raise ValueError("raw SQL bind parameter has no matching value")
            key = f"p{index}"
            parameters[key] = values[index - 1]
            return f":{key}"

        statement = _POSITIONAL_BIND.sub(replace, query)
        statement = _BIND_CAST.sub(r"CAST(:\1 AS \2)", statement)
        return text(statement), parameters

    @asynccontextmanager
    async def transaction(
        self, *, isolation: str | None = None, readonly: bool = False
    ) -> AsyncIterator[None]:
        async with self._session.begin():
            if isolation is not None:
                levels = {
                    "read_committed": "READ COMMITTED",
                    "repeatable_read": "REPEATABLE READ",
                    "serializable": "SERIALIZABLE",
                }
                try:
                    level = levels[isolation]
                except KeyError as error:
                    raise ValueError("unsupported transaction isolation level") from error
                await self._session.connection(
                    execution_options={"isolation_level": level}
                )
            if readonly:
                await self._session.execute(text("SET TRANSACTION READ ONLY"))
            yield

    async def fetchrow(self, query: str, *values: object):
        statement, parameters = self._statement(query, values)
        result = await self._session.execute(statement, parameters)
        return result.mappings().first()

    async def fetch(self, query: str, *values: object):
        statement, parameters = self._statement(query, values)
        result = await self._session.execute(statement, parameters)
        return result.mappings().all()

    async def fetchval(self, query: str, *values: object):
        statement, parameters = self._statement(query, values)
        result = await self._session.execute(statement, parameters)
        return result.scalar()

    async def execute(self, query: str, *values: object) -> None:
        statement, parameters = self._statement(query, values)
        await self._session.execute(statement, parameters)

    async def executemany(
        self, query: str, arguments: Sequence[Sequence[object]]
    ) -> None:
        if not arguments:
            return
        statement, _ = self._statement(query, arguments[0])
        parameters = [self._statement(query, values)[1] for values in arguments]
        await self._session.execute(statement, parameters)


class RawSQLConnectionPool:
    """Session-backed compatibility boundary for specialized raw SQL repositories."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[RawSQLExecutor]:
        async with self._sessions() as session:
            yield RawSQLExecutor(session)

    async def fetchrow(self, query: str, *values: object):
        async with self.acquire() as connection:
            return await connection.fetchrow(query, *values)

    async def fetch(self, query: str, *values: object):
        async with self.acquire() as connection:
            return await connection.fetch(query, *values)

    async def fetchval(self, query: str, *values: object):
        async with self.acquire() as connection:
            return await connection.fetchval(query, *values)

    async def execute(self, query: str, *values: object) -> None:
        async with self._sessions.begin() as session:
            await RawSQLExecutor(session).execute(query, *values)

    async def executemany(
        self, query: str, arguments: Sequence[Sequence[object]]
    ) -> None:
        async with self._sessions.begin() as session:
            await RawSQLExecutor(session).executemany(query, arguments)
