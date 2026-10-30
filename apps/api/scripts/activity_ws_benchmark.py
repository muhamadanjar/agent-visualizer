from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import threading
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
import uvicorn
import websockets
from argon2 import PasswordHasher
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.config import load_settings
from app.infrastructure.redis.activity_stream import STREAM_KEY
from app.presentation.http.application import SESSION_COOKIE

EVENTS = int(os.environ.get("BENCHMARK_EVENTS", "1000"))
EVENTS_PER_SECOND = int(os.environ.get("BENCHMARK_RATE", "100"))
CLIENTS = int(os.environ.get("BENCHMARK_CLIENTS", "10"))
STREAM_MAX_LENGTH = 100_000
ORIGIN = "http://127.0.0.1:8765"


def _event(owner_id: str, ids: dict[str, str], sequence: int) -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "event_version": 1,
        "sequence": sequence,
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "session.heartbeat",
        "owner_id": owner_id,
        "project_id": ids["project_id"],
        "agent_id": ids["agent_id"],
        "run_id": ids["run_id"],
        "task_id": ids["task_id"],
        "payload": {
            "task_revision": 1,
            "source_sequence": sequence,
            "safe_action_summary": None,
            "approval_id": None,
        },
    }


async def _run_profile(
    *,
    redis_url: str,
    owner_username: str,
    owner_password: str,
    owns_stream: list[bool],
) -> dict[str, object]:
    api_url = ORIGIN
    websocket_url = f"ws://127.0.0.1:8765/api/v1/ws"
    async_redis = AsyncRedis.from_url(redis_url, decode_responses=True)
    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=10) as http:
            login = await http.post(
                "/api/v1/auth/login",
                json={"username": owner_username, "password": owner_password},
                headers={"origin": ORIGIN},
            )
            if login.status_code != 200:
                raise RuntimeError(f"Benchmark owner login failed: HTTP {login.status_code}")
            cookie = login.cookies.get(SESSION_COOKIE)
            if not cookie:
                raise RuntimeError("Benchmark login returned no session cookie")

            connect_options: dict[str, object] = {
                "origin": ORIGIN,
                "open_timeout": 10,
            }
            header_parameter = (
                "additional_headers"
                if "additional_headers" in inspect.signature(websockets.connect).parameters
                else "extra_headers"
            )
            connect_options[header_parameter] = {"Cookie": f"{SESSION_COOKIE}={cookie}"}
            if "proxy" in inspect.signature(websockets.connect).parameters:
                connect_options["proxy"] = None

            async with AsyncExitStack() as sockets:
                connections = [
                    await sockets.enter_async_context(
                        websockets.connect(websocket_url, **connect_options)
                    )
                    for _ in range(CLIENTS)
                ]
                owners: set[str] = set()
                for websocket in connections:
                    ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                    if ready.get("type") != "session_ready":
                        raise RuntimeError(f"Unexpected WebSocket greeting: {ready!r}")
                    owners.add(ready["owner_id"])
                if len(owners) != 1:
                    raise RuntimeError("WebSocket clients authenticated as different owners")
                owner_id = owners.pop()

                for websocket in connections:
                    await websocket.send(json.dumps({"type": "subscribe", "after_cursor": 0}))
                for websocket in connections:
                    snapshot = json.loads(await asyncio.wait_for(websocket.recv(), timeout=10))
                    if snapshot.get("type") != "activity_snapshot" or snapshot.get("cursor") != 0:
                        raise RuntimeError(
                            f"Expected an empty initial snapshot, got {snapshot!r}"
                        )

                ids = {
                    "project_id": str(uuid4()),
                    "agent_id": str(uuid4()),
                    "run_id": str(uuid4()),
                    "task_id": str(uuid4()),
                }

                async def receive_all(websocket: object) -> tuple[list[int], list[float]]:
                    sequences: list[int] = []
                    latencies_ms: list[float] = []
                    while len(sequences) < EVENTS:
                        message = json.loads(await websocket.recv())  # type: ignore[attr-defined]
                        if message.get("type") != "activity_events":
                            continue
                        for event in message.get("events", []):
                            sequences.append(event["sequence"])
                            occurred_at = datetime.fromisoformat(event["timestamp"])
                            latencies_ms.append(
                                (time.time() - occurred_at.timestamp()) * 1000
                            )
                    return sequences, latencies_ms

                receivers = [
                    asyncio.create_task(receive_all(websocket))
                    for websocket in connections
                ]
                # Allow the API Redis consumer to finish its initial stream-position read.
                await asyncio.sleep(0.5)
                producer_started = time.perf_counter()
                for sequence in range(1, EVENTS + 1):
                    delay = producer_started + (sequence - 1) / EVENTS_PER_SECOND - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    owns_stream[0] = True
                    await async_redis.xadd(
                        STREAM_KEY,
                        {
                            "event": json.dumps(
                                _event(owner_id, ids, sequence), separators=(",", ":")
                            )
                        },
                        maxlen=STREAM_MAX_LENGTH,
                        approximate=True,
                    )
                producer_duration = time.perf_counter() - producer_started
                results = await asyncio.wait_for(
                    asyncio.gather(*receivers), timeout=30
                )
                expected = list(range(1, EVENTS + 1))
                for index, (sequences, _) in enumerate(results, start=1):
                    if sequences != expected:
                        raise RuntimeError(
                            f"Client {index} received {len(sequences)} events or a sequence gap"
                        )

                all_latencies = sorted(
                    latency for _, latencies in results for latency in latencies
                )
                p95_index = max(0, math.ceil(len(all_latencies) * 0.95) - 1)
                p95_ms = all_latencies[p95_index]
                rate = EVENTS / producer_duration
                report: dict[str, object] = {
                    "scope": "isolated Redis stream consumer to authenticated TCP WebSocket fanout",
                    "postgres_outbox_included": False,
                    "clients": CLIENTS,
                    "events_per_client": EVENTS,
                    "producer_events_per_second": round(rate, 2),
                    "p95_delivery_latency_ms": round(p95_ms, 2),
                    "stream_max_length": STREAM_MAX_LENGTH,
                }
                print(json.dumps(report, sort_keys=True))
                if rate < EVENTS_PER_SECOND:
                    raise RuntimeError(
                        f"Producer rate {rate:.2f}/s missed target {EVENTS_PER_SECOND}/s"
                    )
                if p95_ms > 1000:
                    raise RuntimeError(f"P95 delivery latency {p95_ms:.2f}ms exceeded 1000ms")

            logout = await http.post("/api/v1/auth/logout", headers={"origin": ORIGIN})
            if logout.status_code != 200:
                raise RuntimeError(f"Benchmark session logout failed: HTTP {logout.status_code}")
            return report
    finally:
        await async_redis.aclose()


