from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from apps.launcher import proxy, server


def _docker_frame(stream: int, content: bytes) -> bytes:
    return bytes((stream, 0, 0, 0)) + len(content).to_bytes(4, "big") + content


def test_worker_log_frames_keep_only_complete_stdout_frames() -> None:
    started = {"sequence": 1, "kind": "started", "payload": {}}
    event = {
        "sequence": 2,
        "kind": "event",
        "payload": {
            "event_type": "run.tool_action",
            "event_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "source_sequence": 1,
            "tool_name": "read_file",
        },
    }
    result = {"sequence": 3, "kind": "result", "payload": {"text": "done"}}
    logs = b"".join(
        (
            _docker_frame(2, b"stderr is never returned"),
            _docker_frame(
                1,
                b"".join(
                    json.dumps(frame).encode() + b"\n"
                    for frame in (started, event, result)
                ),
            ),
        )
    )

    frames = server._worker_frames(logs)

    assert frames == [started, event, result]


def test_worker_log_frames_accept_bounded_failure_diagnostics() -> None:
    frames = [
        {"sequence": 1, "kind": "started", "payload": {}},
        {
            "sequence": 2,
            "kind": "failure",
            "payload": {
                "error_type": "ProviderResponseError",
                "cause_type": "HTTPError",
                "cause_status": 429,
                "cause_reason": "unavailable",
            },
        },
    ]
    logs = _docker_frame(
        1,
        b"".join(json.dumps(frame).encode() + b"\n" for frame in frames),
    )

    assert server._worker_frames(logs) == frames


def test_worker_log_frames_reject_unbounded_failure_reason() -> None:
    frames = [
        {"sequence": 1, "kind": "started", "payload": {}},
        {
            "sequence": 2,
            "kind": "failure",
            "payload": {
                "error_type": "ProviderResponseError",
                "cause_type": "HTTPError",
                "cause_status": 429,
                "cause_reason": "provider response leaked",
            },
        },
    ]

    with pytest.raises(server.EngineError):
        server._worker_frames(
            _docker_frame(
                1,
                b"".join(json.dumps(frame).encode() + b"\n" for frame in frames),
            )
        )


@pytest.mark.parametrize(
    ("running", "status_code", "retries"),
    [(True, 502, True), (False, 502, False), (True, 503, False)],
)
def test_event_stream_retries_transient_log_gateway_error_only_while_running(
    running: bool, status_code: int, retries: bool
) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"

    class Engine:
        def list_owned_role(self, _run_id: str, _role: str) -> list[dict[str, object]]:
            return [
                {
                    "Id": "worker",
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.RUN_ID_LABEL: run_id,
                        server.ROLE_LABEL: "worker",
                    },
                }
            ]

        def inspect_container(self, _container_id: str) -> dict[str, object]:
            return {"State": {"Running": running, "ExitCode": None}}

        def logs(self, _container_id: str) -> bytes:
            raise server.EngineError(status_code)

    launcher = object.__new__(server.RuntimeLauncher)
    launcher._engine = Engine()  # type: ignore[assignment]

    if not retries:
        with pytest.raises(server.EngineError):
            launcher.events(run_id, after_sequence=0)
        return

    assert launcher.events(run_id, after_sequence=0) == {
        "events": [],
        "status": "running",
        "exit_code": None,
        "started_at": None,
        "finished_at": None,
    }



def test_worker_log_frames_require_startup_before_activity() -> None:
    event = {
        "sequence": 1,
        "kind": "event",
        "payload": {
            "event_type": "session.heartbeat",
            "event_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "source_sequence": 1,
        },
    }
    with pytest.raises(server.EngineError):
        server._worker_frames(
            _docker_frame(1, json.dumps(event).encode() + b"\n")
        )

    duplicate_start = [
        {"sequence": 1, "kind": "started", "payload": {}},
        {"sequence": 2, "kind": "started", "payload": {}},
    ]
    with pytest.raises(server.EngineError):
        server._worker_frames(
            _docker_frame(
                1,
                b"".join(json.dumps(frame).encode() + b"\n" for frame in duplicate_start),
            )
        )


