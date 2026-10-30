from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.evidence import ActivityEvent
from app.infrastructure.redis.activity_stream import (
    STREAM_KEY,
    ActivityEventHub,
    _decode_event,
    consume_stream,
    publish_outbox,
)


def _wire_event(owner_id: str, sequence: int) -> dict[str, object]:
    return {
        "event_id": f"00000000-0000-0000-0000-{sequence:012d}",
        "event_version": 1,
        "sequence": sequence,
        "timestamp": "2026-10-28T12:00:00+00:00",
        "type": "run.started",
        "owner_id": owner_id,
        "project_id": "00000000-0000-0000-0000-000000000001",
        "agent_id": "00000000-0000-0000-0000-000000000002",
        "run_id": "00000000-0000-0000-0000-000000000003",
        "task_id": "00000000-0000-0000-0000-000000000004",
        "payload": {
            "task_revision": 1,
            "source_sequence": sequence,
            "safe_action_summary": None,
            "approval_id": None,
        },
    }


def test_activity_event_hub_scopes_owners_and_bounds_slow_subscribers() -> None:
    hub = ActivityEventHub()
    owner_queue = hub.subscribe("owner-a")
    other_queue = hub.subscribe("owner-b")

    hub.publish(_wire_event("owner-b", 1))
    assert owner_queue.empty()
    assert other_queue.get_nowait()["owner_id"] == "owner-b"

    for sequence in range(1, 258):
        hub.publish(_wire_event("owner-a", sequence))

    assert owner_queue.qsize() == 256
    assert owner_queue.get_nowait()["sequence"] == 2
    hub.unsubscribe("owner-a", owner_queue)
    hub.unsubscribe("owner-b", other_queue)


def test_activity_stream_rejects_malformed_cursor_envelopes() -> None:
    event = _wire_event("owner-a", 1)
    assert _decode_event(json.dumps(event)) == event
    legacy = _wire_event("owner-a", 5)
    del legacy["payload"]["approval_id"]
    assert _decode_event(json.dumps(legacy)) == _wire_event("owner-a", 5)

    approval = _wire_event("owner-a", 6)
    approval["type"] = "approval.requested"
    approval_id = str(uuid4())
    approval["payload"]["approval_id"] = approval_id
    assert _decode_event(json.dumps(approval)) == approval
    approval["payload"]["approval_id"] = None
    assert _decode_event(json.dumps(approval)) is None

    malformed = _wire_event("owner-a", 2)
    malformed["sequence"] = True
    assert _decode_event(json.dumps(malformed)) is None
    leaked = _wire_event("owner-a", 3)
    leaked["raw_tool_output"] = "must not cross the wire"
    assert _decode_event(json.dumps(leaked)) is None
    assert _decode_event("[]") is None
    assert _decode_event("not-json") is None


def _event() -> ActivityEvent:
    return ActivityEvent(
        owner_id="owner-a",
        cursor=1,
        source_id="runner",
        event_id=uuid4(),
        schema_version=1,
        source_session_id=None,
        workspace_id=None,
        project_id=uuid4(),
        task_id=uuid4(),
        task_revision=1,
        agent_instance_id=uuid4(),
        run_id=uuid4(),
        event_type="run.started",
        occurred_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        source_sequence=1,
    )


def test_outbox_retries_redis_failure_before_acknowledging() -> None:
    event = _event()
    stop = asyncio.Event()
    consumer_ready = asyncio.Event()
    consumer_ready.set()

    class Repository:
        pending = True
        released: list[tuple[str, int]] = []

        async def claim_outbox_batch(self, _claim_id, *, limit: int, lease_seconds: int):
            assert limit == 100 and lease_seconds == 30
            return [event] if self.pending else []

        async def release_outbox_claim(self, owner_id: str, cursor: int, _claim_id):
            self.released.append((owner_id, cursor))

        async def mark_outbox_published(self, owner_id: str, cursor: int, _claim_id):
            self.pending = False
            stop.set()
            return True

    class Redis:
        attempts = 0
        payloads: list[str] = []

        async def xadd(self, _stream, fields, **_options):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("Redis unavailable")
            self.payloads.append(fields["event"])

    repository = Repository()
    redis = Redis()
    asyncio.run(publish_outbox(repository, redis, stop, consumer_ready))

    assert redis.attempts == 2
    assert repository.released == [("owner-a", event.cursor)]
    assert repository.pending is False
    assert json.loads(redis.payloads[0])["event_id"] == str(event.event_id)


def test_stream_consumer_reads_new_events_after_existing_stream_cursor() -> None:
    event = _wire_event("owner-a", 1)
    stop = asyncio.Event()
    consumer_ready = asyncio.Event()
    hub = ActivityEventHub()
    queue = hub.subscribe("owner-a")

    class Redis:
        read_cursor: str | None = None

        async def xrevrange(self, _stream, *, count: int):
            assert count == 1
            return [(b"10-0", {})]

        async def xread(self, streams, *, count: int, block: int):
            assert count == 100 and block == 1000
            self.read_cursor = streams[STREAM_KEY]
            stop.set()
            return [(STREAM_KEY, [(b"11-0", {"event": json.dumps(event)})])]

    redis = Redis()
    asyncio.run(consume_stream(redis, hub, stop, consumer_ready))

    assert consumer_ready.is_set()
    assert redis.read_cursor == "10-0"
    assert queue.get_nowait()["sequence"] == 1

def test_stream_consumer_recovers_from_redis_read_failure_without_losing_cursor() -> None:
    event = _wire_event("owner-a", 11)
    stop = asyncio.Event()
    consumer_ready = asyncio.Event()
    hub = ActivityEventHub()
    queue = hub.subscribe("owner-a")

    class Redis:
        read_cursor: str | None = None
        attempts = 0

        async def xrevrange(self, _stream, *, count: int):
            assert count == 1
            return [(b"10-0", {})]

        async def xread(self, streams, *, count: int, block: int):
            assert count == 100 and block == 1000
            self.read_cursor = streams[STREAM_KEY]
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("Redis unavailable")
            stop.set()
            return [(STREAM_KEY, [(b"11-0", {"event": json.dumps(event)})])]

    redis = Redis()
    asyncio.run(consume_stream(redis, hub, stop, consumer_ready))

    assert consumer_ready.is_set()
    assert redis.attempts == 2
    assert redis.read_cursor == "10-0"
    assert queue.get_nowait()["sequence"] == 11
