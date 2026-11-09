from __future__ import annotations

import json
import socket
import threading
import time
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
                "cause_reason": "Codex upstream network connection failed",
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
    "event_type",
    ["run.working", "run.tool_started", "run.reviewing"],
)
def test_worker_log_frames_accepts_office_activity_phases(event_type: str) -> None:
    started = {"sequence": 1, "kind": "started", "payload": {}}
    event = {
        "sequence": 2,
        "kind": "event",
        "payload": {
            "event_type": event_type,
            "event_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
            "occurred_at": "2026-01-01T00:00:00+00:00",
            "source_sequence": 1,
        },
    }
    frames = server._worker_frames(
        _docker_frame(
            1,
            b"".join(
                json.dumps(frame).encode() + b"\n" for frame in (started, event)
            ),
        )
    )

    assert frames == [started, event]

@pytest.mark.parametrize(
    ("event_type", "tool_name"),
    [("run.canceled", None), ("run.succeeded", None), ("run.tool_action", "shell")],
)
def test_worker_frames_reject_terminal_events_and_unknown_tools(
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


def test_planning_payload_rejects_unknown_fields_tools_streaming_and_paths() -> None:
    payload = {
        "endpoint": {
            "credential_env": "OMP_GATEWAY_KEY",
            "endpoint_url": server.GATEWAY_ENDPOINT,
            "model_id": "openai-codex/gpt-5.2",
        },
        "messages": [{"role": "user", "content": "Plan this."}],
        "response_format": {"type": "json_object"},
        "max_tokens": 128,
    }
    assert server._validate_planning_payload(payload)["messages"] == payload["messages"]
    for extra in (
        {"stream": False},
        {"tools": []},
        {"endpoint_url": server.GATEWAY_ENDPOINT},
        {"path": "/v1/chat/completions"},
        {"url": "https://provider.example/v1"},
    ):
        invalid = dict(payload)
        invalid.update(extra)
        with pytest.raises(server.LauncherError):
            server._validate_planning_payload(invalid)


def test_planning_endpoint_and_model_lengths_are_bounded() -> None:
    payload = {
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": "https://provider.example/v1", "model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    }
    payload["endpoint"]["endpoint_url"] += "a" * 2100
    with pytest.raises(server.LauncherError):
        server._validate_planning_payload(payload)
    payload["endpoint"]["endpoint_url"] = "https://provider.example/v1"
    payload["endpoint"]["model_id"] = "m" * 201
    with pytest.raises(server.LauncherError):
        server._validate_planning_payload(payload)


def test_planning_ninerouter_model_id_allows_plus() -> None:
    payload = {
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": "https://provider.example/v1", "model_id": "provider/model+variant"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    }
    assert server._validate_planning_payload(payload)["endpoint"]["model_id"] == "provider/model+variant"


def test_planning_unpaired_surrogate_is_a_generic_bad_message() -> None:
    payload = {
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": "https://provider.example/v1", "model_id": "model"},
        "messages": [{"role": "user", "content": "\ud800"}], "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError, match="Planning message is invalid"):
        server._validate_planning_payload(payload)


def test_planning_provider_preserves_allowlisted_base_path_and_non_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS",
        "https://provider.example:8443/proxy/v1",
    )
    requests: list[tuple[str, dict[str, str]]] = []

    class Connection:
        def __init__(self, *_args: object, **_kwargs: object) -> None: pass
        def request(self, _method: str, path: str, **kwargs: object) -> None:
            requests.append((path, kwargs["headers"]))
        def getresponse(self) -> object:
            return type("Response", (), {"status": 200, "read": lambda _self, _limit: b'{"choices":[{"message":{"content":"{}"}}]}'})()
        def close(self) -> None: pass

    monkeypatch.setattr(server.socket, "getaddrinfo", lambda *_args, **_kwargs: [(server.socket.AF_INET, server.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 8443))])
    monkeypatch.setattr(server, "_PinnedHTTPSConnection", Connection)
    payload = server._validate_planning_payload({
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": "https://provider.example:8443/proxy/v1", "model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    })
    server._planning_provider_request(payload, "launcher-key")
    assert requests[0][0] == "/proxy/v1/chat/completions"
    assert requests[0][1]["Host"] == "provider.example:8443"


def test_planning_provider_rejects_caller_selected_path_under_allowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS",
        "https://provider.example:8443/proxy/v1",
    )
    payload = server._validate_planning_payload({
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": "https://provider.example:8443/other", "model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    })
    with pytest.raises(server.LauncherError, match="not authorized"):
        server._planning_provider_request(payload, "secret")


def test_planning_local_ninerouter_maps_default_allowlisted_paths_to_fixed_host_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS", raising=False)
    requests: list[tuple[str, str, int, dict[str, str]]] = []

    class Connection:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            requests.append((host, "", port, {"timeout": str(timeout)}))
        def request(self, _method: str, path: str, **kwargs: object) -> None:
            requests[-1] = (requests[-1][0], path, requests[-1][2], kwargs["headers"])
        def getresponse(self) -> object:
            return type("Response", (), {"status": 200, "read": lambda _self, _limit: b'{"choices":[{"message":{"content":"{}"}}]}'})()
        def close(self) -> None: pass

    monkeypatch.setattr(server.http.client, "HTTPConnection", Connection)
    for alias, api_path in (
        ("localhost", "/proxy/v1"),
        ("127.0.0.1", "/proxy/v1"),
        ("localhost", "/v1"),
        ("127.0.0.1", "/v1"),
    ):
        payload = server._validate_planning_payload({
            "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": f"http://{alias}:20128{api_path}", "model_id": "model"},
            "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
        })
        server._planning_provider_request(payload, "launcher-key")
        assert requests[-1][:3] == ("host.docker.internal", f"{api_path}/chat/completions", 20128)
        assert requests[-1][3]["Host"] == "host.docker.internal:20128"

    for endpoint_url in ("http://localhost:20129/v1", "http://private.example/v1", "https://attacker.example/v1"):
        payload["endpoint"]["endpoint_url"] = endpoint_url
        with pytest.raises(server.LauncherError):
            server._planning_provider_request(payload, "launcher-key")


def test_planning_rejects_untrusted_public_provider_before_sending_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS",
        "https://trusted.example/v1",
    )
    monkeypatch.setattr(
        server.socket, "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail("untrusted provider was resolved"),
    )
    payload = server._validate_planning_payload({
        "endpoint": {
            "credential_env": "NINEROUTER_KEY",
            "endpoint_url": "https://attacker.example/v1",
            "model_id": "model",
        },
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    })
    with pytest.raises(server.LauncherError, match="not authorized"):
        server._planning_provider_request(payload, "secret")


@pytest.mark.parametrize(
    "endpoint_url",
    ["http://127.0.0.1:14000/v1", "https://127.0.0.1/v1"],
)
def test_planning_ninerouter_rejects_private_or_arbitrary_destinations(
    monkeypatch: pytest.MonkeyPatch, endpoint_url: str,
) -> None:
    payload = {
        "endpoint": {"credential_env": "NINEROUTER_KEY", "endpoint_url": endpoint_url, "model_id": "combo"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError):
        server._planning_provider_request(server._validate_planning_payload(payload), "secret")



def test_planning_gateway_request_uses_launcher_gateway_key_and_bounded_body(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, bytes, dict[str, str]]] = []

    class Connection:
        def __init__(self, *_args: object, **_kwargs: object) -> None: pass
        def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
            requests.append((path, body, headers))
        def getresponse(self) -> object:
            return type("Response", (), {"status": 200, "read": lambda _self, _limit: b'{"choices":[{"message":{"content":"{}"}}]}'})()
        def close(self) -> None: pass

    monkeypatch.setattr(server, "_UnixHTTPConnection", Connection)
    payload = server._validate_planning_payload({
        "endpoint": {"credential_env": "OMP_GATEWAY_KEY", "endpoint_url": server.GATEWAY_ENDPOINT, "model_id": "openai-codex/gpt-5.2"},
        "messages": [{"role": "user", "content": "Plan."}], "max_tokens": 1,
    })
    server._planning_gateway_request(payload, "launcher-key")
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][2]["Authorization"] == "Bearer launcher-key"
    assert b"launcher-key" not in requests[0][1]


def test_planning_response_strips_provider_metadata_and_redacts_credential() -> None:
    assert server._planning_response(
        {
            "id": "provider-id",
            "usage": {"prompt_tokens": 4},
            "reasoning": "hidden reasoning",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": '{"token":"provider-key"}',
                    "reasoning_content": "hidden reasoning",
                    "diagnostic": "provider-key",
                },
                "finish_reason": "stop",
            }],
        },
        "provider-key",
    ) == {"choices": [{"message": {"content": '{"token":"[REDACTED]"}'}}]}