def test_worker_log_frames_reject_reordered_or_invalid_protocol() -> None:
    duplicate_sequence = {"sequence": 1, "kind": "result", "payload": {"text": "a"}}
    duplicate = json.dumps(duplicate_sequence) + "\n" + json.dumps(duplicate_sequence) + "\n"
    with pytest.raises(server.EngineError):
        server._worker_frames(_docker_frame(1, duplicate.encode()))

    with pytest.raises(server.EngineError):
        server._worker_frames(_docker_frame(1, b"not-json\n"))


@pytest.mark.parametrize(
    ("event_type", "tool_name"),
    [("run.canceled", None), ("run.succeeded", None), ("run.tool_action", "shell")],
)
def test_worker_frames_reject_lifecycle_events_and_unknown_tools(
    event_type: str, tool_name: str | None
) -> None:
    payload = {
        "event_type": event_type,
        "event_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "source_sequence": 1,
    }
    if tool_name is not None:
        payload["tool_name"] = tool_name
    started = {"sequence": 1, "kind": "started", "payload": {}}
    frame = {"sequence": 2, "kind": "event", "payload": payload}

    with pytest.raises(server.EngineError):
        server._worker_frames(
            _docker_frame(
                1,
                b"".join(
                    json.dumps(item).encode() + b"\n" for item in (started, frame)
                ),
            )
        )


def test_start_payload_accepts_only_selected_local_ninerouter_endpoint() -> None:
    payload = {
        "runner_id": "0f00b7b5-d3ea-4c16-b952-e036a07de498",
        "run_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
        "configuration_snapshot": {
            "model_endpoint": {
                "credential_env": "NINEROUTER_KEY",
                "endpoint_url": "http://localhost:20128/v1",
            }
        },
        "workspace_path": "project-a/run-1",
        "allowed_tools": ["read_file"],
        "max_duration_seconds": 3600,
        "max_inference_turns": 24,
        "max_tool_calls": 50,
    }

    runner_id, run_id, workspace, job, target = server._validate_start_payload(payload)

    assert runner_id == payload["runner_id"]
    assert run_id == payload["run_id"]
    assert workspace == "project-a/run-1"
    assert job["workspace_path"] == "."
    assert target == ("host.docker.internal", 20128)
    payload["max_inference_turns"] = 25
    with pytest.raises(server.LauncherError, match="Run inference limit is invalid"):
        server._validate_start_payload(payload)


def test_start_payload_rejects_private_and_unencrypted_nonlocal_endpoints() -> None:
    with pytest.raises(server.LauncherError):
        server._provider_target("http://model.example/v1")
    with pytest.raises(server.LauncherError):
        server._provider_target("https://127.0.0.1/v1")
    with pytest.raises(server.LauncherError):
        server._provider_target("http://localhost:8080/v1")


def test_workspace_creation_rejects_symlink_components(tmp_path: Path) -> None:
    container_root = tmp_path / "container"
    host_root = tmp_path / "host"
    outside = tmp_path / "outside"
    container_root.mkdir()
    host_root.mkdir()
    outside.mkdir()
    (container_root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(server.LauncherError):
        server._prepare_workspace(str(container_root), str(host_root), "link/subdir", 1000, 1000)


def test_workspace_creation_uses_only_the_validated_run_directory(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()

    bind_path, worker_path = server._prepare_workspace(
        str(root), str(root), "project-a/run-1", 1000, 1000
    )

    assert Path(bind_path) == root / "project-a" / "run-1"
    assert Path(bind_path).is_dir()
    assert Path(bind_path).stat().st_uid == 1000
    assert Path(bind_path).stat().st_gid == 1000
    assert worker_path == "/workspace"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "one/../two", r"one\\two", ""])
def test_unsafe_workspace_paths_are_rejected(path: str) -> None:
    assert not server._safe_relative_path(path)


def test_worker_container_configuration_has_fixed_resource_and_network_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NINEROUTER_KEY", "test-secret")
    monkeypatch.delenv("NINEROUTER_URL", raising=False)
    job = {
        "runner_id": "0f00b7b5-d3ea-4c16-b952-e036a07de498",
        "run_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
    }

    config = server._worker_container_config(
        "agent-office-runner:local",
        {server.OWNER_LABEL: "true"},
        str(job["runner_id"]),
        str(job["run_id"]),
        job,
        "/workspace",
        "/host/workspace/run-1",
        1000,
        1000,
    )
    host = config["HostConfig"]
    env = config["Env"]
    assert config["User"] == "1000:1000"

    assert host["Memory"] == 2 * 1024 * 1024 * 1024
    assert host["NanoCpus"] == 1_000_000_000
    assert host["PidsLimit"] == 128
    assert host["CapDrop"] == ["ALL"]
    assert host["ReadonlyRootfs"] is True
    assert host["NetworkMode"] == "ao-run-891e3b1574084d3991afff0ea5a06d18"
    assert "RUNNER_SHARED_TOKEN=" not in "\n".join(env)
    assert "NINEROUTER_KEY=test-secret" in env
    assert not any(item.startswith("NINEROUTER_URL=") for item in env)


