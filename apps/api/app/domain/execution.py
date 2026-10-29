from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


ALLOWED_WORKSPACE_TOOLS = frozenset({"list_files", "read_file", "write_file"})
_WORKSPACE_SEGMENT_RE = re.compile(r"[^/\\\x00-\x1f\x7f]{1,255}\Z")


class ExecutionPolicyInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _normalize_workspace_path(value: str) -> str:
    path = value.strip()
    if (
        not path
        or len(path) > 512
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
    ):
        raise ExecutionPolicyInputError("workspace_path")

    segments = path.split("/")
    if any(
        segment in {"", ".", ".."}
        or not _WORKSPACE_SEGMENT_RE.fullmatch(segment)
        for segment in segments
    ):
        raise ExecutionPolicyInputError("workspace_path")
    return "/".join(segments)


@dataclass(frozen=True, slots=True)
class WorkspacePolicyDraft:
    workspace_path: str
    allowed_tools: tuple[str, ...]
    max_duration_seconds: int
    max_inference_turns: int
    max_tool_calls: int
    enabled: bool = True

    @classmethod
    def create(
        cls,
        *,
        workspace_path: str,
        allowed_tools: tuple[str, ...] | list[str],
        max_duration_seconds: int,
        max_inference_turns: int,
        max_tool_calls: int,
        enabled: bool = True,
    ) -> WorkspacePolicyDraft:
        normalized_path = _normalize_workspace_path(workspace_path)
        tools = tuple(allowed_tools)
        if (
            not tools
            or len(set(tools)) != len(tools)
            or any(tool not in ALLOWED_WORKSPACE_TOOLS for tool in tools)
        ):
            raise ExecutionPolicyInputError("allowed_tools")
        if not 1 <= max_duration_seconds <= 3600:
            raise ExecutionPolicyInputError("max_duration_seconds")
        if not 1 <= max_inference_turns <= 24:
            raise ExecutionPolicyInputError("max_inference_turns")
        if not 1 <= max_tool_calls <= 50:
            raise ExecutionPolicyInputError("max_tool_calls")
        if not isinstance(enabled, bool):
            raise ExecutionPolicyInputError("enabled")
        return cls(
            workspace_path=normalized_path,
            allowed_tools=tools,
            max_duration_seconds=max_duration_seconds,
            max_inference_turns=max_inference_turns,
            max_tool_calls=max_tool_calls,
            enabled=enabled,
        )


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    owner_id: str
    project_id: UUID
    workspace_path: str
    allowed_tools: tuple[str, ...]
    max_duration_seconds: int
    max_inference_turns: int
    max_tool_calls: int
    enabled: bool
    revision: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    runner_id: UUID
    last_seen_at: datetime
    provider_ready: bool


@dataclass(frozen=True, slots=True)
class RunExecutionJob:
    owner_id: str
    run_id: UUID
    task_id: UUID
    project_id: UUID
    instance_id: UUID
    runner_id: UUID
    configuration_snapshot: dict[str, object]
    policy: WorkspacePolicy
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RunnerRunContext:
    owner_id: str
    run_id: UUID
    task_id: UUID
    project_id: UUID
    instance_id: UUID
    task_revision: int


@dataclass(frozen=True, slots=True)
class RunnerLeaseStatus:
    run_id: UUID
    runner_id: UUID | None
    state: str
    active_lease: bool
    lease_expires_at: datetime | None
    max_duration_seconds: int | None
    cancellation_requested: bool



class ExecutionRepository(Protocol):
    async def get_policy_for_project(
        self, owner_id: str, project_id: UUID
    ) -> WorkspacePolicy | None: ...

    async def save_policy(
        self, owner_id: str, project_id: UUID, draft: WorkspacePolicyDraft
    ) -> WorkspacePolicy | None: ...

    async def get_ready_runner(self, *, max_age_seconds: int) -> RunnerStatus | None: ...
    async def get_runner_status(
        self, runner_id: UUID, *, max_age_seconds: int
    ) -> RunnerStatus | None: ...

    async def register_runner(self, runner_id: UUID, *, provider_ready: bool) -> None: ...


    async def claim_next(
        self, runner_id: UUID, *, lease_seconds: int
    ) -> RunExecutionJob | None: ...

    async def heartbeat_run(self, runner_id: UUID, run_id: UUID, *, lease_seconds: int) -> bool: ...

    async def runner_owns_run(self, runner_id: UUID, run_id: UUID) -> bool: ...
    async def get_run_context_for_runner(
        self, runner_id: UUID, run_id: UUID
    ) -> RunnerRunContext | None: ...
    async def request_cancellation(self, owner_id: str, run_id: UUID) -> str | None: ...

    async def get_cancellation_requests(self, runner_id: UUID) -> list[UUID]: ...

    async def get_lease_statuses(
        self, runner_id: UUID, run_ids: list[UUID]
    ) -> list[RunnerLeaseStatus]: ...

