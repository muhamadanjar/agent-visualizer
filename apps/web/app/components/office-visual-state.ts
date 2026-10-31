import type { AgentAvailabilityState, RuntimeProjection } from "../lib/activity";

export type OfficeVisualAnimation =
  | "planning"
  | "working"
  | "tool-action"
  | "reviewing"
  | "waiting"
  | "waiting-approval"
  | "success"
  | "failure"
  | "inactive"
  | "neutral";

export type OfficeVisualState = {
  animation: OfficeVisualAnimation;
  statusLabel: string;
};

type VisualProjection = Pick<RuntimeProjection, "status" | "freshness" | "last_event_type" | "last_event_at">;

const PHASE_EVENT_MAX_AGE_MS = 30_000;

function hasFreshPhaseEvent(projection: VisualProjection | undefined, now: number): boolean {
  if (projection?.freshness !== "fresh") return false;
  const occurredAt = Date.parse(projection.last_event_at);
  const age = now - occurredAt;
  return Number.isFinite(occurredAt) && age >= 0 && age <= PHASE_EVENT_MAX_AGE_MS;
}

const NEUTRAL_STATUS: Record<AgentAvailabilityState, OfficeVisualState> = {
  idle: { animation: "neutral", statusLabel: "Idle" },
  working: { animation: "working", statusLabel: "Working" },
  waiting: { animation: "waiting", statusLabel: "Waiting" },
  offline: { animation: "inactive", statusLabel: "Offline" },
  unknown: { animation: "neutral", statusLabel: "Unknown" },
};

export function deriveOfficeVisualState(
  availability: AgentAvailabilityState,
  projection: VisualProjection | undefined,
  now = Date.now(),
): OfficeVisualState {
  const fresh = projection?.freshness === "fresh";
  const freshPhaseEvent = hasFreshPhaseEvent(projection, now);

  if (availability === "working") {
    if (freshPhaseEvent && projection?.status === "running" && projection.last_event_type === "run.started") {
      return { animation: "planning", statusLabel: "Planning" };
    }
    if (fresh && projection.status === "running" && projection.last_event_type === "run.working") {
      return NEUTRAL_STATUS.working;
    }
    if (freshPhaseEvent && projection?.status === "running" && projection.last_event_type === "run.tool_started") {
      return { animation: "tool-action", statusLabel: "Using tool" };
    }
    if (freshPhaseEvent && projection?.status === "running" && projection.last_event_type === "run.reviewing") {
      return { animation: "reviewing", statusLabel: "Reviewing" };
    }
    return NEUTRAL_STATUS.working;
  }

  if (availability === "waiting") {
    if (fresh && (projection.status === "waiting_approval" || projection.last_event_type === "approval.requested")) {
      return { animation: "waiting-approval", statusLabel: "Waiting approval" };
    }
    return NEUTRAL_STATUS.waiting;
  }

  if (availability === "idle" && fresh) {
    if (projection.status === "succeeded" && projection.last_event_type === "run.succeeded") {
      return { animation: "success", statusLabel: "Completed" };
    }
    if (projection.status === "failed" && projection.last_event_type === "run.failed") {
      return { animation: "failure", statusLabel: "Failed" };
    }
  }

  return NEUTRAL_STATUS[availability];
}
