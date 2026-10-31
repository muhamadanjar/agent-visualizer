from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.agent_factory import AgentFactory, AgentFactoryError
from app.domain.agent_configuration import (
    AgentConfigurationInputError,
    AgentDefinitionConfiguration,
)
from app.domain.model_endpoints import ADAPTER, ModelEndpoint
from app.domain.registry import AgentDefinition


class MemoryDefinitions:
    def __init__(self, definition: AgentDefinition) -> None:
        self.definition = definition

    async def get_for_owner(
        self, owner_id: str, definition_id, *, version: int | None = None
    ) -> AgentDefinition | None:
        if owner_id != self.definition.owner_id or definition_id != self.definition.id:
            return None
        if version is not None and version != self.definition.version:
            return None
        return self.definition


class MemoryEndpoints:
    def __init__(self, owner_id: str, endpoint: ModelEndpoint) -> None:
        self.owner_id = owner_id
        self.endpoint = endpoint

    async def get_for_owner(self, owner_id: str, endpoint_id):
        if owner_id != self.owner_id or endpoint_id != self.endpoint.id:
            return None
        return self.endpoint


def test_agent_configuration_validates_and_round_trips_frozen_fields() -> None:
    endpoint_id = uuid4()
    configuration = AgentDefinitionConfiguration.create(
        role="  Research worker  ",
        model_endpoint_id=endpoint_id,
        model_id="openrouter/model:free",
        temperature=0.4,
        max_iterations=5,
        concurrency_limit=2,
        execution_timeout_seconds=120,
        allowed_tools=["read_file", "write_file"],
    )

    assert configuration.role == "Research worker"
    assert AgentDefinitionConfiguration.from_json(configuration.to_json()) == configuration
    assert "api_key" not in configuration.to_json()

    with pytest.raises(AgentConfigurationInputError, match="role"):
        AgentDefinitionConfiguration.create(
            role=" ", model_endpoint_id=endpoint_id, model_id="openrouter/model:free"
        )
    with pytest.raises(AgentConfigurationInputError, match="allowed_tools"):
        AgentDefinitionConfiguration.create(
            role="Research worker",
            model_endpoint_id=endpoint_id,
            model_id="openrouter/model:free",
            allowed_tools=["shell"],
        )


def test_factory_scopes_endpoint_and_intersects_tools_with_project_policy() -> None:
    owner_id = "owner"
    endpoint_id = uuid4()
    definition_id = uuid4()
    configuration = AgentDefinitionConfiguration.create(
        role="Research worker",
        model_endpoint_id=endpoint_id,
        model_id="openrouter/model:free",
        allowed_tools=["read_file", "write_file"],
    )
    definition = AgentDefinition(
        id=definition_id,
        owner_id=owner_id,
        version=3,
        name="Research worker",
        description="",
        source_markdown="# Research worker",
        unmapped_metadata={},
        configuration=configuration,
        enabled=True,
        archived_at=None,
        created_at=datetime.now(UTC),
    )
    endpoint = ModelEndpoint(
        id=endpoint_id,
        owner_id=owner_id,
        name="Local 9Router",
        adapter=ADAPTER,
        endpoint_url="http://localhost:20128/v1",
        credential_env="NINEROUTER_KEY",
        default_model_id="openrouter/model:free",
        default_route="free",
        archived_at=None,
        created_at=datetime.now(UTC),
    )
    definitions = MemoryDefinitions(definition)
    endpoints = MemoryEndpoints(owner_id, endpoint)
    factory = AgentFactory(definitions, endpoints)

    resolved = asyncio.run(factory.resolve(owner_id, definition_id, ("read_file", "list_files")))
    assert resolved.definition_version == 3
    assert resolved.effective_tools == ("read_file",)
    assert resolved.configuration.route == "free"
    assert resolved.model_endpoint.id == endpoint_id

    with pytest.raises(AgentFactoryError, match="definition"):
        asyncio.run(factory.resolve("another-owner", definition_id, ("read_file",)))

    for inactive in (
        replace(definition, enabled=False),
        replace(definition, archived_at=datetime.now(UTC)),
    ):
        definitions.definition = inactive
        with pytest.raises(AgentFactoryError, match="definition_inactive"):
            asyncio.run(factory.resolve(owner_id, definition_id, ("read_file",)))

    definitions.definition = definition
    endpoints.endpoint = replace(endpoint, archived_at=datetime.now(UTC))
    with pytest.raises(AgentFactoryError, match="model_endpoint"):
        asyncio.run(factory.resolve(owner_id, definition_id, ("read_file",)))


def test_factory_rejects_unqualified_gateway_definition_model() -> None:
    from test_model_endpoints import MemoryModelEndpointRepository
    from app.domain.model_endpoints import ModelEndpointDraft

    async def scenario() -> None:
        endpoints = MemoryModelEndpointRepository()
        endpoint = await endpoints.create("owner", ModelEndpointDraft.create(
            "Codex", "http://localhost:14000/v1", "OMP_GATEWAY_KEY", "openai-codex/example",
        ))
        configuration = AgentDefinitionConfiguration.create(
            role="Reader", model_endpoint_id=endpoint.id, model_id="openai-codex/example",
        )
        definition = AgentDefinition(
            id=uuid4(), owner_id="owner", version=1, name="Reader", description="",
            source_markdown="# Reader", unmapped_metadata={}, configuration=configuration,
            enabled=True, archived_at=None, created_at=datetime.now(UTC),
        )
        definitions = MemoryDefinitions(definition)
        factory = AgentFactory(definitions, endpoints)
        resolved = await factory.resolve("owner", definition.id, ())
        assert resolved.configuration.runtime_id == "langchain"
        assert resolved.model_endpoint.credential_env == "OMP_GATEWAY_KEY"
        for changed in (
            replace(configuration, model_id="other/model"),
            replace(configuration, route="fallback"),
        ):
            definitions.definition = replace(definition, configuration=changed)
            with pytest.raises(AgentFactoryError, match="model_endpoint"):
                await factory.resolve("owner", definition.id, ())
    asyncio.run(scenario())
