from __future__ import annotations

import io
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


import pytest
from runner import (
    EventOutbox,
    RunnerSupervisor,
    RunnerSettings,
    RunnerTransportError,
    TaskExecutionError,
    WorkerStreamAPI,
    WorkspaceTools,
    _RunnerHeartbeatHandler,
    _absolute_provider_deadline,
    _resolve_workspace_path,
    execute_job,
    PermanentAPIResponseError,
    flush_events,
    _check_provider_endpoint,
)



def test_workspace_tools_confine_reads_and_writes_and_apply_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    tools = WorkspaceTools(root, ["list_files", "read_file", "write_file"])

    assert tools.call("write_file", {"path": "src/main.py", "content": "print('ok')"}) == {
        "written": True,
        "bytes": 11,
    }
    assert tools.call("read_file", {"path": "src/main.py"}) == {"content": "print('ok')"}
    assert tools.call("list_files", {"path": "."}) == {
        "files": ["src/main.py"],
        "truncated": False,
    }

    with pytest.raises(ValueError):
        tools.call("read_file", {"path": "../../outside"})
    with pytest.raises(ValueError):
        tools.call("read_file", {"path": ".env"})
    with pytest.raises(TaskExecutionError):
        WorkspaceTools(root, ["shell"])



def test_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TaskExecutionError):
        _resolve_workspace_path(root, "linked/new-project")
    assert not (outside / "new-project").exists()


def test_workspace_path_accepts_mounted_run_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    assert _resolve_workspace_path(root, ".") == root.resolve()

def test_event_outbox_keeps_unacknowledged_events_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "state" / "events.sqlite3"
    outbox = EventOutbox(path)
    event = outbox.enqueue("runner-id", "run-id", "run.started")
    outbox.close()

    restarted = EventOutbox(path)
    assert restarted.pending() == [event]
    restarted.acknowledge(event["event_id"])
    restarted.close()

    restarted_again = EventOutbox(path)
    assert restarted_again.pending() == []
    restarted_again.close()


def test_worker_outbox_rejects_terminal_and_unknown_tool_events(tmp_path: Path) -> None:
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    base = {
        "event_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "source_sequence": 1,
    }
    with pytest.raises(RunnerTransportError):
        outbox.enqueue_external(
            "runner-id", "run-id", {**base, "event_type": "run.canceled"}
        )
    with pytest.raises(RunnerTransportError):
        outbox.enqueue_external(
            "runner-id",
            "run-id",
            {**base, "event_type": "run.tool_action", "tool_name": "shell"},
        )
    assert outbox.pending() == []
    outbox.close()


def test_flush_events_retries_durable_outbox_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    outbox = EventOutbox(path)
    event = outbox.enqueue("runner-id", "run-id", "run.started")

    class UnavailableAPI:
        def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
            raise RunnerTransportError("offline")

    with pytest.raises(RunnerTransportError):
        flush_events(UnavailableAPI(), outbox)  # type: ignore[arg-type]
    assert outbox.pending() == [event]
    outbox.close()

    class AcknowledgingAPI:
        def __init__(self) -> None:
            self.received: list[dict[str, object]] = []

        def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
            self.received.append(payload)
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": 11,
                "replayed": True,
            }

    restarted = EventOutbox(path)
    api = AcknowledgingAPI()
    flush_events(api, restarted)  # type: ignore[arg-type]
    assert api.received == [event]
    assert restarted.pending() == []
    restarted.close()


