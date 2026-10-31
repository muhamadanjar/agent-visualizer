import { expect, test } from "bun:test";
import { deriveOfficeVisualState } from "../app/components/office-visual-state.ts";

const projection = (overrides = {}) => ({
  status: "running",
  freshness: "fresh",
  last_event_type: "run.started",
  last_event_at: new Date().toISOString(),
  ...overrides,
});

test("fresh run events distinguish planning, work, tool use, and review", () => {
  expect(deriveOfficeVisualState("working", projection())).toEqual({
    animation: "planning",
    statusLabel: "Planning",
  });
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.working",
  }))).toEqual({ animation: "working", statusLabel: "Working" });
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.tool_started",
  }))).toEqual({ animation: "tool-action", statusLabel: "Using tool" });
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.tool_action",
  }))).toEqual({ animation: "working", statusLabel: "Working" });
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.reviewing",
  }))).toEqual({ animation: "reviewing", statusLabel: "Reviewing" });
});

test("stale or conflicting phase evidence falls back to verified availability", () => {
  const working = { animation: "working", statusLabel: "Working" };
  expect(deriveOfficeVisualState("working", projection({
    freshness: "stale",
    last_event_type: "run.tool_started",
  }))).toEqual(working);
  expect(deriveOfficeVisualState("working", projection({
    status: "succeeded",
    last_event_type: "run.reviewing",
  }))).toEqual(working);
});

test("phase cues expire by event age even while a heartbeat stays fresh", () => {
  const now = Date.now();
  const working = { animation: "working", statusLabel: "Working" };
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.reviewing",
    last_event_at: new Date(now - 31_000).toISOString(),
    last_heartbeat_at: new Date(now).toISOString(),
  }), now)).toEqual(working);
  expect(deriveOfficeVisualState("working", projection({
    last_event_type: "run.tool_started",
    last_event_at: new Date(now - 29_000).toISOString(),
    last_heartbeat_at: new Date(now).toISOString(),
  }), now)).toEqual({ animation: "tool-action", statusLabel: "Using tool" });
});

test("approval wait is distinct from other verified waiting states", () => {
  expect(deriveOfficeVisualState("waiting", projection({
    status: "waiting_approval",
    last_event_type: "run.waiting_approval",
  }))).toEqual({ animation: "waiting-approval", statusLabel: "Waiting approval" });
  expect(deriveOfficeVisualState("waiting", projection({
    status: "waiting_input",
    last_event_type: "run.waiting_input",
  }))).toEqual({ animation: "waiting", statusLabel: "Waiting" });
});

test("fresh terminal projections play success and failure reactions only when idle", () => {
  expect(deriveOfficeVisualState("idle", projection({
    status: "succeeded",
    last_event_type: "run.succeeded",
  }))).toEqual({ animation: "success", statusLabel: "Completed" });
  expect(deriveOfficeVisualState("idle", projection({
    status: "failed",
    last_event_type: "run.failed",
  }))).toEqual({ animation: "failure", statusLabel: "Failed" });
});

test("stale failures and unknown availability stay neutral", () => {
  expect(deriveOfficeVisualState("idle", projection({ status: "failed", freshness: "stale" }))).toEqual({
    animation: "neutral",
    statusLabel: "Idle",
  });
  expect(deriveOfficeVisualState("unknown", projection({ status: "unknown" }))).toEqual({
    animation: "neutral",
    statusLabel: "Unknown",
  });
});
test("offline runtime evidence maps to an inactive visual state", () => {
  expect(deriveOfficeVisualState("offline", projection())).toEqual({
    animation: "inactive",
    statusLabel: "Offline",
  });
});
