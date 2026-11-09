from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import logging
import math
import os
import re
import socket
import ssl
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

LOG = logging.getLogger("agent_office.runtime_launcher")
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_DOCKER_RESPONSE_BYTES = 9 * 1024 * 1024
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_RUNS = 2
MAX_DURATION_SECONDS = 3600
MAX_INFERENCE_TURNS = 24
MAX_RESULT_BYTES = 1_048_576
MAX_PLANNING_MESSAGES = 64
MAX_PLANNING_MESSAGE_BYTES = 32 * 1024
MAX_PLANNING_PROMPT_BYTES = 128 * 1024
MAX_PLANNING_RESPONSE_BYTES = 256 * 1024
MAX_PLANNING_TOKENS = 4096
MAX_PLANNING_BODY_BYTES = 256 * 1024
PLANNING_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS = (
    "http://localhost:20128/proxy/v1,http://127.0.0.1:20128/proxy/v1,"
    "http://localhost:20128/v1,http://127.0.0.1:20128/v1"
)
_DNS_RESOLUTION_LOCK = threading.Lock()
_DNS_RESOLUTION_THREAD: threading.Thread | None = None
WORKER_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
WORKER_NANO_CPUS = 1_000_000_000
WORKER_PIDS_LIMIT = 128
GATEWAY_ENDPOINT = "http://localhost:14000/v1"
GATEWAY_SOCKET_ROOT = "/omp-relay"
GATEWAY_SOCKET = GATEWAY_SOCKET_ROOT + "/gateway.sock"

PROXY_NETWORK = os.environ.get("RUNTIME_EGRESS_NETWORK", "agent-office-runtime-egress")
WORKSPACE_CONTAINER_ROOT = os.environ.get("WORKSPACE_ROOT", "/workspace-root")
ALLOWED_TOOLS = {"list_files", "read_file", "write_file"}
SAFE_WORKER_FAILURE_REASONS = {
    "unavailable",
    "Model Endpoint configuration is unavailable",
    "Run tool configuration is invalid",
    "Model parameters are invalid",
    "workspace path is invalid",
    "workspace root cannot be a symlink",
    "workspace path cannot traverse a symlink",
    "workspace path escaped its root",
    "workspace tool allowlist is invalid",
    "Codex gateway setup failed",
    "Codex provider setup failed before HTTP request",
    "Codex upstream request failed before HTTP response",
    "Codex upstream transport configuration invalid",
    "Codex upstream transport timed out",
    "Codex upstream TLS connection failed",
    "Codex upstream network connection failed",
    "Codex upstream transport failed",
    "Codex provider response failed",
}
RUNNER_ID_LABEL = "agent-office.runner-id"
RUN_ID_LABEL = "agent-office.run-id"
ROLE_LABEL = "agent-office.role"
OWNER_LABEL = "agent-office.runtime"
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z", re.I)


class LauncherError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _reject_nonfinite_json_constant(_: str) -> None:
    raise ValueError("Planning response contains a non-finite JSON number")


def _parse_finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Planning response contains a non-finite JSON number")
    return number


