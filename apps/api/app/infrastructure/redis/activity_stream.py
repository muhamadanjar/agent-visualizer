from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.domain.evidence import (
    ACTIVITY_EVENT_TYPES,
    ActivityEvent,
    ActivityEventRepository,
    activity_event_envelope,
)

logger = logging.getLogger(__name__)
STREAM_KEY = "agent-office:activity-events:v1"
STREAM_MAX_LENGTH = 100_000
OUTBOX_BATCH_SIZE = 100
OUTBOX_LEASE_SECONDS = 30


class ActivityEventHub:
    """Deliver Redis events to WebSockets connected to this API process."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)

    def subscribe(self, owner_id: str) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=256)
        self._subscribers[owner_id].add(queue)
        return queue

    def unsubscribe(self, owner_id: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        subscribers = self._subscribers.get(owner_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(owner_id, None)

    def publish(self, event: dict[str, object]) -> None:
        owner_id = event.get("owner_id")
        if not isinstance(owner_id, str):
            return
        for queue in tuple(self._subscribers.get(owner_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue


async def publish_outbox(
    repository: ActivityEventRepository,
    redis: Redis,
    stop: asyncio.Event,
    consumer_ready: asyncio.Event,
) -> None:
    """Publish committed PostgreSQL outbox rows after the local Redis reader is ready."""
    while not consumer_ready.is_set() and not stop.is_set():
        await _wait(stop, 0.1)
    while not stop.is_set():
        claim_id = uuid4()
        try:
            events = await repository.claim_outbox_batch(
                claim_id, limit=OUTBOX_BATCH_SIZE, lease_seconds=OUTBOX_LEASE_SECONDS
            )
        except Exception:
            logger.warning("Activity outbox claim failed; retrying")
            await _wait(stop, 1.0)
            continue
        if not events:
            await _wait(stop, 0.25)
            continue
        for index, event in enumerate(events):
            if stop.is_set():
                await _release_claims(repository, events[index:], claim_id)
                return
            try:
                await redis.xadd(
                    STREAM_KEY,
                    {"event": json.dumps(activity_event_envelope(event), separators=(",", ":"))},
                    maxlen=STREAM_MAX_LENGTH,
                    approximate=True,
                )
            except Exception:
                await _release_claims(repository, events[index:], claim_id)
                logger.warning("Activity outbox publish failed; rows remain retryable")
                await _wait(stop, 1.0)
                break
            try:
                await repository.mark_outbox_published(
                    event.owner_id, event.cursor, claim_id
                )
            except Exception:
                # A retry can produce a duplicate Redis entry; WebSocket cursors deduplicate it.
                await _release_claims(repository, events[index:], claim_id)
                logger.warning("Activity outbox acknowledgement failed; rows will be retried")
                await _wait(stop, 1.0)
                break


async def consume_stream(
    redis: Redis,
    hub: ActivityEventHub,
    stop: asyncio.Event,
    consumer_ready: asyncio.Event,
) -> None:
    """Read each stream independently per API replica and fan out to its local sockets."""
    last_id = "0-0"
    while not stop.is_set():
        try:
            latest = await redis.xrevrange(STREAM_KEY, count=1)
            if latest:
                last_id = _text(latest[0][0])
            consumer_ready.set()
            break
        except Exception:
            logger.warning("Activity stream unavailable during startup; retrying")
            await _wait(stop, 1.0)
    while not stop.is_set():
        try:
            batches = await redis.xread({STREAM_KEY: last_id}, count=100, block=1000)
            for _, entries in batches:
                for entry_id, fields in entries:
                    last_id = _text(entry_id)
                    raw = fields.get("event")
                    event = _decode_event(raw)
                    if event is not None:
                        hub.publish(event)
        except Exception:
            logger.warning("Activity stream read failed; retrying")
            await _wait(stop, 1.0)


def _decode_event(raw: Any) -> dict[str, object] | None:
    if not isinstance(raw, (str, bytes)):
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return None
    if set(value) != {
        "event_id",
        "event_version",
        "sequence",
        "timestamp",
        "type",
        "owner_id",
        "project_id",
        "agent_id",
        "run_id",
        "task_id",
        "payload",
    }:
        return None
    if set(payload) != {"task_revision", "source_sequence", "safe_action_summary"}:
        return None
    owner_id = value.get("owner_id")
    sequence = value.get("sequence")
    event_version = value.get("event_version")
    event_type = value.get("type")
    source_sequence = payload.get("source_sequence")
    task_revision = payload.get("task_revision")
    action_summary = payload.get("safe_action_summary")
    try:
        for key in ("event_id", "project_id", "agent_id", "run_id", "task_id"):
            UUID(value[key])
        timestamp = datetime.fromisoformat(value["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not isinstance(owner_id, str)
        or not 1 <= len(owner_id) <= 128
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
        or not isinstance(event_version, int)
        or isinstance(event_version, bool)
        or event_version != 1
        or not isinstance(timestamp, datetime)
        or timestamp.tzinfo is None
        or not isinstance(event_type, str)
        or event_type not in ACTIVITY_EVENT_TYPES
        or not isinstance(task_revision, int)
        or isinstance(task_revision, bool)
        or task_revision < 1
        or (event_type == "run.tool_action" and action_summary is None)
        or (event_type != "run.tool_action" and action_summary is not None)
        or (
            source_sequence is not None
            and (
                not isinstance(source_sequence, int)
                or isinstance(source_sequence, bool)
                or source_sequence < 0
            )
        )
        or (
            action_summary is not None
            and (
                not isinstance(action_summary, str)
                or not 1 <= len(action_summary) <= 240
                or any(ord(char) < 32 for char in action_summary)
            )
        )
    ):
        return None
    return value


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)

async def _release_claims(
    repository: ActivityEventRepository, events: list[ActivityEvent], claim_id: UUID
) -> None:
    for event in events:
        try:
            await repository.release_outbox_claim(event.owner_id, event.cursor, claim_id)
        except Exception:
            logger.warning("Activity outbox claim release failed; lease expiry will recover it")


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