def test_permanently_rejected_event_is_quarantined_and_does_not_block_following_events(
    tmp_path: Path,
) -> None:
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    rejected = outbox.enqueue("runner-id", "run-id", "run.started")
    accepted = outbox.enqueue("runner-id", "run-id", "session.heartbeat")

    class API:
        def __init__(self) -> None:
            self.acknowledged: list[str] = []

        def post(self, _path: str, payload: dict[str, object]) -> dict[str, object]:
            if payload["event_id"] == rejected["event_id"]:
                raise PermanentAPIResponseError(422)
            self.acknowledged.append(str(payload["event_id"]))
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": 1,
            }

    api = API()
    with pytest.raises(RunnerTransportError):
        flush_events(api, outbox)  # type: ignore[arg-type]

    assert outbox.pending() == []
    assert api.acknowledged == [accepted["event_id"]]
    assert outbox.rejected()[0]["event_id"] == rejected["event_id"]
    assert outbox.rejected()[0]["status_code"] == 422
    outbox.close()


def test_runner_executes_langchain_agent_with_scoped_file_actions(tmp_path: Path) -> None:
    received: list[dict[str, object]] = []

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer test-only-key"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(request)
            if len(received) == 1:
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps(
                                    {"path": "result.txt", "content": "completed"}
                                ),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
            else:
                assert any(message.get("role") == "tool" for message in request["messages"])
                message = {"role": "assistant", "content": "Task completed."}
                finish_reason = "stop"
            body = json.dumps(
                {
                    "id": f"chatcmpl-{len(received)}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test/model",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": finish_reason,
                        }
                    ],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class AcknowledgingAPI:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []
            self.result: dict[str, object] | None = None

        def post(
            self, path: str, payload: dict[str, object], *, timeout: float = 10
        ) -> dict[str, object]:
            if path == "/internal/runner/results":
                self.result = payload
                return {"accepted": True}
            self.events.append(payload)
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": len(self.events),
                "replayed": False,
            }

    api = AcknowledgingAPI()
    outbox = EventOutbox(tmp_path / "state" / "events.sqlite3")
    workspace = tmp_path / "workspaces" / "project"
    endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    job = {
        "run_id": "run-1",
        "workspace_path": "project",
        "allowed_tools": ["write_file"],
        "max_duration_seconds": 60,
        "max_inference_turns": 3,
        "max_tool_calls": 3,
        "configuration_snapshot": {
            "task": {
                "title": "Write result",
                "goal": "Write result.txt",
                "input_text": "",
                "acceptance_criteria": ["result.txt contains completed"],
                "max_duration_minutes": 1,
            },
            "agent_instance": {"display_name": "Mira"},
            "agent_definition": {
                "source_markdown": "Write files when asked.",
                "configuration": {
                    "role": "Task worker",
                    "model_id": "test/model",
                    "runtime_id": "langchain",
                    "route": "free",
                    "temperature": 0.2,
                    "max_iterations": 3,
                    "concurrency_limit": 1,
                    "execution_timeout_seconds": 60,
                    "allowed_tools": ["read_file", "write_file"],
                },
            },
            "skills": [],
            "model_endpoint": {
                "endpoint_url": endpoint,
                "credential_env": "NINEROUTER_KEY",
            },
        }
    }
    settings = RunnerSettings(
        api_url="http://api.invalid",
        shared_token="runner-test-token",
        provider_key="test-only-key",
        workspace_root=tmp_path / "workspaces",
        state_root=tmp_path / "state",
    )
    try:
        startup_ack: list[int] = []
        with patch("runner._check_provider_endpoint", return_value=endpoint):
            execute_job(
                job,
                api,
                outbox,
                "runner-1",
                settings,
                on_started=lambda: startup_ack.append(len(received)),
            )  # type: ignore[arg-type]
        assert startup_ack == [0]
        assert received[0]["temperature"] == 0.2
        assert received[0]["route"] == "free"
        assert "Task worker" in received[0]["messages"][0]["content"]
        assert [tool["function"]["name"] for tool in received[0]["tools"]] == ["write_file"]
        assert [event["event_type"] for event in api.events] == [
            "session.heartbeat",
            "run.tool_action",
            "session.heartbeat",
        ]
        assert api.events[1]["tool_name"] == "write_file"
        assert api.result == {
            "runner_id": "runner-1",
            "run_id": "run-1",
            "text": "Task completed.",
        }
        assert outbox.pending() == []
        assert all("test-only-key" not in json.dumps(item["messages"]) for item in received)
    finally:
        outbox.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_selected_public_provider_endpoint_does_not_require_global_url() -> None:
    with patch(
        "runner.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
    ):
        assert (
            _check_provider_endpoint("https://provider.example/v1")
            == "https://provider.example/v1"
        )

    assert (
        _check_provider_endpoint("http://localhost:20128/v1")
        == "http://host.docker.internal:20128/v1"
    )


