from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence
from uuid import UUID


AgentAvailabilityState = Literal["idle", "working", "waiting", "unknown"]

TERMINAL_EVENT_TYPES = frozenset(
    {"run.succeeded", "run.failed", "run.canceled", "run.unknown"}
)
WAITING_PROJECTION_STATUSES = frozenset({"waiting_input", "waiting_approval", "blocked"})
INCOMPATIBLE_RUNNING_EVENT_TYPES = frozenset(
    {"run.waiting_input", "run.waiting_approval", "run.blocked"}
)
INCOMPATIBLE_WAITING_EVENT_TYPES = frozenset(
    {"run.started", "run.tool_action", "session.heartbeat"}
) | TERMINAL_EVENT_TYPES


@dataclass(frozen=True, slots=True)
class AgentRunEvidence:
    instance_id: UUID
    dispatch_state: str
    projection_status: str | None
    last_event_type: str | None
    last_event_at: datetime | None
    last_heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentAvailability:
    instance_id: UUID
    availability: AgentAvailabilityState


def _run_availability(evidence: AgentRunEvidence, now: datetime) -> AgentAvailabilityState:
    if evidence.dispatch_state in {"queued", "blocked"}:
        if evidence.last_event_type == "run.cancellation_pending":
            return "waiting"
        if evidence.projection_status is None:
            return "waiting" if evidence.last_event_type is None else "unknown"
        if (
            evidence.projection_status not in WAITING_PROJECTION_STATUSES
            or evidence.last_event_type is None
            or evidence.last_event_type in INCOMPATIBLE_WAITING_EVENT_TYPES
        ):
            return "unknown"
        return "waiting"
    if evidence.dispatch_state == "unknown":
        return "unknown"
    if evidence.dispatch_state != "leased":
        return "unknown"

    last_seen = evidence.last_heartbeat_at or evidence.last_event_at
    if (
        last_seen is None
        or now.tzinfo is None
        or last_seen.tzinfo is None
        or (now - last_seen).total_seconds() > 30
    ):
        return "unknown"

    if evidence.last_event_type == "run.cancellation_pending":
        return "waiting"
    if evidence.last_event_type is None or evidence.last_event_type in TERMINAL_EVENT_TYPES:
        return "unknown"
    if evidence.projection_status == "running":
        if evidence.last_event_type in INCOMPATIBLE_RUNNING_EVENT_TYPES:
            return "unknown"
        return "working"
    if evidence.projection_status in WAITING_PROJECTION_STATUSES:
        if evidence.last_event_type in INCOMPATIBLE_WAITING_EVENT_TYPES:
            return "unknown"
        return "waiting"
    return "unknown"


def derive_agent_availability(
    instance_id: UUID,
    evidence: Sequence[AgentRunEvidence],
    *,
    owned: bool,
    now: datetime,
) -> AgentAvailability:
    if not owned:
        return AgentAvailability(instance_id, "unknown")
    if not evidence:
        return AgentAvailability(instance_id, "idle")

    states = tuple(_run_availability(run, now) for run in evidence)
    if "unknown" in states:
        state: AgentAvailabilityState = "unknown"
    elif "working" in states:
        state = "working"
    else:
        state = "waiting"
    return AgentAvailability(instance_id, state)
