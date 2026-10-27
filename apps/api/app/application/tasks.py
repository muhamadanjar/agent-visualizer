from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.domain.execution import ExecutionRepository
from app.domain.instances import AgentInstanceRepository
from app.domain.projects import ProjectRepository
from app.domain.provider_config import ProviderConfigRepository
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
    """Coordinates the read-only execution preflight for a task."""

    def __init__(
        self,
        tasks: TaskRepository,
        instances: AgentInstanceRepository | None,
        providers: ProviderConfigRepository,
        execution: ExecutionRepository | None = None,
    ) -> None:
        self._tasks = tasks
        self._instances = instances
        self._providers = providers
        self._execution = execution

    async def preflight(self, owner_id: str, task_id: UUID) -> TaskPreflight | None:
        task = await self._tasks.get_for_owner(owner_id, task_id)
        if task is None:
            return None

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
                assignment = PreflightCheck(
                    "agent_assignment",
                    "blocked",
                    "The assigned Agent Instance is unavailable or archived.",
                )
            else:
                assignment = PreflightCheck(
                    "agent_assignment", "ready", "An active Agent Instance is assigned."
                )

        provider = await self._providers.get_for_owner(owner_id)
        provider_check = PreflightCheck(
            "provider_configuration",
            "ready" if provider is not None else "blocked",
            (
                "A non-secret provider endpoint/model snapshot is configured."
                if provider is not None
                else "Configure a provider endpoint and model before execution."
            ),
        )

        policy = (
            None
            if self._execution is None
            else await self._execution.get_policy_for_project(owner_id, task.project_id)
        )
        if self._execution is None:
            workspace_check = PreflightCheck(
                "workspace_policy",
                "unavailable",
                "Workspace policy storage is unavailable.",
            )
            tool_check = PreflightCheck(
                "tool_allowlist",
                "unavailable",
                "Tool allowlist storage is unavailable.",
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

        runner = (
            None
            if self._execution is None
            else await self._execution.get_ready_runner(max_age_seconds=30)
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
            credentials_check = PreflightCheck(
                "provider_credentials",
                "ready" if runner.provider_ready else "blocked",
                (
                    "Runner has the provider credential required for inference."
                    if runner.provider_ready
                    else "Set NINEROUTER_KEY in the Runner environment before executing."
                ),
            )

        checks = (
            assignment,
            provider_check,
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