def main() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/15").strip()
    if not database_url:
        raise RuntimeError("TEST_DATABASE_URL is required")
    database = urlsplit(database_url)
    if (
        database.scheme not in {"postgresql", "postgresql+asyncpg"}
        or database.hostname != "db"
        or not unquote(database.path.lstrip("/")).endswith("_test")
    ):
        raise RuntimeError(
            "TEST_DATABASE_URL must target a *_test database on the Compose db service"
        )
    redis = urlsplit(redis_url)
    try:
        redis_port = redis.port
    except ValueError as error:
        raise RuntimeError("REDIS_URL has an invalid port") from error
    if (
        redis.scheme != "redis"
        or redis.hostname != "redis"
        or redis_port not in {None, 6379}
        or redis.path != "/15"
        or redis.username is not None
        or redis.password is not None
        or redis.query
        or redis.fragment
    ):
        raise RuntimeError("REDIS_URL must target the isolated Compose Redis database 15")
    if EVENTS <= 0 or EVENTS_PER_SECOND <= 0 or CLIENTS <= 0:
        raise RuntimeError("benchmark counts and rate must be positive")

    benchmark_redis = Redis.from_url(redis_url, decode_responses=True)
    benchmark_redis.ping()
    if benchmark_redis.exists(STREAM_KEY):
        benchmark_redis.close()
        raise RuntimeError(
            f"Refusing to overwrite existing {STREAM_KEY} in the isolated benchmark Redis database"
        )

    username = f"phase3-benchmark-{uuid4().hex[:12]}"
    password = uuid4().hex + uuid4().hex
    settings = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": username,
            "OWNER_PASSWORD_HASH": PasswordHasher().hash(password),
            "SESSION_SECRET": uuid4().hex + uuid4().hex,
            "PUBLIC_ORIGIN": ORIGIN,
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "SESSION_COOKIE_SECURE": "false",
        }
    )
    os.environ.update(
        {
            "APP_ENV": settings.environment,
            "OWNER_USERNAME": settings.owner_username,
            "OWNER_PASSWORD_HASH": settings.owner_password_hash,
            "SESSION_SECRET": settings.session_secret,
            "PUBLIC_ORIGIN": settings.public_origin,
            "DATABASE_URL": settings.database_url,
            "REDIS_URL": settings.redis_url,
            "SESSION_COOKIE_SECURE": "false",
        }
    )
    from app.main import create_app

    app = create_app(settings=settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    owns_stream = [False]
    try:
        server_thread.start()
        startup_deadline = time.monotonic() + 15
        while not server.started and server_thread.is_alive():
            if time.monotonic() >= startup_deadline:
                raise RuntimeError("Timed out waiting for benchmark API startup")
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Benchmark API exited before startup completed")
        asyncio.run(
            _run_profile(
                redis_url=redis_url,
                owner_username=username,
                owner_password=password,
                owns_stream=owns_stream,
            )
        )
    finally:
        server.should_exit = True
        server_thread.join(timeout=15)
        if owns_stream[0]:
            benchmark_redis.delete(STREAM_KEY)
        benchmark_redis.close()
        if server_thread.is_alive():
            raise RuntimeError("Benchmark API did not stop cleanly")


if __name__ == "__main__":
    main()
