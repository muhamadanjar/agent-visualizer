from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from apps.launcher import proxy, server


def payload() -> dict:
    return {
        "runner_id": "0f00b7b5-d3ea-4c16-b952-e036a07de498",
        "run_id": "891e3b15-7408-4d39-91af-ff0ea5a06d18",
        "configuration_snapshot": {
            "model_endpoint": {"credential_env": "OMP_GATEWAY_KEY", "endpoint_url": server.GATEWAY_ENDPOINT},
            "agent_definition": {"configuration": {"runtime_id": "langchain", "model_id": "openai-codex/gpt-5.2"}},
        },
        "workspace_path": "project/run", "allowed_tools": ["read_file"],
        "max_duration_seconds": 120, "max_inference_turns": 4, "max_tool_calls": 4,
    }


@pytest.mark.parametrize("credential", ["OMP_GATEWAY_KEY", "NINEROUTER_KEY"])
def test_worker_receives_only_selected_provider_key(monkeypatch, credential):
    monkeypatch.setenv("OMP_GATEWAY_KEY", "fixture-gateway")
    monkeypatch.setenv("NINEROUTER_KEY", "fixture-router")
    job = payload()
    job["configuration_snapshot"]["model_endpoint"]["credential_env"] = credential
    config = server._worker_container_config("worker", {}, job["runner_id"], job["run_id"], job,
                                             "/workspace", "/host/run", 1000, 1000)
    keys = {entry.split("=", 1)[0] for entry in config["Env"]}
    assert keys & {"OMP_GATEWAY_KEY", "NINEROUTER_KEY"} == {credential}
    assert config["HostConfig"]["CapDrop"] == ["ALL"]
    assert len(config["HostConfig"]["Mounts"]) == 1

def test_second_codex_run_is_rejected_while_first_is_active(monkeypatch):
    class Engine:
        def list_owned_role(self, run_id, role):
            return []

        def list_owned_workers(self):
            return [{"State": "running", "Labels": {"agent-office.provider": "omp-gateway"}}]

    launcher = object.__new__(server.RuntimeLauncher)
    launcher._engine = Engine()
    launcher._host_workspace_root = "/host"
    launcher._worker_uid = launcher._worker_gid = 1000
    launcher._model_key = ""
    launcher._lock = threading.RLock()
    monkeypatch.setattr(server, "_gateway_ready", lambda model: True)
    monkeypatch.setattr(server, "_prepare_workspace", lambda *args: ("/host/run", "/workspace"))
    with pytest.raises(server.LauncherError, match="Codex account already has an active Run") as error:
        launcher.start_run(payload())
    assert error.value.status_code == 429


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:14000/v1", "http://localhost:18765/v1", "https://example.com/v1", "http://localhost:14000/v1?route=x"])
def test_gateway_rejects_other_destinations(endpoint):
    job = payload()
    job["configuration_snapshot"]["model_endpoint"]["endpoint_url"] = endpoint
    with pytest.raises(server.LauncherError):
        server._validate_start_payload(job)


@pytest.mark.parametrize("model,route", [("gpt-5.2", None), ("openai/gpt-5.2", None), ("openai-codex/gpt-5.2", "fallback")])
def test_gateway_rejects_unqualified_models_and_routes(model, route):
    job = payload()
    job["configuration_snapshot"]["agent_definition"]["configuration"].update(model_id=model, route=route)
    with pytest.raises(server.LauncherError):
        server._validate_start_payload(job)


@pytest.mark.parametrize("anonymous,authenticated,models,single_attempt,ready", [
    (401, 200, ["openai-codex/gpt-5.2"], True, True),
    (401, 200, ["openai-codex/gpt-5.2"], False, False),
    (200, 200, ["openai-codex/gpt-5.2"], True, False),
    (401, 401, ["openai-codex/gpt-5.2"], True, False),
    (401, 200, ["openai/gpt-5.2"], True, False),
    (401, 200, [], True, False),
])
def test_gateway_readiness_requires_authenticated_scoped_catalogue(monkeypatch, anonymous, authenticated, models, single_attempt, ready):
    monkeypatch.setenv("OMP_GATEWAY_KEY", "fixture-gateway")
    class Connection:
        def __init__(self, path, timeout):
            assert path == server.GATEWAY_SOCKET
        def request(self, method, path, headers):
            self.path = path
            self.status = 200 if path == "/healthz" else authenticated if "Authorization" in headers else anonymous
        def getresponse(self):
            return self
        def read(self, limit):
            if self.path == "/healthz":
                return json.dumps({
                    "policy": "agent-office-codex-single-attempt-v1",
                    "ompVersion": "18.12.3",
                    "maxUpstreamAttempts": 1 if single_attempt else 6,
                    "websockets": False,
                }).encode()
            return json.dumps({"data": [{"id": model} for model in models]}).encode()
        def close(self):
            pass
    monkeypatch.setattr(server, "_UnixHTTPConnection", Connection)
    assert server._gateway_ready("openai-codex/gpt-5.2") is ready


def test_proxy_gateway_destination_never_resolves_tcp(monkeypatch):
    monkeypatch.setenv("ALLOW_HOST", "localhost")
    monkeypatch.setenv("ALLOW_PORT", "14000")
    monkeypatch.setenv("GATEWAY_UNIX_SOCKET", server.GATEWAY_SOCKET)
    monkeypatch.setattr(proxy.socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS must not be used"))
    assert proxy._destination("localhost", 14000) == ("localhost", 14000)
    with pytest.raises(ValueError):
        proxy._destination("localhost", 18765)
    with pytest.raises(ValueError):
        proxy._destination("example.com", 14000)


def test_gateway_proxy_has_no_external_network_or_credential_mount(monkeypatch):
    class Engine:
        def inspect_container(self, container_id):
            return {"Mounts": [{"Destination": server.GATEWAY_SOCKET_ROOT, "Source": "/host/omp-relay"}]}
    monkeypatch.setattr(server, "DockerEngine", Engine)
    original_stat = server.os.stat
    def relay_stat(path, *args, **kwargs):
        if path == server.GATEWAY_SOCKET_ROOT:
            return SimpleNamespace(st_gid=1000)
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(server.os, "stat", relay_stat)
    job = payload()
    config = server._proxy_container_config("proxy", {server.RUN_ID_LABEL: job["run_id"]}, ("localhost", 14000))
    host = config["HostConfig"]
    assert host["NetworkMode"] == "ao-run-891e3b1574084d3991afff0ea5a06d18"
    assert "ExtraHosts" not in host
    assert host["CapDrop"] == ["ALL"]
    assert host["Mounts"] == [{"Type": "bind", "Source": "/host/omp-relay", "Target": "/omp-relay",
                               "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"}}]
    assert not any("KEY=" in entry for entry in config["Env"])