def test_runner_absolute_deadline_interrupts_agent_execution() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="provider request deadline exceeded"):
        with _absolute_provider_deadline(0.2):
            time.sleep(2)
    assert time.monotonic() - started < 1


def test_deterministic_setup_failure_leaves_terminal_transition_to_supervisor(
    tmp_path: Path,
) -> None:
    class AcknowledgingAPI:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
            self.events.append(payload)
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": len(self.events),
                "replayed": False,
            }

    api = AcknowledgingAPI()
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    job = {
        "run_id": "run-setup-failure",
        "workspace_path": "project",
        "allowed_tools": ["read_file"],
        "max_duration_seconds": 60,
        "max_inference_turns": 3,
        "max_tool_calls": 3,
        "configuration_snapshot": {
            "model_endpoint": {
                "endpoint_url": "https://provider.example/v1",
                "credential_env": "NINEROUTER_KEY",
            },
            "agent_definition": {
                "configuration": {
                    "model_id": "model",
                    "role": "Boundary worker",
                    "runtime_id": "langchain",
                    "temperature": None,
                    "max_iterations": 3,
                    "concurrency_limit": 1,
                    "execution_timeout_seconds": 60,
                    "allowed_tools": ["read_file"],
                },
            },
        }
    }
    settings = RunnerSettings(
        api_url="http://api.invalid",
        shared_token="runner-test-token",
        provider_key=None,
        workspace_root=tmp_path / "workspaces",
        state_root=tmp_path / "state",
    )
    try:
        with pytest.raises(TaskExecutionError, match="Run setup failed"):
            execute_job(job, api, outbox, "runner-1", settings)  # type: ignore[arg-type]
        assert api.events == []
        assert outbox.pending() == []
    finally:
        outbox.close()


def test_heartbeat_acknowledgement_failure_propagates_from_langchain_callback(
    tmp_path: Path,
) -> None:
    class RejectingAPI:
        def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
            raise RunnerTransportError("lease rejected")

    outbox = EventOutbox(tmp_path / "events.sqlite3")
    handler = _RunnerHeartbeatHandler(
        RejectingAPI(),  # type: ignore[arg-type]
        outbox,
        "runner-1",
        "run-rejected",
    )
    try:
        assert handler.raise_error is True
        with pytest.raises(RunnerTransportError, match="lease rejected"):
            handler.on_llm_end(object())
        assert [event["event_type"] for event in outbox.pending()] == [
            "session.heartbeat"
        ]
    finally:
        outbox.close()


