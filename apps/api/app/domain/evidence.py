from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

ACTIVITY_EVENT_TYPES = frozenset(
    {
        "run.started",
        "run.waiting_input",
        "run.waiting_approval",
        "run.blocked",
        "run.tool_action",
        "run.cancellation_pending",
        "run.succeeded",
        "run.failed",
        "run.canceled",
        "run.unknown",
        "session.heartbeat",
        "approval.requested",
        "approval.approved",
        "approval.rejected",
    }
)
EVENT_STATUS = {
    "run.started": "running",
    "run.waiting_input": "waiting_input",
    "run.waiting_approval": "waiting_approval",
    "run.blocked": "blocked",
    "run.cancellation_pending": "running",
    "run.succeeded": "succeeded",
    "run.failed": "failed",
    "run.canceled": "canceled",
    "run.unknown": "unknown",
}
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "canceled", "unknown"})
ACTION_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
SCENE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")
MAX_SCENE_CONTENT_BYTES = 262_144


class EvidenceInputError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ActivityEventDraft:
    owner_id: str
    source_id: str
    event_id: UUID
    schema_version: int
    source_session_id: UUID | None
    workspace_id: UUID | None
    project_id: UUID
    task_id: UUID
    task_revision: int
    agent_instance_id: UUID
    run_id: UUID
    event_type: str
    occurred_at: datetime
    source_sequence: int | None
    action_summary: str | None = None
    approval_id: UUID | None = None
    def validate(self) -> None:
        if not self.owner_id or len(self.owner_id) > 128:
            raise EvidenceInputError("owner_id")
        if not self.source_id or len(self.source_id) > 128:
            raise EvidenceInputError("source_id")
        if self.event_type not in ACTIVITY_EVENT_TYPES:
            raise EvidenceInputError("event_type")
        if self.schema_version != 1:
            raise EvidenceInputError("schema_version")
        if self.task_revision < 1:
            raise EvidenceInputError("task_revision")
        if self.source_sequence is not None and self.source_sequence < 0:
            raise EvidenceInputError("source_sequence")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise EvidenceInputError("occurred_at")
        approval_event = self.event_type.startswith("approval.")
        if approval_event != (self.approval_id is not None):
            raise EvidenceInputError("approval_id")
        if self.event_type == "run.tool_action":
            if (
                self.action_summary is None
                or not self.action_summary
                or len(self.action_summary) > 240
                or any(ord(char) < 32 for char in self.action_summary)
            ):
                raise EvidenceInputError("action_summary")
        elif self.action_summary is not None:
            raise EvidenceInputError("action_summary")



@dataclass(frozen=True, slots=True)
class ActivityEvent:
    owner_id: str
    cursor: int
    source_id: str
    event_id: UUID
    schema_version: int
    source_session_id: UUID | None
    workspace_id: UUID | None
    project_id: UUID
    task_id: UUID
    task_revision: int
    agent_instance_id: UUID
    run_id: UUID
    event_type: str
    occurred_at: datetime
    received_at: datetime
    source_sequence: int | None
    action_summary: str | None = None
    approval_id: UUID | None = None

@dataclass(frozen=True, slots=True)
class RunActivityProjection:
    owner_id: str
    run_id: UUID
    source_id: str
    task_id: UUID
    task_revision: int
    project_id: UUID
    agent_instance_id: UUID
    status: str
    last_event_cursor: int
    last_event_type: str
    last_event_at: datetime
    last_sequence: int | None
    last_heartbeat_at: datetime | None
    last_action_summary: str | None
    updated_at: datetime

@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    cursor: int
    projections: tuple[RunActivityProjection, ...]


class ActivityEventRepository(Protocol):
    async def append_event(self, event: ActivityEventDraft) -> tuple[ActivityEvent, bool]: ...

    async def append_terminal_event(
        self, event: ActivityEventDraft, runner_id: UUID
    ) -> tuple[ActivityEvent, bool]: ...
    async def has_event_for_runner(
        self, owner_id: str, source_id: str, run_id: UUID, event_id: UUID
    ) -> bool: ...

    async def get_projection_for_run(
        self, owner_id: str, run_id: UUID
    ) -> RunActivityProjection | None: ...

    async def list_events_for_run(
        self, owner_id: str, run_id: UUID, *, limit: int
    ) -> list[ActivityEvent] | None: ...

    async def get_snapshot_for_owner(
        self, owner_id: str, *, limit: int
    ) -> ActivitySnapshot: ...

    async def list_events_after(
        self, owner_id: str, cursor: int, *, limit: int
    ) -> list[ActivityEvent]: ...

    async def claim_outbox_batch(
        self, claim_id: UUID, *, limit: int, lease_seconds: int
    ) -> list[ActivityEvent]: ...

    async def mark_outbox_published(
        self, owner_id: str, cursor: int, claim_id: UUID
    ) -> bool: ...

    async def release_outbox_claim(
        self, owner_id: str, cursor: int, claim_id: UUID
    ) -> None: ...


