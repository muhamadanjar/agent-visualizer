from __future__ import annotations

import pytest

from app.domain.execution import ExecutionPolicyInputError, WorkspacePolicyDraft


def test_workspace_policy_accepts_confined_path_known_tools_and_bounded_limits() -> None:
    policy = WorkspacePolicyDraft.create(
        workspace_path="repositories/agent-office",
        allowed_tools=("list_files", "read_file", "write_file"),
        max_duration_seconds=900,
        max_inference_turns=6,
        max_tool_calls=24,
    )

    assert policy.workspace_path == "repositories/agent-office"
    assert policy.allowed_tools == ("list_files", "read_file", "write_file")
    assert policy.max_duration_seconds == 900
    assert policy.max_inference_turns == 6
    assert policy.max_tool_calls == 24


@pytest.mark.parametrize("workspace_path", ["", ".", "../outside", "repo/../../outside", "/etc", "C:\\repo", "repo\\nested"])
def test_workspace_policy_rejects_paths_outside_runner_workspace(workspace_path: str) -> None:
    with pytest.raises(ExecutionPolicyInputError, match="workspace_path"):
        WorkspacePolicyDraft.create(
            workspace_path=workspace_path,
            allowed_tools=("read_file",),
            max_duration_seconds=900,
            max_inference_turns=6,
            max_tool_calls=24,
        )


def test_workspace_policy_rejects_unknown_or_duplicate_tools() -> None:
    with pytest.raises(ExecutionPolicyInputError, match="allowed_tools"):
        WorkspacePolicyDraft.create(
            workspace_path="repo",
            allowed_tools=("shell",),
            max_duration_seconds=900,
            max_inference_turns=6,
            max_tool_calls=24,
        )

    with pytest.raises(ExecutionPolicyInputError, match="allowed_tools"):
        WorkspacePolicyDraft.create(
            workspace_path="repo",
            allowed_tools=("read_file", "read_file"),
            max_duration_seconds=900,
            max_inference_turns=6,
            max_tool_calls=24,
        )


def test_workspace_policy_rejects_unbounded_execution_limits() -> None:
    with pytest.raises(ExecutionPolicyInputError, match="max_duration_seconds"):
        WorkspacePolicyDraft.create(
            workspace_path="repo",
            allowed_tools=("read_file",),
            max_duration_seconds=0,
            max_inference_turns=6,
            max_tool_calls=24,
        )


def test_workspace_policy_accepts_24_inference_turns() -> None:
    policy = WorkspacePolicyDraft.create(
        workspace_path="repo",
        allowed_tools=("read_file",),
        max_duration_seconds=900,
        max_inference_turns=24,
        max_tool_calls=24,
    )

    assert policy.max_inference_turns == 24


def test_workspace_policy_rejects_more_than_24_inference_turns() -> None:
    with pytest.raises(ExecutionPolicyInputError, match="max_inference_turns"):
        WorkspacePolicyDraft.create(
            workspace_path="repo",
            allowed_tools=("read_file",),
            max_duration_seconds=900,
            max_inference_turns=25,
            max_tool_calls=24,
        )