class EngineError(RuntimeError):
    def __init__(self, status_code: int, message: str = "Docker Engine request failed") -> None:
        super().__init__(message)
        self.status_code = status_code


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(self._socket_path)
        except Exception:
            connection.close()
            raise
        self.sock = connection


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to an already-validated public DNS answer."""

    def __init__(
        self, hostname: str, address: str, port: int, timeout: float, deadline: float
    ) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address
        self._server_hostname = hostname
        self._deadline = deadline

    def connect(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("planning deadline exceeded")
        connection = socket.create_connection(
            (self._pinned_address, self.port), timeout=remaining
        )
        self.sock = connection
        try:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("planning deadline exceeded")
            connection.settimeout(remaining)
            self.sock = self._context.wrap_socket(
                connection, server_hostname=self._server_hostname
            )
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("planning deadline exceeded")
            self.sock.settimeout(remaining)
        except BaseException:
            failed_socket = self.sock
            self.sock = None
            if failed_socket is not None:
                try:
                    failed_socket.close()
                except OSError:
                    pass
            connection.close()
            raise

    def send(self, data: bytes) -> None:
        if time.monotonic() >= self._deadline:
            raise TimeoutError("planning deadline exceeded")
        super().send(data)


class DockerEngine:
    def __init__(self) -> None:
        self._socket_path = os.environ.get("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
        self._api_prefix = os.environ.get("DOCKER_API_PREFIX", "")

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        response_limit: int = MAX_DOCKER_RESPONSE_BYTES,
        accepted: tuple[int, ...] = (200,),
    ) -> object:
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        connection = _UnixHTTPConnection(self._socket_path, timeout=35)
        try:
            connection.request(method, self._api_prefix + path, body=encoded, headers=headers)
            response = connection.getresponse()
            data = response.read(response_limit + 1)
            if len(data) > response_limit:
                raise EngineError(502, "Docker Engine response exceeded size limit")
            if response.status not in accepted:
                raise EngineError(response.status)
            if not data:
                return None
            if "application/json" in response.getheader("Content-Type", ""):
                return json.loads(data)
            return data
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
            raise EngineError(503) from error
        finally:
            connection.close()

    def inspect_self_workspace_root(self) -> str:
        container_id = socket.gethostname()
        inspection = self.request(
            "GET", f"/containers/{quote(container_id, safe='')}/json"
        )
        mounts = inspection.get("Mounts") if isinstance(inspection, dict) else None
        if not isinstance(mounts, list):
            raise EngineError(503, "Launcher workspace mount is unavailable")
        for mount in mounts:
            if (
                isinstance(mount, dict)
                and mount.get("Destination") == WORKSPACE_CONTAINER_ROOT
                and isinstance(mount.get("Source"), str)
                and os.path.isabs(mount["Source"])
            ):
                return os.path.normpath(mount["Source"])
        raise EngineError(503, "Launcher workspace mount is unavailable")

    def inspect_container(self, container_id: str) -> dict[str, object]:
        result = self.request(
            "GET", f"/containers/{quote(container_id, safe='')}/json"
        )
        if not isinstance(result, dict):
            raise EngineError(502, "Docker container inspection is invalid")
        return result

    def list_owned_workers(self) -> list[dict[str, object]]:
        filters = json.dumps(
            {"label": [f"{OWNER_LABEL}=true", f"{ROLE_LABEL}=worker"]},
            separators=(",", ":"),
        )
        result = self.request(
            "GET", "/containers/json?all=1&filters=" + quote(filters, safe="")
        )
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise EngineError(502, "Docker worker list is invalid")
        return result

    def list_owned_proxies(self) -> list[dict[str, object]]:
        filters = json.dumps(
            {"label": [f"{OWNER_LABEL}=true", f"{ROLE_LABEL}=proxy"]},
            separators=(",", ":"),
        )
        result = self.request(
            "GET", "/containers/json?all=1&filters=" + quote(filters, safe="")
        )
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise EngineError(502, "Docker proxy list is invalid")
        return result

    def list_owned_role(self, run_id: str, role: str) -> list[dict[str, object]]:
        filters = json.dumps(
            {
                "label": [
                    f"{OWNER_LABEL}=true",
                    f"{RUN_ID_LABEL}={run_id}",
                    f"{ROLE_LABEL}={role}",
                ]
            },
            separators=(",", ":"),
        )
        result = self.request(
            "GET", "/containers/json?all=1&filters=" + quote(filters, safe="")
        )
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise EngineError(502, "Docker container list is invalid")
        return result


    def create_network(self, name: str, labels: dict[str, str]) -> dict[str, object]:
        result = self.request(
            "POST",
            "/networks/create",
            {
                "Name": name,
                "Driver": "bridge",
                "Internal": True,
                "EnableIPv6": False,
                "Labels": labels,
            },
            accepted=(201,),
        )
        if not isinstance(result, dict) or not isinstance(result.get("Id"), str):
            raise EngineError(502, "Docker network creation failed")
        return result

    def connect_network(self, network_id: str, container_id: str) -> None:
        self.request(
            "POST",
            f"/networks/{quote(network_id, safe='')}/connect",
            {
                "Container": container_id,
                "EndpointConfig": {"Aliases": ["egress-proxy"]},
            },
            accepted=(200, 201),
        )

    def create_container(
        self, name: str, config: dict[str, object]
    ) -> dict[str, object]:
        result = self.request(
            "POST",
            "/containers/create?name=" + quote(name, safe=""),
            config,
            accepted=(201,),
        )
        if not isinstance(result, dict) or not isinstance(result.get("Id"), str):
            raise EngineError(502, "Docker container creation failed")
        return result

    def start_container(self, container_id: str) -> None:
        self.request(
            "POST",
            f"/containers/{quote(container_id, safe='')}/start",
            accepted=(204, 304),
        )

    def remove_container(self, container_id: str) -> None:
        self.request(
            "DELETE",
            f"/containers/{quote(container_id, safe='')}?force=0&v=1",
            accepted=(204, 404),
        )

    def remove_network(self, network_id: str) -> None:
        self.request(
            "DELETE", f"/networks/{quote(network_id, safe='')}", accepted=(204, 404)
        )

    def stop_container(self, container_id: str) -> None:
        self.request(
            "POST",
            f"/containers/{quote(container_id, safe='')}/stop?t=10",
            accepted=(204, 304, 404),
        )

    def kill_container(self, container_id: str) -> None:
        self.request(
            "POST",
            f"/containers/{quote(container_id, safe='')}/kill",
            accepted=(204, 409, 404),
        )

    def logs(self, container_id: str) -> bytes:
        result = self.request(
            "GET",
            f"/containers/{quote(container_id, safe='')}/logs?stdout=1&stderr=0&timestamps=0",
            response_limit=MAX_LOG_BYTES,
            accepted=(200,),
        )
        if not isinstance(result, bytes):
            raise EngineError(502, "Docker worker log stream is invalid")
        return result


def _gateway_key() -> str:
    # Scoped client key only; the launcher never receives broker credentials.
    value = os.environ.get("OMP_GATEWAY_KEY", "").strip()
    if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        return ""
    return value


def _gateway_ready(model_id: str | None = None) -> bool:
    key = _gateway_key()
    if not key:
        return False
    # A public model catalogue cannot serve as proof that the key is valid.
    anonymous = _UnixHTTPConnection(GATEWAY_SOCKET, timeout=1)
    try:
        anonymous.request("GET", "/v1/models", headers={"Host": "localhost:14000"})
        if anonymous.getresponse().status not in {401, 403}:
            return False
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        anonymous.close()
    connection = _UnixHTTPConnection(GATEWAY_SOCKET, timeout=2)
    try:
        connection.request("GET", "/v1/models", headers={"Host": "localhost:14000", "Authorization": "Bearer " + key})
        response = connection.getresponse()
        body = response.read(262145)
        if response.status != 200 or len(body) > 262144:
            return False
        data = json.loads(body).get("data")
        scoped = isinstance(data, list) and bool(data) and all(
            isinstance(item, dict) and isinstance(item.get("id"), str)
            and re.fullmatch(r"openai-codex/[A-Za-z0-9._-]+", item["id"])
            for item in data
        )
        if not scoped or (model_id is not None and not any(item["id"] == model_id for item in data)):
            return False
        # Stock OMP can retry uncertain inference requests. Admit only the
        # bounded gateway, whose authenticated health endpoint declares the
        # exact reviewed policy and rejects non-scoped client credentials.
        capability = _UnixHTTPConnection(GATEWAY_SOCKET, timeout=2)
        try:
            capability.request(
                "GET", "/healthz",
                headers={"Host": "localhost:14000", "Authorization": "Bearer " + key},
            )
            response = capability.getresponse()
            body = response.read(4097)
            if response.status != 200 or len(body) > 4096:
                return False
            health = json.loads(body)
            return (
                isinstance(health, dict)
                and health.get("policy") == "agent-office-codex-single-attempt-v1"
                and health.get("ompVersion") == "18.12.3"
                and health.get("maxUpstreamAttempts") == 1
                and health.get("websockets") is False
            )
        finally:
            capability.close()
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


class RuntimeLauncher:
    def __init__(self) -> None:
        self._engine = DockerEngine()
        self._token = os.environ.get("RUNTIME_LAUNCHER_TOKEN", "").strip()
        self._orchestration_token = os.environ.get("ORCHESTRATION_INFERENCE_TOKEN", "").strip()
        self._worker_image = os.environ.get("RUN_WORKER_IMAGE", "").strip()
        self._proxy_image = os.environ.get("RUNTIME_PROXY_IMAGE", "").strip()
        self._worker_uid, self._worker_gid = _worker_identity()
        self._model_key = os.environ.get("NINEROUTER_KEY", "").strip()
        self._lock = threading.RLock()
        self._planning_slots = threading.BoundedSemaphore(1)
        self._planning_gateway_active = False
        self._host_workspace_root: str | None = None
        self._orphans_reconciled = False
    def planning_chat(self, payload: object) -> object:
        request = _validate_planning_payload(payload)
        endpoint = request["endpoint"]
        credential = endpoint["credential_env"]
        if not self._planning_slots.acquire(blocking=False):
            raise LauncherError("Planning inference is busy", 429)
        deadline = time.monotonic() + PLANNING_REQUEST_TIMEOUT_SECONDS
        gateway_reserved = False
        try:
            if credential == "OMP_GATEWAY_KEY":
                with self._lock:
                    self._planning_gateway_active = True
                    gateway_reserved = True
                workers = self._engine.list_owned_workers()
                if any(
                    isinstance(row.get("Labels"), dict)
                    and row["Labels"].get("agent-office.provider") == "omp-gateway"
                    and row.get("State") == "running"
                    for row in workers
                ):
                    raise LauncherError("Codex account already has an active Run", 429)
                return _planning_gateway_request(
                    request, _gateway_key(), deadline=deadline
                )
            if credential == "NINEROUTER_KEY":
                return _planning_provider_request(
                    request, self._model_key, deadline=deadline
                )
            raise LauncherError("Planning endpoint credential is invalid")
        finally:
            if gateway_reserved:
                with self._lock:
                    self._planning_gateway_active = False
            self._planning_slots.release()

    def ready(self) -> dict[str, object]:
        if not self._token or not self._worker_image or not self._proxy_image:
            return {"status": "not_ready", "provider_ready": False, "gateway_ready": False}
        try:
            if self._host_workspace_root is None:
                self._host_workspace_root = self._engine.inspect_self_workspace_root()
            self._engine.request("GET", "/_ping", response_limit=256)
            with self._lock:
                if not self._orphans_reconciled:
                    self._reconcile_orphans()
                    self._orphans_reconciled = True
        except EngineError:
            return {"status": "not_ready", "provider_ready": False, "gateway_ready": False}
        return {"status": "ready", "provider_ready": bool(self._model_key),
                "gateway_ready": _gateway_ready()}

    def list_runs(self) -> list[dict[str, object]]:
        rows = self._engine.list_owned_workers()
        runs: list[dict[str, object]] = []
        for row in rows:
            container_id = row.get("Id")
            labels = row.get("Labels")
            if not isinstance(container_id, str) or not isinstance(labels, dict):
                continue
            run_id = labels.get(RUN_ID_LABEL)
            runner_id = labels.get(RUNNER_ID_LABEL)
            if not isinstance(run_id, str) or not isinstance(runner_id, str):
                continue
            try:
                inspection = self._engine.inspect_container(container_id)
            except EngineError:
                continue
            state = inspection.get("State")
            if not isinstance(state, dict):
                continue
            runs.append(
                {
                    "run_id": run_id,
                    "runner_id": runner_id,
                    "status": "running" if state.get("Running") is True else "exited",
                    "started_at": _normalize_docker_time(state.get("StartedAt")),
                    "finished_at": _normalize_docker_time(state.get("FinishedAt")),
                }
            )
        return runs

    def _reconcile_orphans(self) -> None:
        workers = self._engine.list_owned_workers()
        active_run_ids = {
            labels.get(RUN_ID_LABEL)
            for row in workers
            if isinstance((labels := row.get("Labels")), dict)
            and labels.get(OWNER_LABEL) == "true"
            and labels.get(ROLE_LABEL) == "worker"
            and isinstance(labels.get(RUN_ID_LABEL), str)
            and _UUID_RE.fullmatch(labels[RUN_ID_LABEL])
        }
        orphaned_run_ids: set[str] = set()
        for row in self._engine.list_owned_proxies():
            labels = row.get("Labels")
            if (
                isinstance(labels, dict)
                and labels.get(OWNER_LABEL) == "true"
                and labels.get(ROLE_LABEL) == "proxy"
                and isinstance(labels.get(RUN_ID_LABEL), str)
                and _UUID_RE.fullmatch(labels[RUN_ID_LABEL])
            ):
                orphaned_run_ids.add(labels[RUN_ID_LABEL])
        networks = self._engine.request("GET", "/networks")
        if not isinstance(networks, list):
            raise EngineError(502, "Docker network list is invalid")
        for network in networks:
            labels = network.get("Labels") if isinstance(network, dict) else None
            if (
                isinstance(labels, dict)
                and labels.get(OWNER_LABEL) == "true"
                and labels.get(ROLE_LABEL) == "run-network"
                and isinstance(labels.get(RUN_ID_LABEL), str)
                and _UUID_RE.fullmatch(labels[RUN_ID_LABEL])
            ):
                orphaned_run_ids.add(labels[RUN_ID_LABEL])
        for run_id in sorted(orphaned_run_ids - active_run_ids):
            self.remove_run(run_id)

    def start_run(self, payload: object) -> dict[str, object]:
        runner_id, run_id, workspace_path, job, target = _validate_start_payload(payload)
        credential = job["configuration_snapshot"]["model_endpoint"]["credential_env"]
        if (credential == "OMP_GATEWAY_KEY" and not _gateway_ready(
            job["configuration_snapshot"]["agent_definition"]["configuration"]["model_id"]
        )) or (
            credential == "NINEROUTER_KEY" and not self._model_key
        ):
            raise LauncherError("Selected Model Endpoint is unavailable", 503)
        if self._host_workspace_root is None:
            self._host_workspace_root = self._engine.inspect_self_workspace_root()
        host_root = self._host_workspace_root
        bind_path, worker_workspace = _prepare_workspace(
            WORKSPACE_CONTAINER_ROOT,
            host_root,
            workspace_path,
            self._worker_uid,
            self._worker_gid,
        )
        labels = {
            OWNER_LABEL: "true",
            RUNNER_ID_LABEL: runner_id,
            RUN_ID_LABEL: run_id,
        }
        if credential == "OMP_GATEWAY_KEY":
            labels["agent-office.provider"] = "omp-gateway"
        network_name = "ao-run-" + UUID(run_id).hex
        worker_name = "ao-worker-" + UUID(run_id).hex
        proxy_name = "ao-proxy-" + UUID(run_id).hex
        with self._lock:
            if credential == "OMP_GATEWAY_KEY" and self._planning_gateway_active:
                raise LauncherError("Codex account is reserved for workflow planning", 429)
            existing = self._engine.list_owned_role(run_id, "worker")
            if existing:
                container_id = existing[0].get("Id")
                labels_existing = existing[0].get("Labels")
                if (
                    not isinstance(container_id, str)
                    or not isinstance(labels_existing, dict)
                    or labels_existing.get(RUNNER_ID_LABEL) != runner_id
                ):
                    raise LauncherError("Run is already owned by another Runner", 409)
                inspection = self._engine.inspect_container(container_id)
                state = inspection.get("State", {})
                return {
                    "run_id": run_id,
                    "status": "running" if state.get("Running") else "exited",
                    "started_at": _normalize_docker_time(state.get("StartedAt")),
                }
            active = 0
            for row in self._engine.list_owned_workers():
                if row.get("State") == "running":
                    active += 1
                    active_labels = row.get("Labels")
                    if (
                        credential == "OMP_GATEWAY_KEY"
                        and isinstance(active_labels, dict)
                        and active_labels.get("agent-office.provider") == "omp-gateway"
                    ):
                        raise LauncherError("Codex account already has an active Run", 429)
            if active >= MAX_ACTIVE_RUNS:
                raise LauncherError("Runtime host is at its Run concurrency limit", 429)
            self.remove_run(run_id)

            network_id: str | None = None
            proxy_id: str | None = None
            worker_id: str | None = None
            try:
                network = self._engine.create_network(
                    network_name, {**labels, ROLE_LABEL: "run-network"}
                )
                network_id = str(network["Id"])
                proxy_config = _proxy_container_config(
                    self._proxy_image,
                    {**labels, ROLE_LABEL: "proxy"},
                    target,
                )
                proxy = self._engine.create_container(proxy_name, proxy_config)
                proxy_id = str(proxy["Id"])
                self._engine.start_container(proxy_id)
                if credential != "OMP_GATEWAY_KEY":
                    self._engine.connect_network(network_id, proxy_id)
                worker_config = _worker_container_config(
                    self._worker_image,
                    {**labels, ROLE_LABEL: "worker"},
                    runner_id,
                    run_id,
                    job,
                    worker_workspace,
                    bind_path,
                    self._worker_uid,
                    self._worker_gid,
                )
                worker = self._engine.create_container(worker_name, worker_config)
                worker_id = str(worker["Id"])
                self._engine.start_container(worker_id)
                inspection = self._engine.inspect_container(worker_id)
                state = inspection.get("State")
                if not isinstance(state, dict) or state.get("Running") is not True:
                    raise EngineError(502, "Worker did not remain running")
                return {
                    "run_id": run_id,
                    "status": "running",
                    "started_at": _normalize_docker_time(state.get("StartedAt")),
                }
            except Exception:
                self._remove_partial(run_id, worker_id, proxy_id, network_id)
                raise

    def events(self, run_id: str, after_sequence: int) -> dict[str, object]:
        try:
            container_id = self._owned_container_id(run_id, "worker")
        except EngineError as error:
            LOG.error(
                "worker_container_lookup_failed run_id=%s status_code=%s",
                run_id,
                error.status_code,
            )
            raise
        if container_id is None:
            raise LauncherError("Run container was not found", 404)
        try:
            inspection = self._engine.inspect_container(container_id)
        except EngineError as error:
            LOG.error(
                "worker_inspection_failed run_id=%s status_code=%s",
                run_id,
                error.status_code,
            )
            raise
        state = inspection.get("State")
        if not isinstance(state, dict):
            LOG.error("worker_state_invalid run_id=%s", run_id)
            raise EngineError(502, "Docker worker state is invalid")
        try:
            raw_logs = self._engine.logs(container_id)
        except EngineError as error:
            # Docker may return 502 before a running worker emits its first stdout frame.
            if state.get("Running") is not True or error.status_code != 502:
                LOG.error(
                    "worker_logs_unavailable run_id=%s status_code=%s",
                    run_id,
                    error.status_code,
                )
                raise
            raw_logs = b""
        try:
            frames = _worker_frames(raw_logs)
        except EngineError as error:
            LOG.error(
                "worker_frame_parse_failed run_id=%s reason=%s",
                run_id,
                str(error),
            )
            raise
        events = [frame for frame in frames if frame["sequence"] > after_sequence][:128]
        return {
            "events": events,
            "status": "running" if state.get("Running") is True else "exited",
            "exit_code": state.get("ExitCode"),
            "started_at": _normalize_docker_time(state.get("StartedAt")),
            "finished_at": _normalize_docker_time(state.get("FinishedAt")),
        } | ({"upstream_stop_verified": False} if inspection.get("Config", {}).get("Labels", {}).get("agent-office.provider") == "omp-gateway" else {})

    def stop_run(self, run_id: str) -> dict[str, object]:
        container_id = self._owned_container_id(run_id, "worker")
        if container_id is None:
            return {"run_id": run_id, "verified": False, "status": "missing"}
        try:
            self._engine.stop_container(container_id)
        except EngineError:
            pass
        try:
            inspection = self._engine.inspect_container(container_id)
            gateway = inspection.get("Config", {}).get("Labels", {}).get("agent-office.provider") == "omp-gateway"
            state = inspection.get("State")
            if isinstance(state, dict) and state.get("Running") is True:
                self._engine.kill_container(container_id)
                state = self._engine.inspect_container(container_id).get("State")
            verified = isinstance(state, dict) and state.get("Running") is False
            return {
                "run_id": run_id,
                "verified": verified,
                "status": "stopped" if verified else "unknown",
            } | ({"upstream_stop_verified": False} if gateway else {})
        except EngineError:
            return {"run_id": run_id, "verified": False, "status": "unknown"}

    def remove_run(self, run_id: str) -> dict[str, object]:
        with self._lock:
            worker_id = self._owned_container_id(run_id, "worker")
            if worker_id is not None:
                state = self._engine.inspect_container(worker_id).get("State")
                if not isinstance(state, dict) or state.get("Running") is True:
                    raise LauncherError("Run container must be stopped before removal", 409)
                self._engine.remove_container(worker_id)
            proxy_id = self._owned_container_id(run_id, "proxy")
            if proxy_id is not None:
                try:
                    proxy_state = self._engine.inspect_container(proxy_id).get("State")
                    if isinstance(proxy_state, dict) and proxy_state.get("Running") is True:
                        self._engine.stop_container(proxy_id)
                    self._engine.remove_container(proxy_id)
                except EngineError:
                    pass
            network_name = "ao-run-" + UUID(run_id).hex
            networks = self._engine.request("GET", "/networks")
            if not isinstance(networks, list):
                raise EngineError(502, "Docker network list is invalid")
            for network in networks:
                if not isinstance(network, dict) or network.get("Name") != network_name:
                    continue
                labels = network.get("Labels")
                if (
                    not isinstance(labels, dict)
                    or labels.get(OWNER_LABEL) != "true"
                    or labels.get(RUN_ID_LABEL) != run_id
                    or labels.get(ROLE_LABEL) != "run-network"
                ):
                    raise LauncherError("Run network ownership labels are invalid", 409)
                network_id = network.get("Id")
                if isinstance(network_id, str):
                    self._engine.remove_network(network_id)
            return {"run_id": run_id, "removed": True}

    def _owned_container_id(self, run_id: str, role: str) -> str | None:
        rows = self._engine.list_owned_role(run_id, role)
        if not rows:
            return None
        if len(rows) > 1:
            raise EngineError(502, "Duplicate service-owned Run containers were found")
        labels = rows[0].get("Labels")
        container_id = rows[0].get("Id")
        if (
            not isinstance(labels, dict)
            or labels.get(OWNER_LABEL) != "true"
            or labels.get(RUN_ID_LABEL) != run_id
            or labels.get(ROLE_LABEL) != role
            or not isinstance(container_id, str)
        ):
            raise EngineError(502, "Container ownership labels are invalid")
        return container_id

    def _remove_partial(
        self,
        run_id: str,
        worker_id: str | None,
        proxy_id: str | None,
        network_id: str | None,
    ) -> None:
        for container_id in (worker_id, proxy_id):
            if container_id is None:
                continue
            try:
                self._engine.stop_container(container_id)
            except EngineError:
                pass
            try:
                self._engine.remove_container(container_id)
            except EngineError:
                pass
        if network_id is not None:
            try:
                self._engine.remove_network(network_id)
            except EngineError:
                pass


def _normalize_docker_time(value: object) -> str | None:
    if not isinstance(value, str) or value.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat()


def _validate_start_payload(payload: object) -> tuple[str, str, str, dict[str, object], tuple[str, int]]:
    if not isinstance(payload, dict) or set(payload) != {
        "runner_id",
        "run_id",
        "configuration_snapshot",
        "workspace_path",
        "allowed_tools",
        "max_duration_seconds",
        "max_inference_turns",
        "max_tool_calls",
    }:
        raise LauncherError("Runtime launch request is invalid")
    runner_id, run_id = payload.get("runner_id"), payload.get("run_id")
    if not isinstance(runner_id, str) or not _UUID_RE.fullmatch(runner_id):
        raise LauncherError("Runner ID is invalid")
    if not isinstance(run_id, str) or not _UUID_RE.fullmatch(run_id):
        raise LauncherError("Run ID is invalid")
    workspace_path = payload.get("workspace_path")
    if not isinstance(workspace_path, str) or not _safe_relative_path(workspace_path):
        raise LauncherError("Run workspace path is invalid")
    snapshot = payload.get("configuration_snapshot")
    tools = payload.get("allowed_tools")
    timeout = payload.get("max_duration_seconds")
    turns = payload.get("max_inference_turns")
    tool_calls = payload.get("max_tool_calls")
    if not isinstance(snapshot, dict) or not isinstance(tools, list):
        raise LauncherError("Run execution configuration is invalid")
    if any(not isinstance(tool, str) or tool not in ALLOWED_TOOLS for tool in tools):
        raise LauncherError("Run tool allowlist is invalid")
    if len(set(tools)) != len(tools) or not tools:
        raise LauncherError("Run tool allowlist is invalid")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_DURATION_SECONDS:
        raise LauncherError("Run execution timeout is invalid")
    if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= MAX_INFERENCE_TURNS:
        raise LauncherError("Run inference limit is invalid")
    if not isinstance(tool_calls, int) or isinstance(tool_calls, bool) or not 1 <= tool_calls <= 50:
        raise LauncherError("Run tool-call limit is invalid")
    endpoint = snapshot.get("model_endpoint")
    if not isinstance(endpoint, dict) or endpoint.get("credential_env") not in {"NINEROUTER_KEY", "OMP_GATEWAY_KEY"}:
        raise LauncherError("Selected Model Endpoint credential is invalid")
    endpoint_url = endpoint.get("endpoint_url")
    if endpoint["credential_env"] == "OMP_GATEWAY_KEY":
        definition = snapshot.get("agent_definition")
        configuration = definition.get("configuration") if isinstance(definition, dict) else None
        if (endpoint_url != GATEWAY_ENDPOINT or not isinstance(configuration, dict)
            or configuration.get("runtime_id") != "langchain"
            or not isinstance(configuration.get("model_id"), str)
            or not re.fullmatch(r"openai-codex/[A-Za-z0-9._-]+", configuration["model_id"])
            or configuration.get("route") is not None):
            raise LauncherError("Selected gateway configuration is invalid")
        target = ("localhost", 14000)
    else:
        target = _provider_target(endpoint_url)
    job = dict(payload)
    job["workspace_path"] = "."
    return runner_id, run_id, workspace_path, job, target


def _provider_target(endpoint_url: object) -> tuple[str, int]:
    if not isinstance(endpoint_url, str) or len(endpoint_url) > 2048:
        raise LauncherError("Selected Model Endpoint is invalid")
    parsed = urlsplit(endpoint_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise LauncherError("Selected Model Endpoint is invalid")
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise LauncherError("Selected Model Endpoint is invalid") from error
    if host in {"localhost", "127.0.0.1"}:
        if parsed.scheme != "http" or port != 20128:
            raise LauncherError("Local Model Endpoint is not allowed")
        return "host.docker.internal", port
    if parsed.scheme != "https":
        raise LauncherError("Model Endpoint must use HTTPS")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host, port
    if not address.is_global:
        raise LauncherError("Private Model Endpoint is not allowed")
    return host, port


def _validate_planning_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) not in (
        {"endpoint", "messages", "max_tokens"},
        {"endpoint", "messages", "response_format", "max_tokens"},
    ):
        raise LauncherError("Planning request is invalid")
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, dict) or set(endpoint) != {"credential_env", "endpoint_url", "model_id"}:
        raise LauncherError("Planning endpoint is invalid")
    credential = endpoint.get("credential_env")
    endpoint_url = endpoint.get("endpoint_url")
    model_id = endpoint.get("model_id")
    if credential not in {"OMP_GATEWAY_KEY", "NINEROUTER_KEY"} or not isinstance(endpoint_url, str) or not isinstance(model_id, str):
        raise LauncherError("Planning endpoint is invalid")
    model_pattern = r"[A-Za-z0-9._:/+-]{1,200}" if credential == "NINEROUTER_KEY" else r"[A-Za-z0-9._:/-]{1,200}"
    if len(endpoint_url) > 2048 or len(model_id) > 200 or not re.fullmatch(model_pattern, model_id):
        raise LauncherError("Planning model is invalid")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_PLANNING_MESSAGES:
        raise LauncherError("Planning messages are invalid")
    total = 0
    normalized_messages: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise LauncherError("Planning message is invalid")
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "user"} or not isinstance(content, str):
            raise LauncherError("Planning message is invalid")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise LauncherError("Planning message is invalid") from error
        if not encoded or len(encoded) > MAX_PLANNING_MESSAGE_BYTES:
            raise LauncherError("Planning message is invalid")
        total += len(encoded)
        normalized_messages.append({"role": role, "content": content})
    if total > MAX_PLANNING_PROMPT_BYTES:
        raise LauncherError("Planning prompt exceeded size limit", 413)
    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= MAX_PLANNING_TOKENS:
        raise LauncherError("Planning token limit is invalid")
    response_format = payload.get("response_format")
    if response_format is not None and response_format != {"type": "json_object"}:
        raise LauncherError("Planning response format is invalid")
    parsed = {
        "endpoint": {"credential_env": credential, "endpoint_url": endpoint_url, "model_id": model_id},
        "messages": normalized_messages,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        parsed["response_format"] = response_format
    return parsed


def _planning_gateway_request(
    request: dict[str, object], key: str, *, deadline: float | None = None
) -> object:
    if not key:
        raise LauncherError("Planning endpoint is unavailable", 503)
    endpoint = request["endpoint"]
    assert isinstance(endpoint, dict)
    if endpoint["endpoint_url"] != GATEWAY_ENDPOINT or not re.fullmatch(r"openai-codex/[A-Za-z0-9._-]+", endpoint["model_id"]):
        raise LauncherError("Planning gateway endpoint is invalid")
    return _planning_http_request(
        _UnixHTTPConnection(
            GATEWAY_SOCKET, timeout=PLANNING_REQUEST_TIMEOUT_SECONDS
        ),
        "/v1/chat/completions",
        request,
        {"Host": "localhost:14000", "Authorization": "Bearer " + key},
        secret=key,
        deadline=deadline,
    )


def _planning_provider_endpoint(value: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError("endpoint")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise LauncherError("Planning provider endpoint is invalid", 503) from error
    return (
        parsed.scheme,
        parsed.hostname.lower().rstrip("."),
        port,
        parsed.path.rstrip("/") or "/",
    )


def _planning_provider_allowed_endpoints() -> set[tuple[str, str, int, str]]:
    configured = os.environ.get(
        "ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS",
        DEFAULT_ORCHESTRATION_PLANNING_ALLOWED_ENDPOINTS,
    )
    endpoints: set[tuple[str, str, int, str]] = set()
    for value in configured.split(","):
        endpoint = value.strip()
        if endpoint:
            endpoints.add(_planning_provider_endpoint(endpoint))
    return endpoints


def _resolve_provider_addresses(host: str, port: int, deadline: float) -> list[tuple]:
    global _DNS_RESOLUTION_THREAD
    completed = threading.Event()
    result: list[tuple] = []
    failure: list[Exception] = []

    def resolve() -> None:
        try:
            result.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as error:
            failure.append(error)
        finally:
            completed.set()

    with _DNS_RESOLUTION_LOCK:
        if _DNS_RESOLUTION_THREAD is not None and _DNS_RESOLUTION_THREAD.is_alive():
            raise LauncherError("Planning provider resolution is busy", 503)
        _DNS_RESOLUTION_THREAD = threading.Thread(
            target=resolve, name="planning-dns-resolution", daemon=True
        )
        _DNS_RESOLUTION_THREAD.start()
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not completed.wait(remaining):
        raise LauncherError("Planning provider resolution timed out", 504)
    if failure:
        raise LauncherError("Planning provider endpoint is unavailable", 503) from failure[0]
    return result


def _planning_provider_request(
    request: dict[str, object], key: str, *, deadline: float | None = None
) -> object:
    if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise LauncherError("Planning endpoint is unavailable", 503)
    endpoint = request["endpoint"]
    assert isinstance(endpoint, dict)
    endpoint_url = endpoint["endpoint_url"]
    parsed_endpoint = _planning_provider_endpoint(endpoint_url)
    if parsed_endpoint not in _planning_provider_allowed_endpoints():
        raise LauncherError("Planning provider endpoint is not authorized")
    scheme, host, port, base_path = parsed_endpoint
    if deadline is None:
        deadline = time.monotonic() + PLANNING_REQUEST_TIMEOUT_SECONDS
    path = (base_path.rstrip("/") or "") + "/chat/completions"
    if scheme == "http":
        if host not in {"localhost", "127.0.0.1"} or port != 20128:
            raise LauncherError("Planning provider endpoint is not allowed")
        return _planning_http_request(
            http.client.HTTPConnection(
                "host.docker.internal",
                20128,
                timeout=PLANNING_REQUEST_TIMEOUT_SECONDS,
            ),
            path,
            request,
            {"Host": "host.docker.internal:20128", "Authorization": "Bearer " + key},
            secret=key,
            deadline=deadline,
        )
    if scheme != "https":
        raise LauncherError("Planning provider endpoint is invalid")
    answers = _resolve_provider_addresses(host, port, deadline)
    addresses = {item[4][0] for item in answers if item[4]}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise LauncherError("Planning provider endpoint is not public")
    host_header = f"[{host}]:{port}" if ":" in host else (f"{host}:{port}" if port != 443 else host)
    return _planning_http_request(
        _PinnedHTTPSConnection(
            host,
            sorted(addresses)[0],
            port,
            timeout=PLANNING_REQUEST_TIMEOUT_SECONDS,
            deadline=deadline,
        ),
        path,
        request,
        {"Host": host_header, "Authorization": "Bearer " + key},
        secret=key,
        deadline=deadline,
    )


def _set_planning_timeout(
    connection: http.client.HTTPConnection, response: object, deadline: float
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LauncherError("Planning inference timed out", 504)
    connection.timeout = remaining
    sock = getattr(connection, "sock", None)
    if sock is None:
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(remaining)


def _planning_response(result: object, secret: str) -> dict[str, object]:
    choices = result.get("choices") if isinstance(result, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LauncherError("Planning response is invalid", 502)
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LauncherError("Planning response is invalid", 502) from error
    if secret:
        try:
            parsed = json.loads(
                content,
                parse_constant=_reject_nonfinite_json_constant,
                parse_float=_parse_finite_json_float,
            )

            def redact(value: object) -> object:
                if isinstance(value, str):
                    sanitized_value = value.replace(secret, "[REDACTED]")
                    if secret in sanitized_value:
                        raise ValueError("provider credential remains in planning response")
                    return sanitized_value
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if isinstance(value, dict):
                    sanitized: dict[str, object] = {}
                    for key, item in value.items():
                        sanitized_key = key.replace(secret, "[REDACTED]")
                        if secret in sanitized_key or sanitized_key in sanitized:
                            raise ValueError("redacted planning keys are invalid")
                        sanitized[sanitized_key] = redact(item)
                    return sanitized
                if value is None or isinstance(value, (bool, int, float)):
                    if secret in json.dumps(value, allow_nan=False):
                        raise ValueError("provider credential remains in planning response")
                return value

            if not isinstance(parsed, dict):
                raise ValueError("planning response is not an object")
            sanitized_content = json.dumps(
                redact(parsed),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            encoded_content = sanitized_content.encode("utf-8")
            if len(encoded_content) > MAX_PLANNING_RESPONSE_BYTES:
                raise ValueError("sanitized planning response is invalid")
            content = sanitized_content
        except (ValueError, RecursionError) as error:
            raise LauncherError("Planning response is invalid", 502) from error
    return {"choices": [{"message": {"content": content}}]}


def _planning_http_request(
    connection: http.client.HTTPConnection,
    path: str,
    request: dict[str, object],
    headers: dict[str, str],
    *,
    secret: str = "",
    deadline: float | None = None,
) -> object:
    endpoint = request["endpoint"]
    assert isinstance(endpoint, dict)
    body: dict[str, object] = {
        "model": endpoint["model_id"],
        "messages": request["messages"],
        "max_tokens": request["max_tokens"],
    }
    if "response_format" in request:
        body["response_format"] = request["response_format"]
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if deadline is None:
        deadline = time.monotonic() + PLANNING_REQUEST_TIMEOUT_SECONDS
    deadline_expired = threading.Event()
    response: object | None = None
    active_socket: object | None = None

    def interrupt_expired_request() -> None:
        deadline_expired.set()

        def shutdown(sock: object | None) -> bool:
            if sock is None:
                return False
            try:
                sock.shutdown(socket.SHUT_RDWR)
                return True
            except OSError:
                return False

        connection_socket = getattr(connection, "sock", None)
        if shutdown(connection_socket):
            return
        if active_socket is not connection_socket and shutdown(active_socket):
            return
        if response is not None:
            raw = getattr(getattr(response, "fp", None), "raw", None)
            response_socket = getattr(raw, "_sock", None)
            if (
                response_socket is not connection_socket
                and response_socket is not active_socket
            ):
                shutdown(response_socket)

    def check_deadline() -> None:
        if deadline_expired.is_set() or time.monotonic() >= deadline:
            raise LauncherError("Planning inference timed out", 504)

    deadline_timer = threading.Timer(
        max(0.0, deadline - time.monotonic()), interrupt_expired_request
    )
    deadline_timer.daemon = True
    try:
        deadline_timer.start()
        _set_planning_timeout(connection, None, deadline)
        connection.request(
            "POST",
            path,
            body=encoded,
            headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        )
        active_socket = getattr(connection, "sock", None)
        _set_planning_timeout(connection, None, deadline)
        response = connection.getresponse()
        check_deadline()
        if response.status != 200:
            raise LauncherError("Planning inference failed", 502)
        data = bytearray()
        reader = getattr(response, "read1", None)
        if reader is None:
            _set_planning_timeout(connection, response, deadline)
            data.extend(response.read(MAX_PLANNING_RESPONSE_BYTES + 1))
            check_deadline()
        else:
            while len(data) <= MAX_PLANNING_RESPONSE_BYTES:
                _set_planning_timeout(connection, response, deadline)
                chunk = reader(min(8192, MAX_PLANNING_RESPONSE_BYTES + 1 - len(data)))
                check_deadline()
                if not chunk:
                    break
                data.extend(chunk)
        if len(data) > MAX_PLANNING_RESPONSE_BYTES:
            raise LauncherError("Planning response exceeded size limit", 502)
        try:
            result = json.loads(
                data,
                parse_constant=_reject_nonfinite_json_constant,
                parse_float=_parse_finite_json_float,
            )
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise LauncherError("Planning response is invalid", 502) from error
        sanitized = _planning_response(result, secret)
        check_deadline()
        return sanitized
    except LauncherError as error:
        if deadline_expired.is_set() or time.monotonic() >= deadline:
            raise LauncherError("Planning inference timed out", 504) from error
        raise
    except TimeoutError as error:
        raise LauncherError("Planning inference timed out", 504) from error
    except (OSError, ValueError, http.client.HTTPException) as error:
        if deadline_expired.is_set() or time.monotonic() >= deadline:
            raise LauncherError("Planning inference timed out", 504) from error
        raise LauncherError("Planning inference unavailable", 503) from error
    finally:
        deadline_timer.cancel()
        if response is not None:
            close_response = getattr(response, "close", None)
            if callable(close_response):
                try:
                    close_response()
                except OSError:
                    pass
        connection.close()


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and len(value) <= 512
        and not path.is_absolute()
        and "\\" not in value
        and "\x00" not in value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _worker_identity() -> tuple[int, int]:
    values = []
    for key in ("RUN_WORKER_UID", "RUN_WORKER_GID"):
        raw = os.environ.get(key, "1000").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 2_147_483_647:
            raise RuntimeError(f"{key} must be a non-root numeric identity")
        values.append(int(raw))
    return values[0], values[1]


def _prepare_workspace(
    container_root: str,
    host_root: str,
    relative: str,
    worker_uid: int,
    worker_gid: int,
) -> tuple[str, str]:
    root = os.path.realpath(container_root)
    host = os.path.realpath(host_root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    current_fd = root_fd
    parts = relative.split("/")
    try:
        for part in parts:
            try:
                child_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                os.mkdir(part, mode=0o770, dir_fd=current_fd)
                child_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current_fd,
                )
                os.fchown(child_fd, worker_uid, worker_gid)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd
    except OSError as error:
        raise LauncherError("Run workspace is unavailable or contains a symlink", 409) from error
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)
    bind_path = os.path.normpath(os.path.join(host, *parts))
    if os.path.commonpath((host, bind_path)) != host:
        raise LauncherError("Run workspace escaped its root")
    return bind_path, "/workspace"


def _base_labels(labels: dict[str, str]) -> dict[str, str]:
    return {OWNER_LABEL: "true", **labels}


def _logging_config() -> dict[str, object]:
    return {"Type": "json-file", "Config": {"max-size": "4m", "max-file": "2"}}


def _security_host_config(
    *, memory: int, nano_cpus: int, pids_limit: int, network_mode: str
) -> dict[str, object]:
    return {
        "NetworkMode": network_mode,
        "Memory": memory,
        "NanoCpus": nano_cpus,
        "PidsLimit": pids_limit,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "ReadonlyRootfs": True,
        "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=32m,mode=1777"},
        "LogConfig": _logging_config(),
        "RestartPolicy": {"Name": "no"},
    }


def _proxy_container_config(
    image: str,
    labels: dict[str, str],
    target: tuple[str, int],
) -> dict[str, object]:
    host, port = target
    config: dict[str, object] = {
        "Image": image,
        "Cmd": ["python", "/app/proxy.py"],
        "User": "10001:10001",
        "Env": [f"ALLOW_HOST={host}", f"ALLOW_PORT={port}", "PROXY_LISTEN_PORT=3128"],
        "Labels": _base_labels(labels),
        "ExposedPorts": {"3128/tcp": {}},
        "HostConfig": _security_host_config(
            memory=256 * 1024 * 1024,
            nano_cpus=250_000_000,
            pids_limit=64,
            network_mode=PROXY_NETWORK,
        )
        | {"ExtraHosts": ["host.docker.internal:host-gateway"]},
    }
    if target == ("localhost", 14000):
        # Resolve only our own mounted relay directory, never a payload-supplied path.
        engine = DockerEngine()
        inspection = engine.inspect_container(socket.gethostname())
        mounts = inspection.get("Mounts", [])
        source = next((m.get("Source") for m in mounts
                       if m.get("Destination") == GATEWAY_SOCKET_ROOT), None)
        if not isinstance(source, str) or not os.path.isabs(source):
            raise LauncherError("Gateway relay mount is unavailable", 503)
        config["Env"].append("GATEWAY_UNIX_SOCKET=" + GATEWAY_SOCKET)
        config["HostConfig"]["GroupAdd"] = [str(os.stat(GATEWAY_SOCKET_ROOT).st_gid)]
        config["HostConfig"]["Mounts"] = [{
            "Type": "bind", "Source": source, "Target": GATEWAY_SOCKET_ROOT,
            "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"},
        }]
        config["HostConfig"].pop("ExtraHosts", None)
        # Unlike an HTTPS provider proxy, the gateway proxy needs no IP egress.
        run_network = "ao-run-" + UUID(labels[RUN_ID_LABEL]).hex
        config["HostConfig"]["NetworkMode"] = run_network
        config["NetworkingConfig"] = {
            "EndpointsConfig": {run_network: {"Aliases": ["egress-proxy"]}}
        }
    return config


def _worker_container_config(
    image: str,
    labels: dict[str, str],
    runner_id: str,
    run_id: str,
    job: dict[str, object],
    worker_workspace: str,
    bind_path: str,
    worker_uid: int,
    worker_gid: int,
) -> dict[str, object]:
    encoded_job = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    if len(encoded_job.encode("utf-8")) > MAX_BODY_BYTES:
        raise LauncherError("Run execution snapshot is too large", 413)
    env = [
        "RUN_JOB_JSON=" + encoded_job,
        f"RUNNER_ID={runner_id}",
        f"RUN_ID={run_id}",
        "RUNNER_WORKSPACE_ROOT=" + worker_workspace,
        "RUNNER_STATE_ROOT=/tmp/run-state",
        "RUNTIME_PROXY_URL=http://egress-proxy:3128",
        "LOG_LEVEL=WARNING",
    ]
    credential = job["configuration_snapshot"]["model_endpoint"]["credential_env"]
    model_key = _gateway_key() if credential == "OMP_GATEWAY_KEY" else os.environ.get("NINEROUTER_KEY", "").strip()
    if not model_key:
        raise LauncherError("Selected Model Endpoint is unavailable", 503)
    env.append(credential + "=" + model_key)
    config: dict[str, object] = {
        "Image": image,
        "Cmd": ["worker"],
        "User": f"{worker_uid}:{worker_gid}",
        "WorkingDir": worker_workspace,
        "Env": env,
        "Labels": _base_labels(labels),
        "Tty": False,
        "HostConfig": _security_host_config(
            memory=WORKER_MEMORY_BYTES,
            nano_cpus=WORKER_NANO_CPUS,
            pids_limit=WORKER_PIDS_LIMIT,
            network_mode="ao-run-" + UUID(run_id).hex,
        )
        | {
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": bind_path,
                    "Target": worker_workspace,
                    "ReadOnly": False,
                    "BindOptions": {"Propagation": "rprivate"},
                }
            ]
        },
    }
    return config





def _docker_stdout(raw: bytes) -> bytes:
    output = bytearray()
    offset = 0
    while offset + 8 <= len(raw):
        stream_id = raw[offset]
        length = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        offset += 8
        end = offset + length
        if end > len(raw):
            raise EngineError(502, "Docker log frame is truncated")
        if stream_id == 1:
            output.extend(raw[offset:end])
        offset = end
    if offset != len(raw):
        raise EngineError(502, "Docker log framing is invalid")
    if len(output) > MAX_LOG_BYTES:
        raise EngineError(502, "Worker output exceeded size limit")
    return bytes(output)


def _worker_frames(raw_logs: bytes) -> list[dict[str, object]]:
    stdout = _docker_stdout(raw_logs)
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EngineError(502, "Worker output is not UTF-8") from error
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    frames: list[dict[str, object]] = []
    previous = 0
    started = False
    result_seen = False
    failure_seen = False
    for line in lines:
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as error:
            raise EngineError(502, "Worker protocol frame is invalid") from error
        if (
            not isinstance(frame, dict)
            or set(frame) != {"sequence", "kind", "payload"}
            or not isinstance(frame.get("kind"), str)
            or not isinstance(frame.get("sequence"), int)
            or isinstance(frame.get("sequence"), bool)
            or frame["sequence"] <= previous
            or frame.get("kind") not in {"started", "event", "result", "failure"}
            or not isinstance(frame.get("payload"), dict)
        ):
            raise EngineError(502, "Worker protocol frame is invalid")
        previous = frame["sequence"]
        payload = frame["payload"]
        if frame["kind"] == "started":
            if started or frames or payload:
                raise EngineError(502, "Worker startup frame is invalid")
            started = True
        elif not started:
            raise EngineError(502, "Worker activity preceded startup acknowledgement")
        elif result_seen or failure_seen:
            raise EngineError(502, "Worker output followed a terminal frame")
        elif frame["kind"] == "failure":
            error_type = payload.get("error_type")
            cause_type = payload.get("cause_type")
            cause_status = payload.get("cause_status")
            cause_reason = payload.get("cause_reason")
            if (
                set(payload)
                != {"error_type", "cause_type", "cause_status", "cause_reason"}
                or not isinstance(error_type, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type)
                or not isinstance(cause_type, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", cause_type)
                or (
                    cause_status != "unavailable"
                    and (
                        not isinstance(cause_status, int)
                        or isinstance(cause_status, bool)
                        or not 100 <= cause_status <= 599
                    )
                )
                or cause_reason not in SAFE_WORKER_FAILURE_REASONS
            ):
                raise EngineError(502, "Worker failure frame is invalid")
            failure_seen = True
        elif frame["kind"] == "event":
            event_type = payload.get("event_type")
            event_id = payload.get("event_id")
            occurred_at = payload.get("occurred_at")
            source_sequence = payload.get("source_sequence")
            tool_name = payload.get("tool_name")
            if (
                not isinstance(event_type, str)
                or event_type not in {
                    "run.tool_action",
                    "run.working",
                    "run.tool_started",
                    "run.reviewing",
                    "session.heartbeat",
                }
                or not isinstance(event_id, str)
                or not _UUID_RE.fullmatch(event_id)
                or not isinstance(occurred_at, str)
                or not isinstance(source_sequence, int)
                or isinstance(source_sequence, bool)
                or source_sequence < 1
                or set(payload)
                != (
                    {
                        "event_type",
                        "event_id",
                        "occurred_at",
                        "source_sequence",
                        "tool_name",
                    }
                    if event_type == "run.tool_action"
                    else {
                        "event_type",
                        "event_id",
                        "occurred_at",
                        "source_sequence",
                    }
                )
                or (
                    event_type == "run.tool_action"
                    and (
                        not isinstance(tool_name, str)
                        or tool_name not in ALLOWED_TOOLS
                    )
                )
            ):
                raise EngineError(502, "Worker activity frame is invalid")
            try:
                datetime.fromisoformat(occurred_at)
            except ValueError as error:
                raise EngineError(502, "Worker activity frame is invalid") from error
        else:
            result = payload.get("text")
            if (
                set(payload) != {"text"}
                or not isinstance(result, str)
                or len(result.encode("utf-8")) > MAX_RESULT_BYTES
            ):
                raise EngineError(502, "Worker result frame is invalid")
        frames.append(frame)
        if frame["kind"] == "result":
            result_seen = True
    return frames


class LauncherRequestHandler(BaseHTTPRequestHandler):
    server_version = ""
    sys_version = ""

    @property
    def launcher(self) -> RuntimeLauncher:
        return self.server.launcher  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args: object) -> None:
        LOG.info("launcher_http_request", extra={"request": args[0] if args else ""})

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        if path.path == "/health/ready":
            response = self.launcher.ready()
            self._send_json(200 if response["status"] == "ready" else 503, response)
            return
        if not self._authenticate():
            self._send_error(401, "unauthorized")
            return
        try:
            if path.path == "/internal/runs":
                self._send_json(200, {"runs": self.launcher.list_runs()})
                return
            match = re.fullmatch(r"/internal/runs/([0-9a-fA-F-]{36})/events", path.path)
            if match:
                run_id = _validated_path_uuid(match.group(1))
                values = parse_qs(path.query, strict_parsing=True)
                after_values = values.get("after_sequence", ["0"])
                if len(after_values) != 1:
                    raise LauncherError("Event cursor is invalid")
                try:
                    after = int(after_values[0])
                except ValueError as error:
                    raise LauncherError("Event cursor is invalid") from error
                if not 0 <= after <= 1_000_000:
                    raise LauncherError("Event cursor is invalid")
                self._send_json(200, self.launcher.events(run_id, after))
                return
            self._send_error(404, "not_found")
        except LauncherError as error:
            self._send_error(error.status_code, str(error))
        except EngineError as error:
            if path.path.startswith("/internal/runs/") and path.path.endswith("/events"):
                LOG.error(
                    "runtime_event_stream_failed run_id=%s status_code=%s",
                    path.path.split("/")[3],
                    error.status_code,
                )
            self._send_error(error.status_code, "runtime_engine_unavailable")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        authenticated = self._authenticate_orchestration() if path == "/internal/planning/chat-completions" else self._authenticate()
        if not authenticated:
            self._send_error(401, "unauthorized")
            return
        try:
            body = self._read_json_body(MAX_PLANNING_BODY_BYTES if path == "/internal/planning/chat-completions" else MAX_BODY_BYTES)
            if path == "/internal/runs":
                self._send_json(201, self.launcher.start_run(body))
                return
            if path == "/internal/planning/chat-completions":
                self._send_json(200, self.launcher.planning_chat(body))
                return
            match = re.fullmatch(r"/internal/runs/([0-9a-fA-F-]{36})/stop", path)
            if match:
                self._send_json(200, self.launcher.stop_run(_validated_path_uuid(match.group(1))))
                return
            self._send_error(404, "not_found")
        except LauncherError as error:
            self._send_error(error.status_code, str(error))
        except EngineError:
            self._send_error(503, "runtime_engine_unavailable")

    def do_DELETE(self) -> None:
        if not self._authenticate():
            self._send_error(401, "unauthorized")
            return
        match = re.fullmatch(r"/internal/runs/([0-9a-fA-F-]{36})", urlsplit(self.path).path)
        if not match:
            self._send_error(404, "not_found")
            return
        try:
            self._send_json(200, self.launcher.remove_run(_validated_path_uuid(match.group(1))))
        except LauncherError as error:
            self._send_error(error.status_code, str(error))
        except EngineError:
            self._send_error(503, "runtime_engine_unavailable")

    def _authenticate(self) -> bool:
        expected = self.launcher._token
        authorization = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(
            authorization, f"Bearer {expected}"
        )

    def _authenticate_orchestration(self) -> bool:
        expected = self.launcher._orchestration_token
        authorization = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(authorization, f"Bearer {expected}")

    def _read_json_body(self, limit: int = MAX_BODY_BYTES) -> object:
        if self.headers.get_content_type() != "application/json":
            raise LauncherError("JSON request body is required", 415)
        length = self.headers.get("Content-Length", "")
        try:
            size = int(length)
        except ValueError as error:
            raise LauncherError("Request body length is invalid", 411) from error
        if not 0 <= size <= limit:
            raise LauncherError("Request body exceeded size limit", 413)
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise LauncherError("Request body is incomplete", 400)
        try:
            return json.loads(raw, object_pairs_hook=_unique_object_pairs)
        except (json.JSONDecodeError, ValueError) as error:
            raise LauncherError("JSON request body is invalid") from error

    def _send_json(self, status: int, value: object) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, detail: str) -> None:
        self._send_json(status, {"detail": detail})


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _validated_path_uuid(value: str) -> str:
    if not _UUID_RE.fullmatch(value):
        raise LauncherError("Run ID is invalid")
    return str(UUID(value))


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    launcher = RuntimeLauncher()
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("RUNTIME_LAUNCHER_PORT", "8001"))), LauncherRequestHandler)
    server.daemon_threads = True
    server.launcher = launcher  # type: ignore[attr-defined]
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
