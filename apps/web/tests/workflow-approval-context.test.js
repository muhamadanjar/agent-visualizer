import { expect, test } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WorkflowNodeCard } from "../app/components/workflow-ui";

const node = {
  node_id: "review",
  child_task_id: "task-1",
  child_revision: 3,
  digest: "approved-digest",
  dependencies: [],
  status: "waiting_approval",
  child_task: {
    title: "Review the migration plan",
    goal: "Review the migration without applying it.",
    input_text: "Check this input: <script>alert(1)</script>",
    acceptance_criteria: ["Report risks", "Do not modify the source database"],
    resource_refs: ["docs/migration.md"],
    output_schema: { type: "object" },
  },
  definition_id: "definition-1",
  definition_version: 2,
  instance_id: "instance-1",
  project_id: "project-1",
  workspace_path: "/workspace/project",
  input_payload: { request: "Review only" },
  resource_refs: ["docs/migration.md"],
  output_schema: { type: "object" },
  model_scope: { model: "reviewer" },
  tool_scope: ["read_file"],
  limits: { max_child_runs: 1 },
  approval_id: "approval-1",
  approval_digest: "approved-digest",
  approval_expires_at: "2026-11-10T12:00:00Z",
  approval_decision: null,
  child_run_id: null,
  output: null,
};

function render(nodeOverride = {}) {
  return renderToStaticMarkup(createElement(WorkflowNodeCard, {
    node: { ...node, ...nodeOverride },
    busy: false,
    onDecision: () => undefined,
  }));
}

test("Owner approval displays the executable child Task context", () => {
  const markup = render();

  expect(markup).toContain("Review the migration plan");
  expect(markup).toContain("Review the migration without applying it.");
  expect(markup).toContain("Report risks");
  expect(markup).toContain("Do not modify the source database");
  expect(markup).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  expect(markup).not.toContain("<script>alert(1)</script>");
  expect(markup).toContain('aria-label="Executable child Task context"');
  expect(markup).not.toMatch(/<button[^>]*disabled=""/);
});

test("Owner decisions remain disabled without complete context or matching digest", () => {
  const missingContext = render({ child_task: null });
  expect(missingContext).toContain("Executable child Task context is unavailable or incomplete.");
  expect(missingContext).toContain("Approval is disabled: executable child Task context is unavailable.");
  expect(missingContext.match(/<button[^>]*disabled=""/g)).toHaveLength(2);

  const mismatchedDigest = render({ approval_digest: "different-digest" });
  expect(mismatchedDigest).toContain("Approval is disabled: the approval digest does not match this Delegation.");
  expect(mismatchedDigest.match(/<button[^>]*disabled=""/g)).toHaveLength(2);
});