def test_launcher_reconciles_only_owned_resources_without_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_id = "11111111-1111-4111-8111-111111111111"
    orphan_id = "22222222-2222-4222-8222-222222222222"

    class Engine:
        def list_owned_workers(self) -> list[dict[str, object]]:
            return [
                {
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.ROLE_LABEL: "worker",
                        server.RUN_ID_LABEL: active_id,
                    }
                }
            ]

        def list_owned_proxies(self) -> list[dict[str, object]]:
            return [
                {
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.ROLE_LABEL: "proxy",
                        server.RUN_ID_LABEL: orphan_id,
                    }
                }
            ]

        def request(self, method: str, path: str) -> list[dict[str, object]]:
            assert (method, path) == ("GET", "/networks")
            return [
                {
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.ROLE_LABEL: "run-network",
                        server.RUN_ID_LABEL: orphan_id,
                    }
                },
                {
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.ROLE_LABEL: "run-network",
                        server.RUN_ID_LABEL: active_id,
                    }
                },
                {
                    "Labels": {
                        server.OWNER_LABEL: "false",
                        server.ROLE_LABEL: "run-network",
                        server.RUN_ID_LABEL: "33333333-3333-4333-8333-333333333333",
                    }
                },
            ]

    launcher = server.RuntimeLauncher.__new__(server.RuntimeLauncher)
    launcher._engine = Engine()
    removed: list[str] = []
    monkeypatch.setattr(launcher, "remove_run", lambda run_id: removed.append(run_id))

    launcher._reconcile_orphans()

    assert removed == [orphan_id]


def test_proxy_rejects_dns_answers_that_resolve_to_private_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_HOST", "provider.example")
    monkeypatch.setenv("ALLOW_PORT", "443")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 443))],
    )

    with pytest.raises(ValueError):
        proxy._destination("provider.example", 443)


def test_proxy_http_host_must_match_allowlisted_request_target() -> None:
    assert proxy._http_request_target(
        "http://host.docker.internal:20128/v1/chat/completions",
        "host.docker.internal:20128",
    ) == (
        "host.docker.internal",
        20128,
        "/v1/chat/completions",
    )

    with pytest.raises(ValueError):
        proxy._http_request_target(
            "http://host.docker.internal:20128/v1",
            "internal.service:20128",
        )


@pytest.mark.parametrize(
    ("states", "expected_verified", "expected_kills"),
    [
        ([{"Running": False}], True, 0),
        ([{"Running": True}, {"Running": False}], True, 1),
        ([{"Running": True}, {"Running": True}], False, 1),
    ],
)
def test_stop_run_reports_only_inspected_worker_stop(
    states: list[dict[str, bool]], expected_verified: bool, expected_kills: int
) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"

    class Engine:
        def __init__(self) -> None:
            self.inspections = iter(states)
            self.kills = 0

        def list_owned_role(
            self, requested_run_id: str, role: str
        ) -> list[dict[str, object]]:
            assert requested_run_id == run_id
            assert role == "worker"
            return [
                {
                    "Id": "worker-container",
                    "Labels": {
                        server.OWNER_LABEL: "true",
                        server.RUN_ID_LABEL: run_id,
                        server.ROLE_LABEL: "worker",
                    },
                }
            ]

        def stop_container(self, _container_id: str) -> None:
            return None

        def inspect_container(self, _container_id: str) -> dict[str, object]:
            return {"State": next(self.inspections)}

        def kill_container(self, _container_id: str) -> None:
            self.kills += 1

    launcher = server.RuntimeLauncher.__new__(server.RuntimeLauncher)
    engine = Engine()
    launcher._engine = engine  # type: ignore[assignment]

    result = launcher.stop_run(run_id)

    assert result == {
        "run_id": run_id,
        "verified": expected_verified,
        "status": "stopped" if expected_verified else "unknown",
    }
    assert engine.kills == expected_kills
