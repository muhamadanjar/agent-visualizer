from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import create_app
from app.domain.provider_config import ProviderConfig, ProviderConfigDraft
from test_api import ORIGIN, database_ready, login, settings


class MemoryProviderConfigRepository:
    def __init__(self) -> None:
        self.configs: dict[str, ProviderConfig] = {}

    async def get_for_owner(self, owner_id: str) -> ProviderConfig | None:
        return self.configs.get(owner_id)

    async def upsert_for_owner(
        self, owner_id: str, draft: ProviderConfigDraft
    ) -> ProviderConfig:
        current = self.configs.get(owner_id)
        config = ProviderConfig(
            owner_id=owner_id,
            version=1 if current is None else current.version + 1,
            adapter="openai_compatible_chat_completions",
            endpoint_url=draft.endpoint_url,
            model_id=draft.model_id,
            route=draft.route,
            updated_at=datetime.now(UTC),
        )
        self.configs[owner_id] = config
        return config


def test_provider_config_is_owner_authenticated_validated_and_secret_free() -> None:
    repository = MemoryProviderConfigRepository()
    client = TestClient(
        create_app(
            settings(),
            database_check=database_ready,
            provider_config_repository=repository,
        )
    )

    with client:
        assert client.get("/api/v1/provider-config").status_code == 401
        assert login(client).status_code == 200
        assert client.get("/api/v1/provider-config").status_code == 404

        invalid_urls = [
            "http://example.com/v1",
            "http://localhost:9999/v1",
            "http://169.254.169.254/latest/meta-data",
            "https://user:password@example.com/v1",
            "https://api.example.com/v1?key=secret",
            "https://-evil.example/v1",
            "https://api..example/v1",
            "https://169.254.169.999/v1",
        ]
        for endpoint_url in invalid_urls:
            response = client.put(
                "/api/v1/provider-config",
                json={
                    "endpoint_url": endpoint_url,
                    "model_id": "openrouter/cohere/north-mini-code:free",
                },
                headers={"origin": ORIGIN},
            )
            assert response.status_code == 422

        configured = client.put(
            "/api/v1/provider-config",
            json={
                "endpoint_url": "http://localhost:20128/v1/",
                "model_id": "openrouter/cohere/north-mini-code:free",
                "route": "free",
            },
            headers={"origin": ORIGIN},
        )
        assert configured.status_code == 200
        saved = configured.json()
        assert saved["adapter"] == "openai_compatible_chat_completions"
        assert saved["endpoint_url"] == "http://localhost:20128/v1"
        assert saved["model_id"] == "openrouter/cohere/north-mini-code:free"
        assert saved["route"] == "free"
        assert saved["version"] == 1
        assert "api_key" not in saved
        assert "secret" not in saved

        updated = client.put(
            "/api/v1/provider-config",
            json={
                "endpoint_url": "http://localhost:20128/v1",
                "model_id": "openrouter/another-model:free",
            },
            headers={"origin": ORIGIN},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated.json()["route"] is None
        assert client.get("/api/v1/provider-config").json() == updated.json()

        extra_secret = client.put(
            "/api/v1/provider-config",
            json={
                "endpoint_url": "http://localhost:20128/v1",
                "model_id": "openrouter/another-model:free",
                "api_key": "must-not-be-accepted",
            },
            headers={"origin": ORIGIN},
        )
        assert extra_secret.status_code == 422
        assert "must-not-be-accepted" not in extra_secret.text
