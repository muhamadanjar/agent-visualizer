"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  buildWorkflowStartInput,
  decideWorkflowApproval,
  requestRunWorkflow,
  requestWorkflowStart,
  type WorkflowDelegation,
  type WorkflowProjection,
} from "../lib/workflow";
import type { Task } from "../lib/tasks";
import { StatusPill } from "./workspace-components";

function hasExecutableChildTaskContext(
  value: WorkflowDelegation["child_task"] | undefined,
): value is NonNullable<WorkflowDelegation["child_task"]> {
  return value !== null && value !== undefined
    && typeof value.title === "string"
    && value.title.trim().length > 0
    && typeof value.goal === "string"
    && value.goal.trim().length > 0
    && typeof value.input_text === "string"
    && Array.isArray(value.acceptance_criteria)
    && value.acceptance_criteria.every((item) => typeof item === "string")
    && Array.isArray(value.resource_refs)
    && value.resource_refs.every((item) => typeof item === "string")
    && value.output_schema !== undefined;
}
function displayValue(value: unknown) {
  if (value === undefined) return "Not provided";
  if (typeof value === "string") return value || "Empty string";
  const serialized = JSON.stringify(value, null, 2);
  return serialized === undefined ? "Not provided" : serialized;
}

function SnapshotField({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="workflow-snapshot-field">
      <dt>{label}</dt>
      <dd><pre>{displayValue(value)}</pre></dd>
    </div>
  );
}