def test_runner_executes_without_any_workspace_tools(tmp_path: Path) -> None:
    requests: list[dict[str, object]] = []

    class ProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(
                {
                    "id": "chatcmpl-no-tools",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test/model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "No tools required."},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class AcknowledgingAPI:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []
            self.result: dict[str, object] | None = None

        def post(
            self, path: str, payload: dict[str, object], *, timeout: float = 10
        ) -> dict[str, object]:
            if path == "/internal/runner/results":
                self.result = payload
                return {"accepted": True}
            self.events.append(payload)
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": len(self.events),
                "replayed": False,
            }

    endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    api = AcknowledgingAPI()
    outbox = EventOutbox(tmp_path / "state" / "events.sqlite3")
    job = {
        "run_id": "run-no-tools",
        "workspace_path": "project",
        "allowed_tools": ["read_file"],
        "max_duration_seconds": 30,
        "max_inference_turns": 1,
        "max_tool_calls": 1,
        "configuration_snapshot": {
            "task": {"title": "Answer", "goal": "Answer without tools.", "max_duration_minutes": 1},
            "agent_instance": {"display_name": "Mira"},
            "agent_definition": {
                "configuration": {
                    "role": "Task worker",
                    "runtime_id": "langchain",
                    "model_id": "test/model",
                    "temperature": None,
                    "max_iterations": 1,
                    "execution_timeout_seconds": 30,
                    "allowed_tools": [],
                }
            },
            "model_endpoint": {
                "endpoint_url": endpoint,
                "credential_env": "NINEROUTER_KEY",
            },
            "skills": [],
        },
    }
    settings = RunnerSettings(
        api_url="http://api.invalid",
        shared_token="runner-test-token",
        provider_key="test-only-key",
        workspace_root=tmp_path / "workspaces",
        state_root=tmp_path / "state",
    )
    try:
        with patch("runner._check_provider_endpoint", return_value=endpoint):
            execute_job(job, api, outbox, "runner-1", settings)  # type: ignore[arg-type]
        assert requests[0].get("tools", []) == []
        assert [event["event_type"] for event in api.events] == [
            "session.heartbeat",
        ]
        assert api.result is not None
    finally:
        outbox.close()
        server.shutdown()
        server.server_close()

def test_worker_stream_api_emits_safe_event_and_result_frames() -> None:
    stream = io.StringIO()
    api = WorkerStreamAPI("runner-1", "run-1", stream)
    api.started()

    event_response = api.post(
        "/internal/runner/events",
        {
            "runner_id": "runner-1",
            "run_id": "run-1",
            "event_id": "event-1",
            "event_type": "run.tool_action",
            "tool_name": "write_file",
            "source_sequence": 1,
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "secret": "must-not-leak",
        },
    )
    result_response = api.post(
        "/internal/runner/results",
        {
            "runner_id": "runner-1",
            "run_id": "run-1",
            "text": "Completed safely.",
        },
    )

    frames = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert event_response == {
        "acknowledged": True,
        "event_id": "event-1",
        "cursor": 1,
    }
    assert result_response == {"accepted": True}
    assert frames == [
        {"sequence": 1, "kind": "started", "payload": {}},
        {
            "sequence": 2,
            "kind": "event",
            "payload": {
                "event_type": "run.tool_action",
                "event_id": "event-1",
                "occurred_at": "2026-01-01T00:00:00+00:00",
                "source_sequence": 1,
                "tool_name": "write_file",
            },
        },
        {
            "sequence": 3,
            "kind": "result",
            "payload": {"text": "Completed safely."},
        },
    ]


def test_external_worker_event_replay_reuses_host_sequence_and_payload(
    tmp_path: Path,
) -> None:
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    frame = {
        "event_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "event_type": "run.tool_action",
        "tool_name": "write_file",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "source_sequence": 9,
    }
    try:
        first = outbox.enqueue_external("runner-1", "run-1", frame)
        outbox.acknowledge(first["event_id"])
        replay = outbox.enqueue_external("runner-1", "run-1", frame)

        assert replay == first
        assert outbox.pending() == [first]
        with pytest.raises(RunnerTransportError, match="identity conflicts"):
            outbox.enqueue_external(
                "runner-1",
                "run-1",
                {**frame, "tool_name": "read_file"},
            )
    finally:
        outbox.close()


