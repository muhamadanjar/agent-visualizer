from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.model_endpoints import ADAPTER, ModelEndpoint, ModelEndpointDraft
from app.infrastructure.postgres.models import ModelEndpointTable


def _endpoint(row: ModelEndpointTable) -> ModelEndpoint:
    return ModelEndpoint(
        id=row.id,
        owner_id=row.owner_id,
        name=row.name,
        adapter=row.adapter,
        endpoint_url=row.endpoint_url,
        credential_env=row.credential_env,
        default_model_id=row.default_model_id,
        default_route=row.default_route,
        archived_at=row.archived_at,
        created_at=row.created_at,
    )


class PostgresModelEndpointRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, owner_id: str, draft: ModelEndpointDraft) -> ModelEndpoint:
        row = ModelEndpointTable(
            id=uuid4(),
            owner_id=owner_id,
            name=draft.name,
            adapter=ADAPTER,
            endpoint_url=draft.endpoint_url,
            credential_env=draft.credential_env,
            default_model_id=draft.default_model_id,
            default_route=draft.default_route,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            return _endpoint(row)

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ) -> list[ModelEndpoint]:
        statement = (
            select(ModelEndpointTable)
            .where(ModelEndpointTable.owner_id == owner_id)
            .order_by(ModelEndpointTable.created_at, ModelEndpointTable.id)
            .limit(limit)
            .offset(offset)
        )
        if not include_archived:
            statement = statement.where(ModelEndpointTable.archived_at.is_(None))
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
            return [_endpoint(row) for row in rows]

    async def get_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ModelEndpointTable).where(
                    ModelEndpointTable.owner_id == owner_id,
                    ModelEndpointTable.id == endpoint_id,
                )
            )
            return None if row is None else _endpoint(row)

    async def archive_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(ModelEndpointTable)
                .where(
                    ModelEndpointTable.owner_id == owner_id,
                    ModelEndpointTable.id == endpoint_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.archived_at is None:
                row.archived_at = func.now()
                await session.flush()
                await session.refresh(row, attribute_names=["archived_at"])
            return _endpoint(row)
