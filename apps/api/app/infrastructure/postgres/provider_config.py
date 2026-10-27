from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.provider_config import ADAPTER, ProviderConfig, ProviderConfigDraft
from app.infrastructure.postgres.models import ProviderConfigTable


def _provider_config(row: ProviderConfigTable) -> ProviderConfig:
    return ProviderConfig(
        owner_id=row.owner_id,
        version=row.version,
        adapter=row.adapter,
        endpoint_url=row.endpoint_url,
        model_id=row.model_id,
        route=row.route,
        updated_at=row.updated_at,
    )


class PostgresProviderConfigRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_for_owner(self, owner_id: str) -> ProviderConfig | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProviderConfigTable).where(ProviderConfigTable.owner_id == owner_id)
            )
            return None if row is None else _provider_config(row)

    async def upsert_for_owner(
        self, owner_id: str, draft: ProviderConfigDraft
    ) -> ProviderConfig:
        statement = (
            insert(ProviderConfigTable)
            .values(
                owner_id=owner_id,
                version=1,
                adapter=ADAPTER,
                endpoint_url=draft.endpoint_url,
                model_id=draft.model_id,
                route=draft.route,
            )
            .on_conflict_do_update(
                index_elements=[ProviderConfigTable.owner_id],
                set_={
                    "version": ProviderConfigTable.version + 1,
                    "adapter": ADAPTER,
                    "endpoint_url": draft.endpoint_url,
                    "model_id": draft.model_id,
                    "route": draft.route,
                    "updated_at": func.now(),
                },
            )
            .returning(ProviderConfigTable)
        )
        async with self._sessions.begin() as session:
            row = (await session.execute(statement)).scalar_one()
        return _provider_config(row)