def test_supervisor_persists_result_before_success_terminal_event(tmp_path: Path) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"
    runner_id = "0f00b7b5-d3ea-4c16-b952-e036a07de498"
    started_at = datetime.now(UTC)
    finished_at = started_at + timedelta(seconds=1)

    class API:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def post(self, path: str, payload: dict[str, object], **_kwargs: object):
            self.calls.append((path, payload))
            if path == "/internal/runner/results":
                return {"accepted": True}
            if path == "/internal/runner/events":
                return {
                    "acknowledged": True,
                    "event_id": payload["event_id"],
                    "cursor": len(self.calls),
                }
            raise AssertionError(f"unexpected API path: {path}")

    class Launcher:
        removed: list[str] = []

        def start(self, _payload: dict[str, object]) -> dict[str, object]:
            return {
                "run_id": run_id,
                "status": "running",
                "started_at": started_at.isoformat(),
            }

        def events(self, _run_id: str, _cursor: int) -> dict[str, object]:
            return {
                "events": [
                    {
                        "sequence": 1,
                        "kind": "started",
                        "payload": {},
                    },
                    {
                        "sequence": 2,
                        "kind": "result",
                        "payload": {"text": "Completed"},
                    },
                ],
                "status": "exited",
                "exit_code": 0,
                "finished_at": finished_at.isoformat(),
            }

        def remove(self, target: str) -> None:
            self.removed.append(target)

    api = API()
    launcher = Launcher()
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    supervisor = RunnerSupervisor(runner_id, api, launcher, outbox)  # type: ignore[arg-type]
    try:
        supervisor.run_job(
            {
                "run_id": run_id,
                "configuration_snapshot": {},
                "workspace_path": "workspace",
                "allowed_tools": ["read_file"],
                "max_duration_seconds": 3600,
                "max_inference_turns": 12,
                "max_tool_calls": 50,
            }
        )

        result_index = next(
            index for index, call in enumerate(api.calls)
            if call[0] == "/internal/runner/results"
        )
        succeeded_index = next(
            index for index, call in enumerate(api.calls)
            if call[0] == "/internal/runner/events"
            and call[1]["event_type"] == "run.succeeded"
        )
        assert result_index < succeeded_index
        assert launcher.removed == [run_id]
        assert outbox.pending() == []
    finally:
        outbox.close()

def test_supervisor_rejects_worker_result_before_startup_acknowledgement(
    tmp_path: Path,
) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"
    runner_id = "0f00b7b5-d3ea-4c16-b952-e036a07de498"
    started_at = datetime.now(UTC)
    event_types: list[str] = []
    result_posts: list[dict[str, object]] = []

    class API:
        def post(self, path: str, payload: dict[str, object], **_kwargs: object):
            if path == "/internal/runner/results":
                result_posts.append(payload)
                return {"accepted": True}
            if path == "/internal/runner/events":
                event_types.append(str(payload["event_type"]))
                return {
                    "acknowledged": True,
                    "event_id": payload["event_id"],
                    "cursor": len(event_types),
                }
            raise AssertionError(f"unexpected API path: {path}")

    class Launcher:
        stopped: list[str] = []
        removed: list[str] = []

        def start(self, _payload: dict[str, object]) -> dict[str, object]:
            return {
                "run_id": run_id,
                "status": "running",
                "started_at": started_at.isoformat(),
            }

        def events(self, _run_id: str, _cursor: int) -> dict[str, object]:
            return {
                "events": [
                    {
                        "sequence": 1,
                        "kind": "result",
                        "payload": {"text": "forged"},
                    }
                ],
                "status": "running",
                "exit_code": None,
                "finished_at": None,
            }

        def stop(self, target: str) -> dict[str, bool]:
            self.stopped.append(target)
            return {"verified": True}

        def remove(self, target: str) -> None:
            self.removed.append(target)

    api = API()
    launcher = Launcher()
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    supervisor = RunnerSupervisor(runner_id, api, launcher, outbox)  # type: ignore[arg-type]
    try:
        supervisor.run_job(
            {
                "run_id": run_id,
                "configuration_snapshot": {},
                "workspace_path": "workspace",
                "allowed_tools": ["read_file"],
                "max_duration_seconds": 3600,
                "max_inference_turns": 12,
                "max_tool_calls": 50,
            }
        )

        assert launcher.stopped == [run_id]
        assert launcher.removed == [run_id]
        assert event_types == ["run.unknown"]
        assert result_posts == []
    finally:
        outbox.close()




