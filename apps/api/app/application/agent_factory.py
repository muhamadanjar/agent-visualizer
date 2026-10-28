from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from app.domain.agent_configuration import AgentDefinitionConfiguration, REGISTERED_TOOLS
from app.domain.model_endpoints import ModelEndpoint, ModelEndpointRepository
from app.domain.registry import AgentDefinition, AgentRegistryRepository


class AgentFactoryError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfiguration:
    definition_id: UUID
    definition_version: int
    model_endpoint: ModelEndpoint
    configuration: AgentDefinitionConfiguration
    effective_tools: tuple[str, ...]


class AgentFactory:
    """Resolves one immutable definition version into a validated Run config."""

    def __init__(
        self,
        definitions: AgentRegistryRepository,
        endpoints: ModelEndpointRepository,
    ) -> None:
        self._definitions = definitions
        self._endpoints = endpoints

    async def resolve(
        self,
        owner_id: str,
        definition_id: UUID,
        project_tools: tuple[str, ...],
        *,
        version: int | None = None,
    ) -> ResolvedAgentConfiguration:
        definition = await self._definitions.get_for_owner(
            owner_id, definition_id, version=version
        )
        if definition is None:
            raise AgentFactoryError("definition")
        if definition.archived_at is not None or not definition.enabled:
            raise AgentFactoryError("definition_inactive")
        configuration = definition.configuration
        if configuration is None:
            raise AgentFactoryError("configuration")
        endpoint = await self._endpoints.get_for_owner(
            owner_id, configuration.model_endpoint_id
        )
        if endpoint is None or endpoint.archived_at is not None:
            raise AgentFactoryError("model_endpoint")
        if configuration.route is None and endpoint.default_route is not None:
            configuration = replace(configuration, route=endpoint.default_route)
        if configuration.runtime_id != "langchain":
            raise AgentFactoryError("runtime")
        if any(tool not in REGISTERED_TOOLS for tool in configuration.allowed_tools):
            raise AgentFactoryError("unknown_tool")
        if any(tool not in REGISTERED_TOOLS for tool in project_tools):
            raise AgentFactoryError("unknown_project_tool")
        project_tool_set = set(project_tools)
        effective_tools = tuple(
            tool for tool in configuration.allowed_tools if tool in project_tool_set
        )
        return ResolvedAgentConfiguration(
            definition_id=definition.id,
            definition_version=definition.version,
            model_endpoint=endpoint,
            configuration=configuration,
            effective_tools=effective_tools,
        )
