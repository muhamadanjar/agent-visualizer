from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.application.agent_factory import AgentFactory, AgentFactoryError
from app.domain.execution import ExecutionRepository
from app.domain.instances import AgentInstanceRepository
from app.domain.model_endpoints import ModelEndpointRepository
from app.domain.projects import ProjectRepository
from app.domain.registry import AgentRegistryRepository
from app.domain.tasks import TaskInputError, TaskRepository, TaskSpec


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    key: str
    status: Literal["ready", "blocked", "unavailable"]
    detail: str


@dataclass(frozen=True, slots=True)
class TaskPreflight:
    task_id: UUID
    ready: bool
    checks: tuple[PreflightCheck, ...]


class TaskWorkflow:
    """Coordinates read-only Agent Factory and workspace preflight for a Task."""

    def __init__(
        self,
        tasks: TaskRepository,
        instances: AgentInstanceRepository | None,
        definitions: AgentRegistryRepository | None,
        endpoints: ModelEndpointRepository | None,
        execution: ExecutionRepository | None = None,
    ) -> None:
        self._tasks = tasks
        self._instances = instances
        self._factory = (
            None
            if definitions is None or endpoints is None
            else AgentFactory(definitions, endpoints)
        )
        self._execution = execution

    async def preflight(self, owner_id: str, task_id: UUID) -> TaskPreflight | None:
        task = await self._tasks.get_for_owner(owner_id, task_id)
        if task is None:
            return None

        instance = None
        if task.agent_instance_id is None:
            assignment = PreflightCheck(
                "agent_assignment",
                "blocked",
                "Assign an active Agent Instance before creating a Run.",
            )
        elif self._instances is None:
            assignment = PreflightCheck(
                "agent_assignment",
                "unavailable",
                "Agent Instance storage is unavailable; assignment activity cannot be checked.",
            )
        else:
            instance = await self._instances.get_for_owner(owner_id, task.agent_instance_id)
            if instance is None or instance.archived_at is not None:
                instance = None
                assignment = PreflightCheck(
                    "agent_assignment",
                    "blocked",
                    "The assigned Agent Instance is unavailable or archived.",
                )
            else:
                assignment = PreflightCheck(
                    "agent_assignment", "ready", "An active Agent Instance is assigned."
                )

        policy = (
            None
            if self._execution is None
            else await self._execution.get_policy_for_project(owner_id, task.project_id)
        )
        if self._execution is None:
            workspace_check = PreflightCheck(
                "workspace_policy", "unavailable", "Workspace policy storage is unavailable."
            )
            tool_check = PreflightCheck(
                "tool_allowlist", "unavailable", "Tool allowlist storage is unavailable."
            )
        elif policy is None:
            workspace_check = PreflightCheck(
                "workspace_policy",
                "blocked",
                "Configure a project-relative workspace path and enable its execution policy.",
            )
            tool_check = PreflightCheck(
                "tool_allowlist",
                "blocked",
                "Select at least one supported workspace tool in the project policy.",
            )
        else:
            workspace_check = PreflightCheck(
                "workspace_policy",
                "ready" if policy.enabled else "blocked",
                (
                    f"Project workspace '{policy.workspace_path}' is configured."
                    if policy.enabled
                    else "Project execution policy is disabled."
                ),
            )
            tool_check = PreflightCheck(
                "tool_allowlist",
                "ready" if policy.allowed_tools else "blocked",
                (
                    "Enabled tools: " + ", ".join(policy.allowed_tools) + "."
                    if policy.allowed_tools
                    else "Select at least one supported workspace tool."
                ),
            )

        resolved = None
        if self._factory is None:
            model_check = PreflightCheck(
                "model_endpoint",
                "unavailable",
                "Agent Factory or Model Endpoint storage is unavailable.",
            )
        elif instance is None:
            model_check = PreflightCheck(
                "model_endpoint", "blocked", "An active Agent Instance is required."
            )
        else:
            try:
                resolved = await self._factory.resolve(
                    owner_id,
                    instance.definition_id,
                    () if policy is None else policy.allowed_tools,
                )
            except AgentFactoryError as error:
                detail = {
                    "definition_inactive": "The Agent Definition is disabled or archived.",
                    "configuration": "Configure a valid Agent Definition Version.",
                    "model_endpoint": "Select an active Model Endpoint owned by this account.",
                    "runtime": "The selected runtime is not registered.",
                    "unknown_tool": "The Agent Definition selects an unregistered tool.",
                    "unknown_project_tool": "The project policy contains an unregistered tool.",
                }.get(error.reason, "Agent Factory configuration validation failed.")
                model_check = PreflightCheck("model_endpoint", "blocked", detail)
            else:
                model_check = PreflightCheck(
                    "model_endpoint",
                    "ready",
                    "The active Agent Definition Version and Model Endpoint are registered.",
                )
                if policy is not None and policy.allowed_tools:
                    if (
                        resolved.configuration.allowed_tools
                        and not resolved.effective_tools
                    ):
                        tool_check = PreflightCheck(
                            "tool_allowlist",
                            "blocked",
                            "No selected Agent Definition tools are permitted by the project policy.",
                        )
                    elif resolved.effective_tools:
                        tool_check = PreflightCheck(
                            "tool_allowlist",
                            "ready",
                            "Effective tools: " + ", ".join(resolved.effective_tools) + ".",
                        )

        credential_env = "NINEROUTER_KEY" if resolved is None else resolved.model_endpoint.credential_env
        runner = (
            None
            if self._execution is None
            else await self._execution.get_ready_runner(max_age_seconds=30, credential_env=credential_env)
        )
        if self._execution is None or runner is None:
            runner_check = PreflightCheck(
                "runner_connection",
                "unavailable",
                "No authenticated Runner heartbeat was received in the last 30 seconds.",
            )
            credentials_check = PreflightCheck(
                "provider_credentials",
                "unavailable",
                "Runner provider credential availability cannot be checked.",
            )
        else:
            runner_check = PreflightCheck(
                "runner_connection", "ready", "An authenticated Runner heartbeat is current."
            )
            provider_ready = runner.gateway_ready if credential_env == "OMP_GATEWAY_KEY" else runner.provider_ready
            credentials_check = PreflightCheck(
                "provider_credentials",
                "ready" if provider_ready else "blocked",
                (
                    "Runner reports readiness for the selected provider; upstream acceptance is not verified."
                    if provider_ready
                    else "The selected provider is not ready. Check its server-side configuration."
                ),
            )

        checks = (
            assignment,
            model_check,
            workspace_check,
            tool_check,
            runner_check,
            credentials_check,
        )
        return TaskPreflight(
            task_id=task_id,
            ready=all(check.status == "ready" for check in checks),
            checks=checks,
        )


async def validate_task_references(
    owner_id: str,
    spec: TaskSpec,
    projects: ProjectRepository,
    instances: AgentInstanceRepository | None,
) -> None:
    if await projects.get_for_owner(owner_id, spec.project_id) is None:
        raise TaskInputError("reference")
    if spec.agent_instance_id is None:
        return
    if instances is None:
        raise TaskInputError("instance_storage")
    instance = await instances.get_for_owner(owner_id, spec.agent_instance_id)
    if instance is None or instance.archived_at is not None:
        raise TaskInputError("reference")