def test_planning_response_redacts_unicode_escaped_credential_in_plan() -> None:
    assert server._planning_response(
        {
            "choices": [{
                "message": {
                    "content": '{"nodes":[{"input_text":"\\u0066ixture-secret"}]}',
                },
            }],
        },
        "fixture-secret",
    ) == {"choices": [{"message": {"content": '{"nodes":[{"input_text":"[REDACTED]"}]}'}}]}


@pytest.mark.parametrize(
    ("secret", "content"),
    [
        ("REDACTED", '{"nodes":[{"input_text":"REDACTED"}]}'),
        ("12345", '{"nodes":[{"output_schema":{"const":12345}}]}'),
        ("token", '{"nodes":[],"token":1,"[REDACTED]":2}'),
        ("true", '{"nodes":[],"enabled":true}'),
        ("null", '{"nodes":[],"value":null}'),
    ],
)
def test_planning_response_rejects_secrets_that_survive_redaction(secret: str, content: str) -> None:
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response(
            {"choices": [{"message": {"content": content}}]},
            secret,
        )


def test_planning_response_rejects_credential_recreated_across_redaction() -> None:
    secret = 'a"[REDACTED]'
    content = json.dumps(
        {"nodes": [{"input_text": 'a"a"[REDACTED]'}]},
        separators=(",", ":"),
    )
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response(
            {"choices": [{"message": {"content": content}}]},
            secret,
        )