@pytest.mark.parametrize(
    ("verified", "terminal", "removed"),
    [(True, "run.canceled", True), (False, "run.unknown", False)],
)
def test_supervisor_reports_cancellation_only_after_verified_stop(
    tmp_path: Path, verified: bool, terminal: str, removed: bool
) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"
    runner_id = "0f00b7b5-d3ea-4c16-b952-e036a07de498"
    started_at = datetime.now(UTC)

    class API:
        def __init__(self) -> None:
            self.events: list[str] = []

        def post(self, path: str, payload: dict[str, object], **_kwargs: object):
            if path == "/internal/runner/cancel-requests":
                return {"run_ids": [run_id]}
            if path == "/internal/runner/events":
                self.events.append(str(payload["event_type"]))
                return {
                    "acknowledged": True,
                    "event_id": payload["event_id"],
                    "cursor": len(self.events),
                }
            raise AssertionError(f"unexpected API path: {path}")

    class Launcher:
        removed: list[str] = []

        def start(self, _payload: dict[str, object]) -> dict[str, object]:
            return {
                "run_id": run_id,
                "status": "running",
                "started_at": started_at.isoformat(),
            }

        def events(self, _run_id: str, _cursor: int) -> dict[str, object]:
            return {
                "events": [],
                "status": "running",
                "exit_code": None,
                "finished_at": None,
            }

        def stop(self, _run_id: str) -> dict[str, bool]:
            return {"verified": verified}

        def remove(self, target: str) -> None:
            self.removed.append(target)

    api = API()
    launcher = Launcher()
    outbox = EventOutbox(tmp_path / "events.sqlite3")
    supervisor = RunnerSupervisor(runner_id, api, launcher, outbox)  # type: ignore[arg-type]
    try:
        supervisor.run_job(
            {
                "run_id": run_id,
                "configuration_snapshot": {},
                "workspace_path": "workspace",
                "allowed_tools": ["read_file"],
                "max_duration_seconds": 3600,
                "max_inference_turns": 12,
                "max_tool_calls": 50,
            }
        )

        assert api.events[-2:] == ["run.cancellation_pending", terminal]
        assert launcher.removed == ([run_id] if removed else [])
    finally:
        outbox.close()


@pytest.mark.parametrize(
    ("list_error", "terminal"),
    [(False, "run.failed"), (True, "run.unknown")],
)
def test_start_failure_is_terminal_only_after_launcher_reconciliation(
    tmp_path: Path, list_error: bool, terminal: str
) -> None:
    run_id = "891e3b15-7408-4d39-91af-ff0ea5a06d18"
    runner_id = "0f00b7b5-d3ea-4c16-b952-e036a07de498"
    event_types: list[str] = []

    class API:
        def post(self, path: str, payload: dict[str, object], **_kwargs: object):
            assert path == "/internal/runner/events"
            event_types.append(str(payload["event_type"]))
            return {
                "acknowledged": True,
                "event_id": payload["event_id"],
                "cursor": len(event_types),
            }

    class Launcher:
        def start(self, _payload: dict[str, object]) -> dict[str, object]:
            raise RunnerTransportError("launcher response was unavailable")

        def list_runs(self) -> list[dict[str, object]]:
            if list_error:
                raise RunnerTransportError("launcher reconciliation unavailable")
            return []

    outbox = EventOutbox(tmp_path / "events.sqlite3")
    supervisor = RunnerSupervisor(runner_id, API(), Launcher(), outbox)  # type: ignore[arg-type]
    try:
        supervisor.run_job(
            {
                "run_id": run_id,
                "configuration_snapshot": {},
                "workspace_path": "workspace",
                "allowed_tools": ["read_file"],
                "max_duration_seconds": 3600,
                "max_inference_turns": 12,
                "max_tool_calls": 50,
            }
        )

        assert event_types == [terminal]
    finally:
        outbox.close()
