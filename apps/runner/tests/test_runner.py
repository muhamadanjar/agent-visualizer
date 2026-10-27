from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from runner import (
    EventOutbox,
    ProviderResponseError,
    RunnerSettings,
    RunnerTransportError,
    TaskExecutionError,
    WorkspaceTools,
    _provider_tools_request,
    _resolve_workspace_path,
    execute_job,
    flush_events,
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


def test_runner_executes_openai_compatible_tool_loop_with_scoped_file_actions(tmp_path: Path) -> None:
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
            else:
                assert any(message.get("role") == "tool" for message in request["messages"])
                message = {"role": "assistant", "content": "Task completed."}
            body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
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

        def post(self, path: str, payload: dict[str, object], *, timeout: float = 10) -> dict[str, object]:
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
            "agent_definition": {"source_markdown": "Write files when asked."},
            "skills": [],
            "provider": {"endpoint_url": endpoint, "model_id": "test/model", "route": None},
        },
    }
    settings = RunnerSettings(
        api_url="http://api.invalid",
        shared_token="runner-test-token",
        provider_key="test-only-key",
        provider_url=None,
        workspace_root=tmp_path / "workspaces",
        state_root=tmp_path / "state",
    )
    try:
        with patch("runner._check_provider_endpoint", return_value=endpoint):
            execute_job(job, api, outbox, "runner-1", settings)  # type: ignore[arg-type]
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "completed"
        assert [event["event_type"] for event in api.events] == [
            "run.started",
            "session.heartbeat",
            "run.tool_action",
            "session.heartbeat",
            "run.succeeded",
        ]
        assert api.events[2]["tool_name"] == "write_file"
        assert outbox.pending() == []
        assert all("test-only-key" not in json.dumps(item["messages"]) for item in received)
    finally:
        outbox.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_provider_deadline_interrupts_trickled_response() -> None:
    body = json.dumps({"choices": [{"message": {"content": "done"}}]}).encode()

    class SlowProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.05)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowProviderHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(ProviderResponseError):
            _provider_tools_request(
                f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                "test-only-key",
                "test/model",
                [],
                [],
                0.2,
            )
        assert time.monotonic() - started < 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_deterministic_setup_failure_emits_terminal_failure(tmp_path: Path) -> None:
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
            "provider": {
                "endpoint_url": "https://provider.example/v1",
                "model_id": "model",
            }
        },
    }
    settings = RunnerSettings(
        api_url="http://api.invalid",
        shared_token="runner-test-token",
        provider_key=None,
        provider_url=None,
        workspace_root=tmp_path / "workspaces",
        state_root=tmp_path / "state",
    )
    try:
        with pytest.raises(TaskExecutionError, match="Run setup failed"):
            execute_job(job, api, outbox, "runner-1", settings)  # type: ignore[arg-type]
        assert [event["event_type"] for event in api.events] == ["run.failed"]
        assert outbox.pending() == []
    finally:
        outbox.close()