def test_planning_response_accepts_quote_credential_absent_from_plan() -> None:
    content = '{"nodes":[]}'
    assert server._planning_response(
        {"choices": [{"message": {"content": content}}]},
        '"',
    ) == {"choices": [{"message": {"content": content}}]}


def test_planning_response_rejects_redaction_expansion_over_limit() -> None:
    content = json.dumps({"nodes": [{"input_text": "x" * 30_000}]}, separators=(",", ":"))
    assert len(content.encode("utf-8")) < server.MAX_PLANNING_RESPONSE_BYTES
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response(
            {"choices": [{"message": {"content": content}}]},
            "x",
        )


def test_planning_response_rejects_surrogate_created_by_json_decoding() -> None:
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response(
            {"choices": [{"message": {"content": '{"nodes":[{"input_text":"\\ud800"}]}'}}]},
            "provider-key",
        )


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_planning_response_rejects_nonfinite_plan_values(number: str) -> None:
    content = '{"nodes":[],"value":' + number + "}"
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response(
            {"choices": [{"message": {"content": content}}]},
            "provider-key",
        )


def test_planning_response_preserves_finite_json_float() -> None:
    content = '{"nodes":[],"threshold":0.25}'
    assert server._planning_response(
        {"choices": [{"message": {"content": content}}]},
        "provider-key",
    ) == {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_planning_http_request_rejects_nonfinite_provider_envelope(number: str) -> None:
    body = ('{"choices":[{"message":{"content":"{}"}}],"usage":{"value":' + number + "}}").encode()

    class Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> object:
            return type("Response", (), {
                "status": 200,
                "read": lambda _self, _limit: body,
            })()

        def close(self) -> None:
            pass

    request = {
        "endpoint": {"model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError, match="Planning response is invalid"):
        server._planning_http_request(Connection(), "/v1/chat/completions", request, {}, secret="provider-key")


def test_planning_http_request_rejects_deep_provider_envelope() -> None:
    body = (
        b'{"choices":[{"message":{"content":"{}"}}],"ignored":'
        + b"[" * 1100
        + b"0"
        + b"]" * 1100
        + b"}"
    )

    class Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> object:
            return type("Response", (), {
                "status": 200,
                "read": lambda _self, _limit: body,
            })()

        def close(self) -> None:
            pass

    request = {
        "endpoint": {"model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError, match="Planning response is invalid"):
        server._planning_http_request(Connection(), "/v1/chat/completions", request, {}, secret="provider-key")


def test_planning_response_rejects_success_envelopes_without_message_content() -> None:
    with pytest.raises(server.LauncherError, match="response is invalid"):
        server._planning_response({"error": {"detail": "provider-key"}}, "provider-key")


def test_planning_http_request_rejects_unpaired_surrogate_response() -> None:
    class Connection:
        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> object:
            return type("Response", (), {
                "status": 200,
                "read": lambda _self, _limit: b'{"choices":[{"message":{"content":"\\ud800"}}]}',
            })()

        def close(self) -> None:
            pass

    request = {
        "endpoint": {"model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError, match="Planning response is invalid"):
        server._planning_http_request(Connection(), "/v1/chat/completions", request, {})

def test_planning_deadline_reduces_read_timeout_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 10.0}

    class Sock:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def settimeout(self, value: float) -> None:
            self.timeouts.append(value)

        def shutdown(self, _how: int) -> None:
            pass

    class Response:
        status = 200
        fp = None

        def read1(self, _limit: int) -> bytes:
            clock["now"] = 11.1
            return b'{"choices":[]}'

    class Connection:
        timeout = 60

        def __init__(self) -> None:
            self.sock = Sock()

        def request(self, *_args: object, **_kwargs: object) -> None:
            clock["now"] = 10.2

        def getresponse(self) -> Response:
            clock["now"] = 10.5
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(server.time, "monotonic", lambda: clock["now"])
    connection = Connection()
    request = {
        "endpoint": {"model_id": "model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    with pytest.raises(server.LauncherError, match="timed out"):
        server._planning_http_request(
            connection, "/v1/chat/completions", request, {}, deadline=11.0
        )
    assert connection.sock.timeouts == pytest.approx([1.0, 0.8, 0.5])


def test_planning_deadline_interrupts_trickling_response_headers() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]

    def trickle_headers() -> None:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        with client:
            client.settimeout(1)
            request_data = bytearray()
            while b"\r\n\r\n" not in request_data:
                chunk = client.recv(4096)
                if not chunk:
                    return
                request_data.extend(chunk)
            header, body = bytes(request_data).split(b"\r\n\r\n", 1)
            content_length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            while len(body) < content_length:
                chunk = client.recv(4096)
                if not chunk:
                    return
                body += chunk
            try:
                for byte in b"HTTP/1.1 200 OK\r\nX-":
                    client.sendall(bytes((byte,)))
                    time.sleep(0.03)
                for _ in range(60):
                    client.sendall(b"A")
                    time.sleep(0.03)
            except OSError:
                pass

    worker = threading.Thread(target=trickle_headers, daemon=True)
    worker.start()
    request = {
        "endpoint": {"model_id": "test-model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    connection = server.http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    deadline = time.monotonic() + 0.15
    started = time.monotonic()
    try:
        with pytest.raises(server.LauncherError, match="timed out") as error:
            server._planning_http_request(
                connection, "/v1/chat/completions", request, {}, deadline=deadline
            )
        assert error.value.status_code == 504
        assert time.monotonic() - started < 0.75
    finally:
        listener.close()
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_planning_deadline_interrupts_trickled_chunk_size_on_close_response() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]

    def trickle_chunk_size() -> None:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        with client:
            client.settimeout(1)
            request_data = bytearray()
            while b"\r\n\r\n" not in request_data:
                chunk = client.recv(4096)
                if not chunk:
                    return
                request_data.extend(chunk)
            header, body = bytes(request_data).split(b"\r\n\r\n", 1)
            content_length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            while len(body) < content_length:
                chunk = client.recv(4096)
                if not chunk:
                    return
                body += chunk
            client.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Connection: close\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            try:
                for _ in range(60):
                    client.sendall(b"1")
                    time.sleep(0.03)
            except OSError:
                pass

    worker = threading.Thread(target=trickle_chunk_size, daemon=True)
    worker.start()
    request = {
        "endpoint": {"model_id": "test-model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    connection = server.http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    deadline = time.monotonic() + 0.15
    started = time.monotonic()
    try:
        with pytest.raises(server.LauncherError, match="timed out") as error:
            server._planning_http_request(
                connection, "/v1/chat/completions", request, {}, deadline=deadline
            )
        assert error.value.status_code == 504
        assert time.monotonic() - started < 0.75
    finally:
        listener.close()
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_pinned_https_deadline_interrupts_tls_handshake_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interrupted = threading.Event()
    sent: list[bytes] = []

    class Transport:
        def settimeout(self, _timeout: float) -> None:
            pass

        def shutdown(self, _how: int) -> None:
            interrupted.set()

        def close(self) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            sent.append(data)

    transport = Transport()

    class TLSContext:
        def wrap_socket(self, _sock: object, *, server_hostname: str) -> object:
            assert server_hostname == "provider.example"
            assert interrupted.wait(1)

            class WrappedSocket:
                def settimeout(self, _timeout: float) -> None:
                    pass

                def close(self) -> None:
                    pass

                def sendall(self, data: bytes) -> None:
                    sent.append(data)

            return WrappedSocket()

    monkeypatch.setattr(server.socket, "create_connection", lambda *_args, **_kwargs: transport)
    deadline = time.monotonic() + 0.15
    connection = server._PinnedHTTPSConnection(
        "provider.example", "93.184.216.34", 443, timeout=2, deadline=deadline
    )
    connection._context = TLSContext()
    request = {
        "endpoint": {"model_id": "test-model"},
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }

    with pytest.raises(server.LauncherError, match="timed out") as error:
        server._planning_http_request(
            connection, "/v1/chat/completions", request, {}, deadline=deadline
        )

    assert error.value.status_code == 504
    assert interrupted.is_set()
    assert sent == []

def test_planning_dns_resolution_enforces_deadline_without_unbounded_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked_resolution(*_args: object, **_kwargs: object) -> list[tuple]:
        entered.set()
        assert release.wait(2)
        return []

    monkeypatch.setattr(server.socket, "getaddrinfo", blocked_resolution)
    worker: threading.Thread | None = None
    try:
        with pytest.raises(server.LauncherError, match="resolution timed out"):
            server._resolve_provider_addresses(
                "provider.example", 443, server.time.monotonic() + 0.02
            )
        assert entered.wait(1)
        worker = server._DNS_RESOLUTION_THREAD
    finally:
        release.set()
        worker = worker or server._DNS_RESOLUTION_THREAD
        if worker is not None:
            worker.join(timeout=1)
    assert worker is not None
    assert not worker.is_alive()


def test_planning_reservation_releases_runtime_lock_and_blocks_codex_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = object.__new__(server.RuntimeLauncher)
    launcher._lock = threading.RLock()
    launcher._planning_slots = threading.BoundedSemaphore(1)
    launcher._planning_gateway_active = False
    launcher._engine = type("Engine", (), {"list_owned_workers": lambda _self: []})()
    launcher._model_key = ""
    launcher._host_workspace_root = "/host"
    launcher._worker_uid = 1000
    launcher._worker_gid = 1000
    started = threading.Event()
    release = threading.Event()

    def planning_request(*_args: object, **_kwargs: object) -> object:
        started.set()
        assert release.wait(2)
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(server, "_gateway_key", lambda: "gateway-key")
    monkeypatch.setattr(server, "_planning_gateway_request", planning_request)
    monkeypatch.setattr(server, "_gateway_ready", lambda *_args: True)
    monkeypatch.setattr(
        server,
        "_validate_start_payload",
        lambda _payload: (
            "runner-id",
            "891e3b15-7408-4d39-91af-ff0ea5a06d18",
            "workspace",
            {
                "configuration_snapshot": {
                    "model_endpoint": {"credential_env": "OMP_GATEWAY_KEY"},
                    "agent_definition": {"configuration": {"model_id": "codex"}},
                }
            },
            ("localhost", 14000),
        ),
    )
    monkeypatch.setattr(server, "_prepare_workspace", lambda *_args: ("/host", "/workspace"))
    payload = {
        "endpoint": {
            "credential_env": "OMP_GATEWAY_KEY",
            "endpoint_url": server.GATEWAY_ENDPOINT,
            "model_id": "openai-codex/gpt-5.2",
        },
        "messages": [{"role": "user", "content": "Plan."}],
        "max_tokens": 1,
    }
    thread = threading.Thread(target=launcher.planning_chat, args=(payload,))
    thread.start()
    try:
        assert started.wait(2)
        assert launcher._lock.acquire(timeout=0.2)
        launcher._lock.release()
        with pytest.raises(server.LauncherError, match="reserved for workflow planning"):
            launcher.start_run({})
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


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
        "configuration_snapshot": {
            "model_endpoint": {"credential_env": "NINEROUTER_KEY"},
        },
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