export function WorkflowStartPanel({ task }: { task: Task }) {
  const [goal, setGoal] = useState(task.goal);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<{ parentRunId: string; planDigest: string } | null>(null);
  const requestIdRef = useRef<{ taskId: string; requestId: string } | null>(null);

  async function startWorkflow() {
    const normalizedGoal = goal.trim();
    if (normalizedGoal.length < 3 || starting) return;
    const previousRequest = requestIdRef.current;
    const requestId = previousRequest?.taskId === task.id
      ? previousRequest.requestId
      : crypto.randomUUID();
    requestIdRef.current = { taskId: task.id, requestId };
    setStarting(true);
    setError(null);
    try {
      const response = await requestWorkflowStart(
        task.id,
        buildWorkflowStartInput(task, normalizedGoal, requestId),
      );
      requestIdRef.current = null;
      setStarted({ parentRunId: response.parent_run_id, planDigest: response.plan_digest });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Workflow could not be started.");
    } finally {
      setStarting(false);
    }
  }

  const goalIsMeaningful = goal.trim().length >= 3;
  return (
    <section className="content-section workflow-start-panel" aria-labelledby="workflow-start-heading">
      <div className="section-heading">
        <h2 id="workflow-start-heading">Multi-agent workflow</h2>
        <StatusPill>Owner action</StatusPill>
      </div>
      <p className="field-note">Workflow preflight is separate from normal Run preflight: parent planning does not require a Runner connection.</p>
      <label className="workflow-goal-field">
        <span>Planning goal</span>
        <textarea value={goal} onChange={(event) => setGoal(event.target.value)} rows={4} aria-describedby="workflow-goal-help" />
      </label>
      <p className="field-note" id="workflow-goal-help">Starting creates a parent Run and persists the planner’s exact delegation plan. It does not silently start a normal Run.</p>
      {started ? (
        <div className="workflow-started" role="status">
          <strong>Workflow plan created.</strong>
          <span>Plan digest: {started.planDigest}</span>
          <Link className="button" href={`/runs/${encodeURIComponent(started.parentRunId)}`}>Open parent Run</Link>
        </div>
      ) : (
        <button className="button" type="button" disabled={!goalIsMeaningful || starting} onClick={() => void startWorkflow()}>
          {starting ? "Planning workflow…" : "Start workflow"}
        </button>
      )}
      {!goalIsMeaningful ? <p className="inline-error" role="alert">Add a meaningful goal before starting the workflow.</p> : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
    </section>
  );
}

export function WorkflowNodeCard({ node, busy, onDecision }: { node: WorkflowDelegation; busy: boolean; onDecision: (node: WorkflowDelegation, decision: "approved" | "rejected") => void }) {
  const rawChildTask = node.child_task;
  const childTaskContext = hasExecutableChildTaskContext(rawChildTask) ? rawChildTask : null;
  const childTaskContextAvailable = childTaskContext !== null;
  const approvalPending = node.approval_id !== null
    && (node.approval_decision === null || node.approval_decision === "pending");
  const approvalBindingMatches = node.approval_digest === node.digest;
  const canDecide = approvalPending && childTaskContextAvailable && approvalBindingMatches;
  return (
    <article className="workflow-node-card">
      <div className="workflow-node-heading">
        <div><p className="eyebrow">Node · {node.node_id}</p><h4>{childTaskContext?.title ?? node.child_task_id}</h4></div>
        <StatusPill tone={node.status === "succeeded" ? "green" : node.status === "failed" ? "amber" : "neutral"}>{node.status}</StatusPill>
      </div>
      <section aria-label="Executable child Task context">
        {childTaskContext !== null ? (
          <dl className="workflow-snapshot">
            <SnapshotField label="Child Task goal / instructions" value={childTaskContext.goal} />
            <SnapshotField label="Child Task input text" value={childTaskContext.input_text} />
            <SnapshotField label="Acceptance criteria" value={childTaskContext.acceptance_criteria} />
            <SnapshotField label="Child Task resource refs" value={childTaskContext.resource_refs} />
            <SnapshotField label="Child Task output schema" value={childTaskContext.output_schema} />
          </dl>
        ) : (
          <p className="field-note" role="status">Executable child Task context is unavailable or incomplete.</p>
        )}
      </section>
      <dl className="workflow-snapshot">
        <SnapshotField label="Child task revision" value={node.child_revision} />
        <SnapshotField label="Child task digest" value={node.digest} />
        <SnapshotField label="Definition" value={{ id: node.definition_id, version: node.definition_version }} />
        <SnapshotField label="Agent instance" value={node.instance_id} />
        <SnapshotField label="Project" value={node.project_id} />
        <SnapshotField label="Workspace path" value={node.workspace_path} />
        <SnapshotField label="Input payload" value={node.input_payload} />
        <SnapshotField label="Resource refs" value={node.resource_refs} />
        <SnapshotField label="Dependencies" value={node.dependencies} />
        <SnapshotField label="Output schema" value={node.output_schema} />
        <SnapshotField label="Model scope" value={node.model_scope} />
        <SnapshotField label="Tool scope" value={node.tool_scope} />
        <SnapshotField label="Limits" value={node.limits} />
        <SnapshotField label="Approval" value={{ id: node.approval_id, digest: node.approval_digest, expires_at: node.approval_expires_at, decision: node.approval_decision }} />
        <SnapshotField label="Output" value={node.output} />
      </dl>
      <div className="workflow-node-footer">
        {node.child_run_id ? <Link className="text-link" href={`/runs/${encodeURIComponent(node.child_run_id)}`}>Open child Run {node.child_run_id}</Link> : <span className="muted">Child Run not assigned</span>}
        {approvalPending ? (
          <div className="workflow-approval-actions">
            {canDecide
              ? <span className="field-note">The Owner decision is bound to the exact Delegation digest shown above.</span>
              : <span className="field-note" role="alert">{childTaskContext !== null ? "Approval is disabled: the approval digest does not match this Delegation." : "Approval is disabled: executable child Task context is unavailable."}</span>}
            <button className="button" type="button" disabled={busy || !canDecide} onClick={() => onDecision(node, "approved")}>{busy ? "Saving…" : "Approve node"}</button>
            <button className="button button-secondary" type="button" disabled={busy || !canDecide} onClick={() => onDecision(node, "rejected")}>Reject node</button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

export function WorkflowRunPanel({ runId }: { runId: string }) {
  const [workflow, setWorkflow] = useState<WorkflowProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notWorkflow, setNotWorkflow] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [decisionNode, setDecisionNode] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setWorkflow(null);
    setError(null);
    setNotWorkflow(false);

    let workflowNotFound = false;
    const load = async () => {
      try {
        const result = await requestRunWorkflow(runId, controller.signal);
        if (active) {
          if (result === null) {
            workflowNotFound = true;
            setWorkflow(null);
            setNotWorkflow(true);
            setError(null);
            return;
          }
          workflowNotFound = false;
          setWorkflow(result);
          setError(null);
          setNotWorkflow(false);
        }
      } catch (reason) {
        if (active && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Could not load workflow plan.");
        }
      }
    };
    void load();
    const poll = window.setInterval(() => { if (!workflowNotFound) void load(); }, 5_000);
    return () => { active = false; controller.abort(); window.clearInterval(poll); };
  }, [reloadKey, runId]);

  async function decide(node: WorkflowDelegation, decision: "approved" | "rejected") {
    if (!node.approval_id || !node.approval_digest || decisionNode) return;
    setDecisionNode(node.node_id);
    setDecisionError(null);
    try {
      await decideWorkflowApproval(node.approval_id, node.approval_digest, decision);
      setReloadKey((key) => key + 1);
    } catch (reason) {
      setDecisionError(reason instanceof Error ? reason.message : "Approval decision could not be saved.");
    } finally {
      setDecisionNode(null);
    }
  }

  if (notWorkflow) return null;
  if (!workflow && !error) return <section className="run-evidence-section" aria-labelledby="workflow-plan-heading"><h3 id="workflow-plan-heading">Multi-agent workflow</h3><p className="muted" role="status">Loading persisted workflow plan…</p></section>;
  if (!workflow && error) return <section className="run-evidence-section" aria-labelledby="workflow-plan-heading"><div className="section-heading"><h3 id="workflow-plan-heading">Multi-agent workflow</h3><button className="button button-secondary" type="button" onClick={() => setReloadKey((key) => key + 1)}>Retry</button></div><p className="inline-error" role="alert">{error}</p></section>;
  if (!workflow) return null;
  return (
    <section className="run-evidence-section workflow-run-panel" aria-labelledby="workflow-plan-heading">
      <div className="section-heading"><div><h3 id="workflow-plan-heading">Persisted workflow plan</h3><span className="muted">Live plan projection · refreshes every 5 seconds</span></div><StatusPill tone={workflow.status === "succeeded" ? "green" : "amber"}>{workflow.status}</StatusPill></div>
      <dl className="run-facts workflow-plan-facts"><div><span>Parent Run</span><strong>{workflow.parent_run_id}</strong></div><div><span>Plan digest</span><strong>{workflow.plan_digest}</strong></div><div><span>Plan revision</span><strong>{workflow.plan_revision}</strong></div><div><span>Delegations</span><strong>{workflow.delegations.length}</strong></div></dl>
      {decisionError ? <p className="inline-error" role="alert">{decisionError}</p> : null}
      <div className="workflow-node-list">{workflow.delegations.map((node) => <WorkflowNodeCard key={node.node_id} node={node} busy={decisionNode === node.node_id} onDecision={(item, decision) => void decide(item, decision)} />)}</div>
    </section>
  );
}