def activity_event_envelope(event: ActivityEvent) -> dict[str, object]:
    """Convert persisted Activity Event data to the versioned PRD wire contract."""
    return {
        "event_id": str(event.event_id),
        "event_version": event.schema_version,
        "sequence": event.cursor,
        "timestamp": event.occurred_at.isoformat(),
        "type": event.event_type,
        "owner_id": event.owner_id,
        "project_id": str(event.project_id),
        "agent_id": str(event.agent_instance_id),
        "run_id": str(event.run_id),
        "task_id": str(event.task_id),
        "payload": {
            "task_revision": event.task_revision,
            "source_sequence": event.source_sequence,
            "safe_action_summary": event.action_summary,
            "approval_id": None if event.approval_id is None else str(event.approval_id),
        },
    }


def _same_event(row: Mapping[str, object], event: ActivityEventDraft) -> bool:
    return all(
        row[field] == value
        for field, value in (
            ("source_id", event.source_id),
            ("schema_version", event.schema_version),
            ("source_session_id", event.source_session_id),
            ("workspace_id", event.workspace_id),
            ("project_id", event.project_id),
            ("task_id", event.task_id),
            ("task_revision", event.task_revision),
            ("agent_instance_id", event.agent_instance_id),
            ("run_id", event.run_id),
            ("event_type", event.event_type),
            ("occurred_at", event.occurred_at),
            ("source_sequence", event.source_sequence),
            ("action_summary", event.action_summary),
            ("approval_id", event.approval_id),
        )
    )


def _is_newer(event: ActivityEventDraft, current: RunActivityProjection) -> bool:
    if event.source_id != current.source_id:
        raise EvidenceInputError("source_conflict")
    if event.source_sequence is not None and current.last_sequence is not None:
        return event.source_sequence > current.last_sequence
    return event.occurred_at > current.last_event_at
@dataclass(frozen=True, slots=True)
class ApprovalRequestDraft:
    owner_id: str
    run_id: UUID
    request_id: UUID
    action_type: str
    action_digest: str
    resource_ref: str | None
    expires_at: datetime

    def validate(self) -> None:
        if not self.action_type or len(self.action_type) > 64:
            raise EvidenceInputError("action_type")
        if ACTION_DIGEST_RE.fullmatch(self.action_digest) is None:
            raise EvidenceInputError("action_digest")
        if self.resource_ref is not None and (not self.resource_ref or len(self.resource_ref) > 2048):
            raise EvidenceInputError("resource_ref")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise EvidenceInputError("expires_at")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    owner_id: str
    id: UUID
    run_id: UUID
    request_id: UUID
    action_type: str
    action_digest: str
    resource_ref: str | None
    created_at: datetime
    expires_at: datetime
    decision: str | None
    decided_at: datetime | None


class ApprovalRepository(Protocol):
    async def create_request(
        self, draft: ApprovalRequestDraft
    ) -> tuple[ApprovalRequest, bool]: ...

    async def resolve(
        self, owner_id: str, approval_id: UUID, action_digest: str,
        decision: str, now: datetime,
    ) -> ApprovalRequest: ...

    async def get_for_owner(self, owner_id: str, approval_id: UUID) -> ApprovalRequest | None: ...

    async def list_for_run(
        self, owner_id: str, run_id: UUID, *, limit: int
    ) -> list[ApprovalRequest] | None: ...

@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    owner_id: str
    run_id: UUID
    artifact_type: str
    display_name: str
    media_type: str | None
    byte_length: int | None
    sha256: str | None
    storage_key: UUID

    def validate(self) -> None:
        if not self.artifact_type or len(self.artifact_type) > 64:
            raise EvidenceInputError("artifact_type")
        if not self.display_name.strip() or len(self.display_name) > 240:
            raise EvidenceInputError("display_name")
        if self.media_type is not None and (not self.media_type or len(self.media_type) > 120):
            raise EvidenceInputError("media_type")
        if self.byte_length is not None and self.byte_length < 0:
            raise EvidenceInputError("byte_length")
        if self.sha256 is not None and ACTION_DIGEST_RE.fullmatch(self.sha256) is None:
            raise EvidenceInputError("sha256")


@dataclass(frozen=True, slots=True)
class Artifact:
    owner_id: str
    id: UUID
    run_id: UUID
    artifact_type: str
    display_name: str
    media_type: str | None
    byte_length: int | None
    sha256: str | None
    storage_key: UUID
    created_at: datetime


class ArtifactRepository(Protocol):
    async def create(self, draft: ArtifactDraft) -> Artifact: ...

    async def list_for_run(self, owner_id: str, run_id: UUID) -> list[Artifact] | None: ...

@dataclass(frozen=True, slots=True)
class OfficeSceneVersion:
    owner_id: str
    scene_id: str
    version: int
    contents: dict[str, object]
    created_at: datetime


class OfficeSceneRepository(Protocol):
    async def create_version(
        self, owner_id: str, scene_id: str, contents: dict[str, object],
        expected_previous_version: int | None,
    ) -> OfficeSceneVersion: ...

    async def get_version(
        self, owner_id: str, scene_id: str, version: int | None = None
    ) -> OfficeSceneVersion | None: ...

def action_digest(canonical_parameters: dict[str, object]) -> str:
    try:
        encoded = json.dumps(
            canonical_parameters,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EvidenceInputError("action_parameters") from error
    return hashlib.sha256(encoded).hexdigest()
