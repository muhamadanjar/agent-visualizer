from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.availability import AgentRunEvidence, derive_agent_availability


@pytest.mark.parametrize(
    ("dispatch_state", "projection_status", "last_event_type", "age_seconds", "expected"),
    [
        ("queued", None, None, None, "waiting"),
        ("blocked", None, None, None, "waiting"),
        ("queued", "running", "run.started", 1, "unknown"),
        ("queued", None, "run.started", 1, "unknown"),
        ("leased", "waiting_input", "run.started", 1, "unknown"),
        ("blocked", "running", "run.started", 1, "unknown"),
        ("unknown", "running", "run.started", 1, "unknown"),
        ("leased", "running", "run.started", 1, "working"),
        ("leased", "running", "run.started", 30, "working"),
        ("leased", "waiting_input", "run.waiting_input", 1, "waiting"),
        ("leased", "waiting_approval", "run.waiting_approval", 1, "waiting"),
        ("leased", "running", "run.cancellation_pending", 1, "waiting"),
        ("leased", "running", "run.waiting_input", 1, "unknown"),
        ("leased", "running", "run.started", 31, "unknown"),
        ("leased", None, None, None, "unknown"),
        ("leased", "succeeded", "run.succeeded", 1, "unknown"),
        ("leased", "running", "run.unknown", 1, "unknown"),
    ],
)
def test_run_evidence_maps_only_verified_work_to_working(
    dispatch_state: str,
    projection_status: str | None,
    last_event_type: str | None,
    age_seconds: int | None,
    expected: str,
) -> None:
    now = datetime.now(UTC)
    last_event_at = now - timedelta(seconds=age_seconds) if age_seconds is not None else None
    evidence = AgentRunEvidence(
        instance_id=uuid4(),
        dispatch_state=dispatch_state,
        projection_status=projection_status,
        last_event_type=last_event_type,
        last_event_at=last_event_at,
        last_heartbeat_at=None,
    )

    actual = derive_agent_availability(
        evidence.instance_id,
        [evidence],
        owned=True,
        now=now,
    )

    assert actual.availability == expected


def test_no_unresolved_run_is_idle_but_foreign_instance_is_unknown() -> None:
    now = datetime.now(UTC)
    instance_id = uuid4()

    assert derive_agent_availability(instance_id, [], owned=True, now=now).availability == "idle"
    assert derive_agent_availability(instance_id, [], owned=False, now=now).availability == "unknown"


def test_conflicting_run_evidence_never_hides_uncertainty() -> None:
    now = datetime.now(UTC)
    instance_id = uuid4()
    running = AgentRunEvidence(
        instance_id, "leased", "running", "run.started", now, None
    )
    unknown = AgentRunEvidence(
        instance_id, "unknown", "running", "run.started", now, None
    )

    assert derive_agent_availability(
        instance_id, [running, unknown], owned=True, now=now
    ).availability == "unknown"
    assert derive_agent_availability(
        instance_id, [running], owned=True, now=now
    ).availability == "working"


def test_recent_heartbeat_is_authoritative_when_the_last_event_is_old() -> None:
    now = datetime.now(UTC)
    instance_id = uuid4()
    evidence = AgentRunEvidence(
        instance_id=instance_id,
        dispatch_state="leased",
        projection_status="running",
        last_event_type="run.started",
        last_event_at=now - timedelta(minutes=2),
        last_heartbeat_at=now - timedelta(seconds=5),
    )

    assert derive_agent_availability(
        instance_id, [evidence], owned=True, now=now
    ).availability == "working"
