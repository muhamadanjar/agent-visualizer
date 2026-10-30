from __future__ import annotations

import os
from uuid import uuid4

import pytest

from apps.launcher import server


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_RUNTIME_TESTS") != "1",
    reason="set RUN_DOCKER_RUNTIME_TESTS=1 to exercise the real Docker network boundary",
)
def test_internal_run_network_blocks_direct_external_socket_access() -> None:
    engine = server.DockerEngine()
    suffix = uuid4().hex
    network_name = "ao-phase2-egress-" + suffix
    container_name = "ao-phase2-egress-" + suffix
    labels = {"agent-office.phase2-smoke": "true"}
    network = engine.create_network(network_name, labels)
    network_id = str(network["Id"])
    container_id: str | None = None
    try:
        container = engine.create_container(
            container_name,
            {
                "Image": "agent-office-runner:local",
                "Entrypoint": ["python"],
                "Cmd": [
                    "-c",
                    "import socket, sys; "
                    "exec('try:\\n socket.create_connection((\"1.1.1.1\", 443), timeout=2)\\n'"
                    "'except OSError:\\n print(\"direct_egress=blocked\")\\n'"
                    "'else:\\n print(\"direct_egress=allowed\")\\n sys.exit(7)')",
                ],
                "Labels": labels,
                "HostConfig": {
                    "NetworkMode": network_name,
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 32,
                    "Memory": 256 * 1024 * 1024,
                },
            },
        )
        container_id = str(container["Id"])
        engine.start_container(container_id)
        completion = engine.request(
            "POST",
            f"/containers/{container_id}/wait?condition=not-running",
            accepted=(200,),
        )
        assert isinstance(completion, dict)
        assert completion.get("StatusCode") == 0
        assert b"direct_egress=blocked" in server._docker_stdout(
            engine.logs(container_id)
        )
    finally:
        if container_id is not None:
            engine.remove_container(container_id)
        engine.remove_network(network_id)

@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_RUNTIME_TESTS") != "1",
    reason="set RUN_DOCKER_RUNTIME_TESTS=1 to exercise the real Docker network boundary",
)
def test_run_proxy_rejects_non_selected_endpoint_from_worker_container() -> None:
    engine = server.DockerEngine()
    suffix = uuid4().hex
    run_id = str(uuid4())
    network_name = "ao-phase2-proxy-" + suffix
    network_labels = {
        server.OWNER_LABEL: "true",
        server.RUN_ID_LABEL: run_id,
        server.ROLE_LABEL: "run-network",
    }
    network = engine.create_network(network_name, network_labels)
    network_id = str(network["Id"])
    proxy_id: str | None = None
    worker_id: str | None = None
    try:
        proxy_config = server._proxy_container_config(
            "agent-office-runtime-launcher:local",
            {server.RUN_ID_LABEL: run_id, server.ROLE_LABEL: "proxy"},
            ("selected.example", 443),
        )
        proxy = engine.create_container("ao-phase2-proxy-" + suffix, proxy_config)
        proxy_id = str(proxy["Id"])
        engine.start_container(proxy_id)
        engine.connect_network(network_id, proxy_id)

        worker = engine.create_container(
            "ao-phase2-probe-" + suffix,
            {
                "Image": "agent-office-runner:local",
                "Entrypoint": ["python"],
                "Cmd": [
                    "-c",
                    "import socket, sys, time\n"
                    "for attempt in range(30):\n"
                    " try:\n"
                    "  s=socket.create_connection(('egress-proxy', 3128), timeout=1)\n"
                    "  break\n"
                    " except OSError:\n"
                    "  time.sleep(0.1)\n"
                    "else:\n"
                    " print('proxy_connection_failed')\n"
                    " sys.exit(8)\n"
                    "s.sendall(b'GET http://not-selected.invalid:443/v1 HTTP/1.1\\r\\n"
                    "Host: not-selected.invalid:443\\r\\nConnection: close\\r\\n\\r\\n')\n"
                    "response=s.recv(512)\n"
                    "print(response.decode(errors='replace'))\n"
                    "sys.exit(0 if b'403 Forbidden' in response else 7)\n",
                ],
                "Labels": {"agent-office.phase2-smoke": "true"},
                "HostConfig": {
                    "NetworkMode": network_name,
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 32,
                    "Memory": 256 * 1024 * 1024,
                },
            },
        )
        worker_id = str(worker["Id"])
        engine.start_container(worker_id)
        completion = engine.request(
            "POST",
            f"/containers/{worker_id}/wait?condition=not-running",
            accepted=(200,),
        )
        assert isinstance(completion, dict)
        stdout = server._docker_stdout(engine.logs(worker_id))
        assert completion.get("StatusCode") == 0, stdout.decode(errors="replace")
        assert b"403 Forbidden" in stdout
    finally:
        for container_id in (worker_id, proxy_id):
            if container_id is not None:
                engine.stop_container(container_id)
                engine.remove_container(container_id)
        engine.remove_network(network_id)
