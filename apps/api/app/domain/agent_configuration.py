from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from app.domain.model_endpoints import ModelEndpointInputError, normalize_model_id, normalize_route


REGISTERED_TOOLS = frozenset({"list_files", "read_file", "write_file"})
SUPPORTED_RUNTIME_IDS = frozenset({"langchain"})


class AgentConfigurationInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AgentDefinitionConfiguration:
    role: str
    model_endpoint_id: UUID
    model_id: str
    route: str | None
    runtime_id: str
    temperature: float | None
    max_iterations: int
    concurrency_limit: int
    execution_timeout_seconds: int
    allowed_tools: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        role: str,
        model_endpoint_id: UUID,
        model_id: str,
        route: str | None = None,
        runtime_id: str = "langchain",
        temperature: float | None = None,
        max_iterations: int = 10,
        concurrency_limit: int = 1,
        execution_timeout_seconds: int = 3600,
        allowed_tools: list[str] | tuple[str, ...] = (),
    ) -> AgentDefinitionConfiguration:
        try:
            normalized_model = normalize_model_id(model_id)
            normalized_route = normalize_route(route)
        except ModelEndpointInputError as error:
            raise AgentConfigurationInputError(error.reason) from error
        normalized_role = role.strip()
        if not normalized_role or len(normalized_role) > 120:
            raise AgentConfigurationInputError("role")
        if runtime_id not in SUPPORTED_RUNTIME_IDS:
            raise AgentConfigurationInputError("runtime_id")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
            or not 0 <= float(temperature) <= 2
        ):
            raise AgentConfigurationInputError("temperature")
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or not 1 <= max_iterations <= 100:
            raise AgentConfigurationInputError("max_iterations")
        if not isinstance(concurrency_limit, int) or isinstance(concurrency_limit, bool) or not 1 <= concurrency_limit <= 20:
            raise AgentConfigurationInputError("concurrency_limit")
        if not isinstance(execution_timeout_seconds, int) or isinstance(execution_timeout_seconds, bool) or not 1 <= execution_timeout_seconds <= 86_400:
            raise AgentConfigurationInputError("execution_timeout_seconds")
        if len(allowed_tools) > len(REGISTERED_TOOLS) or any(tool not in REGISTERED_TOOLS for tool in allowed_tools):
            raise AgentConfigurationInputError("allowed_tools")
        if len(set(allowed_tools)) != len(allowed_tools):
            raise AgentConfigurationInputError("allowed_tools")
        return cls(
            role=normalized_role,
            model_endpoint_id=model_endpoint_id,
            model_id=normalized_model,
            route=normalized_route,
            runtime_id=runtime_id,
            temperature=None if temperature is None else float(temperature),
            max_iterations=max_iterations,
            concurrency_limit=concurrency_limit,
            execution_timeout_seconds=execution_timeout_seconds,
            allowed_tools=tuple(allowed_tools),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "role": self.role,
            "model_endpoint_id": str(self.model_endpoint_id),
            "model_id": self.model_id,
            "route": self.route,
            "runtime_id": self.runtime_id,
            "temperature": self.temperature,
            "max_iterations": self.max_iterations,
            "concurrency_limit": self.concurrency_limit,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "allowed_tools": list(self.allowed_tools),
        }

    @classmethod
    def from_json(cls, value: object) -> AgentDefinitionConfiguration | None:
        if not isinstance(value, dict):
            return None
        try:
            endpoint_id = UUID(str(value["model_endpoint_id"]))
            allowed_tools = value.get("allowed_tools", [])
            if not isinstance(allowed_tools, list) or any(not isinstance(item, str) for item in allowed_tools):
                return None
            model_id = value["model_id"]
            role = value["role"]
            route = value.get("route")
            runtime_id = value.get("runtime_id", "langchain")
            temperature = value.get("temperature")
            max_iterations = value.get("max_iterations")
            concurrency_limit = value.get("concurrency_limit")
            execution_timeout_seconds = value.get("execution_timeout_seconds")
            if (
                not isinstance(role, str)
                or not isinstance(model_id, str)
                or (route is not None and not isinstance(route, str))
                or not isinstance(runtime_id, str)
                or (temperature is not None and not isinstance(temperature, (int, float)))
                or not isinstance(max_iterations, int)
                or not isinstance(concurrency_limit, int)
                or not isinstance(execution_timeout_seconds, int)
            ):
                return None
            return cls.create(
                role=role,
                model_endpoint_id=endpoint_id,
                model_id=model_id,
                route=route,
                runtime_id=runtime_id,
                temperature=temperature,
                max_iterations=max_iterations,
                concurrency_limit=concurrency_limit,
                execution_timeout_seconds=execution_timeout_seconds,
                allowed_tools=allowed_tools,
            )
        except (KeyError, TypeError, ValueError, AgentConfigurationInputError):
            return None
