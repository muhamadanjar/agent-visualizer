from __future__ import annotations

import hmac
import http.client
import ipaddress
import json
import logging
import os
import re
import socket
import threading
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
WORKER_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
WORKER_NANO_CPUS = 1_000_000_000
WORKER_PIDS_LIMIT = 128
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


class RuntimeLauncher:
    def __init__(self) -> None:
        self._engine = DockerEngine()
        self._token = os.environ.get("RUNTIME_LAUNCHER_TOKEN", "").strip()
        self._worker_image = os.environ.get("RUN_WORKER_IMAGE", "").strip()
        self._proxy_image = os.environ.get("RUNTIME_PROXY_IMAGE", "").strip()
        self._worker_uid, self._worker_gid = _worker_identity()
        self._model_key = os.environ.get("NINEROUTER_KEY", "").strip()
        self._lock = threading.RLock()
        self._host_workspace_root: str | None = None
        self._orphans_reconciled = False

    def ready(self) -> dict[str, object]:
        if not self._token or not self._worker_image or not self._proxy_image:
            return {"status": "not_ready", "provider_ready": False}
        try:
            if self._host_workspace_root is None:
                self._host_workspace_root = self._engine.inspect_self_workspace_root()
            self._engine.request("GET", "/_ping", response_limit=256)
            with self._lock:
                if not self._orphans_reconciled:
                    self._reconcile_orphans()
                    self._orphans_reconciled = True
        except EngineError:
            return {"status": "not_ready", "provider_ready": False}
        return {"status": "ready", "provider_ready": bool(self._model_key)}

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
        if not self._model_key:
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
        network_name = "ao-run-" + UUID(run_id).hex
        worker_name = "ao-worker-" + UUID(run_id).hex
        proxy_name = "ao-proxy-" + UUID(run_id).hex
        with self._lock:
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
        }

    def stop_run(self, run_id: str) -> dict[str, object]:
        container_id = self._owned_container_id(run_id, "worker")
        if container_id is None:
            return {"run_id": run_id, "verified": False, "status": "missing"}
        try:
            self._engine.stop_container(container_id)
        except EngineError:
            pass
        try:
            state = self._engine.inspect_container(container_id).get("State")
            if isinstance(state, dict) and state.get("Running") is True:
                self._engine.kill_container(container_id)
                state = self._engine.inspect_container(container_id).get("State")
            verified = isinstance(state, dict) and state.get("Running") is False
            return {
                "run_id": run_id,
                "verified": verified,
                "status": "stopped" if verified else "unknown",
            }
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
    if not isinstance(endpoint, dict) or endpoint.get("credential_env") != "NINEROUTER_KEY":
        raise LauncherError("Selected Model Endpoint credential is invalid")
    endpoint_url = endpoint.get("endpoint_url")
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
    model_key = os.environ.get("NINEROUTER_KEY", "").strip()
    if model_key:
        env.append("NINEROUTER_KEY=" + model_key)
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
                or event_type not in {"run.tool_action", "session.heartbeat"}
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
        if not self._authenticate():
            self._send_error(401, "unauthorized")
            return
        try:
            body = self._read_json_body()
            if path == "/internal/runs":
                self._send_json(201, self.launcher.start_run(body))
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

    def _read_json_body(self) -> object:
        if self.headers.get_content_type() != "application/json":
            raise LauncherError("JSON request body is required", 415)
        length = self.headers.get("Content-Length", "")
        try:
            size = int(length)
        except ValueError as error:
            raise LauncherError("Request body length is invalid", 411) from error
        if not 0 <= size <= MAX_BODY_BYTES:
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
