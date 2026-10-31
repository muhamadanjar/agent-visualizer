#!/usr/bin/env python3
"""Install the private Codex gateway and restart idle runtime services.

The isolated OMP profile and scoped client files must already exist. The script
never prints credentials, passes them in argv, or writes them into the checkout.
"""
from __future__ import annotations

import os
import argparse
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request


def private_text(path: Path, limit: int = 4096) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or not 1 <= info.st_size <= limit):
            raise ValueError("private file permissions invalid")
        return source.read(limit + 1)


def install_file(source: Path, destination: Path, *, replace: bool = False) -> None:
    if destination.is_symlink():
        raise ValueError("existing installation is a symlink")
    if destination.exists() and destination.read_bytes() != source.read_bytes():
        info = destination.stat(follow_symlinks=False)
        if not replace or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("existing installation differs; preserve operator changes")
        shutil.copyfile(source, destination)
    elif not destination.exists():
        shutil.copyfile(source, destination)


def main(update_managed_gateway_code: bool = False) -> int:
    root = Path(__file__).resolve().parent.parent
    home = Path.home()
    token_path = home / ".local/share/agent-office/omp-gateway/client.token"
    clients_path = home / ".local/share/agent-office/omp-gateway/clients.json"
    broker_token_path = home / ".omp/profiles/agent-office-codex/auth-broker.token"
    relay = home / ".local/share/agent-office/omp-relay"
    gateway_dir = home / ".local/lib/agent-office/omp-single-attempt"
    state_dir = home / ".local/state/agent-office/omp-single-attempt"
    units = home / ".config/systemd/user"
    config = home / ".config/agent-office/omp-single-attempt.env"
    runner_stopped = False
    try:
        if not os.access("/usr/lib/systemd/systemd-socket-proxyd", os.X_OK):
            raise ValueError("relay executable unavailable")
        token = private_text(token_path).strip()
        private_text(clients_path, 128 * 1024)
        private_text(broker_token_path)
        if not token or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("scoped token invalid")
        for directory in (relay, gateway_dir, state_dir):
            if directory.is_symlink():
                raise ValueError("installation directory is a symlink")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if directory.stat().st_uid != os.getuid():
                raise ValueError("installation ownership invalid")
        os.chown(relay, -1, os.getgid())
        relay.chmod(0o710)
        gateway_dir.chmod(0o700)
        state_dir.chmod(0o700)
        for name in ("package.json", "bun.lock", "main.ts", "gateway.ts", "policy.ts", "transport.ts"):
            install_file(
                root / "deploy/omp-single-attempt" / name,
                gateway_dir / name,
                replace=update_managed_gateway_code and name in {"gateway.ts", "policy.ts", "transport.ts"},
            )
        install_env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "LANG": "C.UTF-8"}
        subprocess.run(["bun", "install", "--frozen-lockfile", "--ignore-scripts", "--no-progress"],
                       cwd=gateway_dir, env=install_env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_text = (f"OMP_AUTH_BROKER_TOKEN_FILE={broker_token_path}\n"
                       f"OMP_GATEWAY_CLIENTS_FILE={clients_path}\n")
        if config.is_symlink():
            raise ValueError("gateway configuration is a symlink")
        if config.exists():
            if private_text(config) != config_text:
                raise ValueError("gateway configuration differs; preserve operator changes")
        else:
            fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(config_text)
        units.mkdir(parents=True, exist_ok=True)
        for name in ("omp-codex-broker.service", "omp-single-attempt.service",
                     "omp-gateway-relay.socket", "omp-gateway-relay.service"):
            install_file(root / "deploy" / name, units / name)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        for unit in ("omp-codex-broker.service", "omp-single-attempt.service", "omp-gateway-relay.socket"):
            subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if update_managed_gateway_code:
            subprocess.run(["systemctl", "--user", "restart", "omp-single-attempt.service"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        deadline = time.monotonic() + 15
        while True:
            try:
                request = urllib.request.Request(
                    "http://127.0.0.1:14000/healthz",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    policy = json.load(response)
                if policy.get("policy") != "agent-office-codex-single-attempt-v1" or policy.get("maxUpstreamAttempts") != 1 or policy.get("websockets") is not False:
                    raise ValueError("gateway policy invalid")
                break
            except (OSError, ValueError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise ValueError("authenticated gateway not ready")
                time.sleep(0.25)
        environment = dict(os.environ, OMP_GATEWAY_KEY=token, OMP_RELAY_GID=str(os.getgid()))
        active_command = ["docker", "ps", "--quiet", "--filter", "label=agent-office.runtime=true",
                          "--filter", "label=agent-office.role=worker"]
        def active_workers() -> bool:
            return bool(subprocess.run(active_command, check=True, capture_output=True, timeout=15).stdout.strip())
        if active_workers():
            raise ValueError("active Runs must finish before runtime restart")
        subprocess.run(["docker", "compose", "stop", "runner"], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        runner_stopped = True
        if active_workers():
            raise ValueError("a Run started during runtime restart")
        compose_files = [root / "docker-compose.yml"]
        operator_override = root / "docker-compose.override.yml"
        if operator_override.is_symlink():
            raise ValueError("Compose override must not be a symlink")
        if operator_override.is_file():
            compose_files.append(operator_override)
        compose_files.append(root / "deploy/compose.omp.yml")
        compose_command = ["docker", "compose"]
        for compose_file in compose_files:
            compose_command.extend(["-f", str(compose_file)])
        subprocess.run([
            *compose_command, "up", "-d", "--no-build", "--no-deps",
            "--force-recreate", "runtime-launcher", "runner",
        ], cwd=root, env=environment, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        runner_stopped = False
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        if runner_stopped:
            subprocess.run(["docker", "compose", "start", "runner"], cwd=root,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        print("OMP wiring failed; check credentials, idle Runs, systemd, gateway and integrated images.", file=sys.stderr)
        return 1
    print("Private OMP wiring installed; authenticated gateway readiness remains authoritative.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update-managed-gateway-code",
        action="store_true",
        help="replace gateway.ts, policy.ts, and transport.ts in the user-owned gateway installation",
    )
    raise SystemExit(main(parser.parse_args().update_managed_gateway_code))
