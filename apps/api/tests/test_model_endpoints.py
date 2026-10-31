from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.model_endpoints import ADAPTER, ModelEndpoint, ModelEndpointDraft, ModelEndpointInputError
from app.main import create_app
from test_api import ORIGIN, database_ready, login, settings


class MemoryModelEndpointRepository:
    def __init__(self) -> None:
        self.endpoints: dict[tuple[str, UUID], ModelEndpoint] = {}

    async def create(self, owner_id: str, draft: ModelEndpointDraft) -> ModelEndpoint:
        endpoint = ModelEndpoint(
            id=uuid4(),
            owner_id=owner_id,
            name=draft.name,
            adapter=ADAPTER,
            endpoint_url=draft.endpoint_url,
            credential_env=draft.credential_env,
            default_model_id=draft.default_model_id,
            default_route=draft.default_route,
            archived_at=None,
            created_at=datetime.now(UTC),
        )
        self.endpoints[(owner_id, endpoint.id)] = endpoint
        return endpoint

    async def list_for_owner(
        self, owner_id: str, *, include_archived: bool, limit: int, offset: int
    ) -> list[ModelEndpoint]:
        records = [
            endpoint
            for (record_owner, _), endpoint in self.endpoints.items()
            if record_owner == owner_id and (include_archived or endpoint.archived_at is None)
        ]
        records.sort(key=lambda endpoint: (endpoint.created_at, str(endpoint.id)))
        return records[offset : offset + limit]

    async def get_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None:
        return self.endpoints.get((owner_id, endpoint_id))

    async def archive_for_owner(self, owner_id: str, endpoint_id: UUID) -> ModelEndpoint | None:
        endpoint = await self.get_for_owner(owner_id, endpoint_id)
        if endpoint is None:
            return None
        archived = replace(endpoint, archived_at=endpoint.archived_at or datetime.now(UTC))
        self.endpoints[(owner_id, endpoint_id)] = archived
        return archived


def test_model_endpoints_are_owner_authenticated_immutable_and_secret_free() -> None:
    repository = MemoryModelEndpointRepository()
    client = TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            model_endpoint_repository=repository,
        )
    )

    with client:
        assert client.get("/api/v1/model-endpoints").status_code == 401
        assert login(client).status_code == 200

        invalid = client.post(
            "/api/v1/model-endpoints",
            json={
                "name": "Unsafe endpoint",
                "endpoint_url": "http://169.254.169.254/latest/meta-data",
                "credential_env": "NINEROUTER_KEY",
                "default_model_id": "openrouter/model:free",
            },
            headers={"origin": ORIGIN},
        )
        assert invalid.status_code == 422

        created = client.post(
            "/api/v1/model-endpoints",
            json={
                "name": "Local 9Router",
                "endpoint_url": "http://localhost:20128/v1",
                "credential_env": "NINEROUTER_KEY",
                "default_model_id": "openrouter/cohere/north-mini-code:free",
                "default_route": "free",
            },
            headers={"origin": ORIGIN},
        )
        assert created.status_code == 201
        saved = created.json()
        assert saved["credential_env"] == "NINEROUTER_KEY"
        assert "api_key" not in saved and "secret" not in saved
        assert saved["default_route"] == "free"

        second = client.post(
            "/api/v1/model-endpoints",
            json={
                "name": "OpenRouter direct",
                "endpoint_url": "https://openrouter.ai/api/v1",
                "credential_env": "NINEROUTER_KEY",
                "default_model_id": "openrouter/model:free",
            },
            headers={"origin": ORIGIN},
        )
        assert second.status_code == 201
        assert second.json()["id"] != saved["id"]

        archived = client.post(
            f"/api/v1/model-endpoints/{saved['id']}/archive",
            headers={"origin": ORIGIN},
        )
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        active = client.get("/api/v1/model-endpoints").json()
        assert [item["id"] for item in active] == [second.json()["id"]]
        all_endpoints = client.get("/api/v1/model-endpoints?include_archived=true").json()
        assert {item["id"] for item in all_endpoints} == {saved["id"], second.json()["id"]}
        assert client.post(
            f"/api/v1/model-endpoints/{uuid4()}/archive",
            headers={"origin": ORIGIN},
        ).status_code == 404


def test_gateway_binding_is_explicit_and_has_no_route_override() -> None:
    draft = ModelEndpointDraft.create(
        "Codex Subscription", "http://localhost:14000/v1", "OMP_GATEWAY_KEY",
        "openai-codex/gpt-5.1-codex",
    )
    assert draft.default_model_id == "openai-codex/gpt-5.1-codex"
    assert draft.default_route is None


@pytest.mark.parametrize(("endpoint", "model", "route"), [
    ("http://localhost:20128/v1", "openai-codex/example", None),
    ("https://example.com/v1", "openai-codex/example", None),
    ("http://127.0.0.1:14000/v1", "openai-codex/example", None),
    ("http://localhost:14000/v1", "example", None),
    ("http://localhost:14000/v1", "openai/example", None),
    ("http://localhost:14000/v1", "openai-codex/", None),
    ("http://localhost:14000/v1", "openai-codex/example/other", None),
    ("http://localhost:14000/v1", "openai-codex/example", "fallback"),
])
def test_gateway_rejects_other_endpoints_models_and_routes(endpoint, model, route) -> None:
    with pytest.raises(ModelEndpointInputError):
        ModelEndpointDraft.create("Codex", endpoint, "OMP_GATEWAY_KEY", model, route)


def test_gateway_endpoint_creation_is_authenticated_and_owner_scoped() -> None:
    repository = MemoryModelEndpointRepository()
    payload = {
        "name": "Codex Subscription",
        "endpoint_url": "http://localhost:14000/v1",
        "credential_env": "OMP_GATEWAY_KEY",
        "default_model_id": "openai-codex/example",
    }
    with TestClient(create_app(
        settings(), database_check=database_ready, model_endpoint_repository=repository,
    )) as client:
        assert client.post("/api/v1/model-endpoints", json=payload, headers={"origin": ORIGIN}).status_code == 401
        assert login(client).status_code == 200
        response = client.post("/api/v1/model-endpoints", json=payload, headers={"origin": ORIGIN})
        assert response.status_code == 201
        saved = response.json()
        assert saved["credential_env"] == "OMP_GATEWAY_KEY"
        assert repository.endpoints[("owner", UUID(saved["id"]))].endpoint_url == payload["endpoint_url"]
        assert all(owner == "owner" for owner, _ in repository.endpoints)
        denied = client.post("/api/v1/model-endpoints", json={**payload, "default_model_id": "other/model"}, headers={"origin": ORIGIN})
        assert denied.status_code == 422
