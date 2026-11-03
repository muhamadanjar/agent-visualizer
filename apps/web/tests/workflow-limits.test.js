import { expect, test } from "bun:test";
import { buildWorkflowStartInput } from "../app/lib/workflow.ts";

test("workflow start preserves Task inputs and clamps Task limits", () => {
  expect(buildWorkflowStartInput({
    resource_refs: ["docs/requirements.md"],
    max_child_runs: 100,
    max_duration_minutes: 1440,
  }, "Plan the release", "request-1")).toEqual({
    request_id: "request-1",
    goal: "Plan the release",
    input_refs: ["docs/requirements.md"],
    max_child_runs: 8,
    max_duration_minutes: 60,
  });
});

test("workflow start preserves unset and lower Task limits", () => {
  expect(buildWorkflowStartInput({
    resource_refs: [],
    max_child_runs: null,
    max_duration_minutes: 30,
  }, "Plan the release", "request-2")).toEqual({
    request_id: "request-2",
    goal: "Plan the release",
    input_refs: [],
    max_child_runs: null,
    max_duration_minutes: 30,
  });
});
