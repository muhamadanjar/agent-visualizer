"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { requestTask, requestTaskPreflight, requestTaskRevisions, type Task, type TaskPreflight } from "../lib/tasks";
import { TaskRunHistory } from "./run-list";
import { PageHeader, StatusPill } from "./workspace-components";

function RevisionContents({ task }: { task: Task }) {
  return (
    <div className="revision-content">
      <h4>{task.title}</h4>
      <p>{task.goal}</p>
      <h5>Project and assignment</h5>
      <p>{task.project_name} · {task.agent_instance_name ?? "Unassigned"}</p>
      <h5>Execution bounds</h5>
      <p>Maximum duration: {task.max_duration_minutes ?? "not set"} · Maximum child runs: {task.max_child_runs ?? "not set"}</p>
      {task.input_text ? <><h5>Inputs</h5><p>{task.input_text}</p></> : null}
      <h5>Resource references</h5>
      {task.resource_refs.length ? <ul>{task.resource_refs.map((reference, index) => <li key={`${index}-${reference}`}>{reference}</li>)}</ul> : <p className="muted">None</p>}
      <h5>Acceptance criteria</h5>
      <ul>{task.acceptance_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul>
      <h5>Blocked by</h5>
      {task.blocked_by_task_ids.length
        ? <ul>{task.blocked_by_task_ids.map((dependencyId) => <li key={dependencyId}><Link href={`/tasks/${dependencyId}`}>{dependencyId}</Link></li>)}</ul>
        : <p className="muted">No task dependencies.</p>}
    </div>
  );
}

const REVISION_PAGE_SIZE = 100;
const PREFLIGHT_LABELS: Record<string, string> = {
  agent_assignment: "Agent Instance assignment",
  model_endpoint: "Model Endpoint",
  workspace_policy: "Workspace policy",
  tool_allowlist: "Tool allowlist",
  runner_connection: "Runner connection",
};

function TaskPreflightPanel({ taskId }: { taskId: string }) {
  const [preflight, setPreflight] = useState<TaskPreflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const [startingRun, setStartingRun] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [createdRunId, setCreatedRunId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setPreflight(null);
    setError(null);
    setLoading(true);
    void requestTaskPreflight(taskId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setPreflight(result);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Could not load Task preflight.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [taskId, refreshGeneration]);

  async function startRun() {
    if (!preflight?.ready || startingRun) return;
    setStartingRun(true);
    setStartError(null);
    try {
      const response = await fetch(`/api/v1/tasks/${encodeURIComponent(taskId)}/runs`, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      });
      const body = await response.json().catch(() => null) as { id?: unknown; detail?: unknown } | null;
      if (!response.ok || typeof body?.id !== "string") {
        const detail = typeof body?.detail === "string"
          ? body.detail
          : typeof body?.detail === "object" && body.detail !== null && "message" in body.detail
            ? String(body.detail.message)
            : "Run could not be created. Recheck the policy and Runner connection.";
        throw new Error(detail);
      }
      setCreatedRunId(body.id);
    } catch (reason) {
      setStartError(reason instanceof Error ? reason.message : "Run could not be created.");
    } finally {
      setStartingRun(false);
    }
  }

  return (
    <section className="content-section" aria-labelledby="task-preflight-heading">
      <div className="section-heading">
        <h2 id="task-preflight-heading">Execution preflight</h2>
        <button
          className="button button-secondary"
          disabled={loading}
          onClick={() => setRefreshGeneration((generation) => generation + 1)}
          type="button"
        >
          {loading ? "Checking…" : "Recheck"}
        </button>
      </div>
      <p className="field-note">Readiness gates must pass before a Run can be created.</p>
      {loading ? <p role="status">Checking Task readiness…</p> : null}
      {error ? (
        <div className="preflight-error" role="alert">
          <p>Preflight could not be loaded: {error}</p>
          <p>No Run can be started while the checks are unavailable.</p>
        </div>
      ) : null}
      {preflight ? (
        <>
          <p className={`preflight-summary${preflight.ready ? " is-ready" : ""}`} role="status">
            {preflight.ready
              ? "All reported checks are ready. Starting a Run requires an explicit action."
              : "Run start is blocked: one or more required checks are blocked or unavailable."}
          </p>
          <ul className="preflight-list">
            {preflight.checks.map((check) => (
              <li className="preflight-row" key={check.key}>
                <div className="preflight-row-heading">
                  <strong>{PREFLIGHT_LABELS[check.key] ?? check.key.replaceAll("_", " ")}</strong>
                  <StatusPill tone={check.status === "ready" ? "green" : check.status === "blocked" ? "amber" : "neutral"}>
                    {check.status === "ready" ? "Ready" : check.status === "blocked" ? "Blocked" : "Unavailable"}
                  </StatusPill>
                </div>
                <p>{check.detail}</p>
              </li>
            ))}
          </ul>
          {preflight.ready ? (
            <div className="form-actions">
              {createdRunId ? (
                <Link className="button" href={`/runs/${encodeURIComponent(createdRunId)}`}>Open Run</Link>
              ) : (
                <button className="button" type="button" disabled={startingRun} onClick={() => void startRun()}>
                  {startingRun ? "Queueing Run…" : "Start Run"}
                </button>
              )}
            </div>
          ) : null}
          {startError ? <p className="inline-error" role="alert">{startError}</p> : null}
        </>
      ) : null}
    </section>
  );
}

export function TaskDetailView({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<Task | null>(null);
  const [revisions, setRevisions] = useState<Task[] | null>(null);
  const [revisionOffset, setRevisionOffset] = useState(0);
  const [hasOlderRevisions, setHasOlderRevisions] = useState(false);
  const [loadingOlderRevisions, setLoadingOlderRevisions] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const historyRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    setTask(null);
    setRevisions(null);
    setRevisionOffset(0);
    setHasOlderRevisions(false);
    setLoadingOlderRevisions(false);
    setRevisionError(null);
    setError(null);
    const controller = new AbortController();
    historyRequest.current?.abort();
    historyRequest.current = controller;
    void requestTask(taskId, controller.signal).then(async (loadedTask) => {
      const offset = Math.max(0, loadedTask.revision - REVISION_PAGE_SIZE);
      const loadedRevisions = await requestTaskRevisions(taskId, {
        limit: REVISION_PAGE_SIZE,
        offset,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setTask(loadedTask);
      setRevisions(loadedRevisions);
      setRevisionOffset(offset);
      setHasOlderRevisions(offset > 0);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Could not load task.");
      }
    });
    return () => {
      controller.abort();
      historyRequest.current?.abort();
    };
  }, [taskId]);

  async function loadOlderRevisions() {
    if (!hasOlderRevisions || loadingOlderRevisions) return;
    const olderOffset = Math.max(0, revisionOffset - REVISION_PAGE_SIZE);
    const olderLimit = revisionOffset - olderOffset;
    const controller = new AbortController();
    historyRequest.current?.abort();
    historyRequest.current = controller;
    setLoadingOlderRevisions(true);
    setRevisionError(null);
    try {
      const olderRevisions = await requestTaskRevisions(taskId, {
        limit: olderLimit,
        offset: olderOffset,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setRevisions((current) => current ? [...olderRevisions, ...current] : olderRevisions);
      setRevisionOffset(olderOffset);
      setHasOlderRevisions(olderOffset > 0);
    } catch (reason: unknown) {
      if (!controller.signal.aborted) {
        setRevisionError(reason instanceof Error ? reason.message : "Could not load older revisions.");
      }
    } finally {
      if (!controller.signal.aborted) setLoadingOlderRevisions(false);
    }
  }

  if (error) {
    return <div className="page-stack"><PageHeader eyebrow="Task record" title="Task unavailable" description={error} /><Link className="text-link" href="/tasks">Back to tasks</Link></div>;
  }
  if (!task || !revisions) return <main className="workspace-gate" role="status">Loading task…</main>;

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Task record" title={task.title} description={task.goal}>
        <Link className="text-link" href="/tasks">Back to tasks</Link>
        <Link className="button button-secondary" href={`/tasks/${task.id}/edit`}>Edit task</Link>
      </PageHeader>
      <div className="task-detail-layout">
        <section className="content-section task-detail-main">
          <div className="task-card-status"><StatusPill>Task · {task.stage}</StatusPill><StatusPill>Revision {task.revision}</StatusPill></div>
          <h2>Inputs and resources</h2>
          {task.input_text ? <p>{task.input_text}</p> : <p className="muted">No input text.</p>}
          {task.resource_refs.length ? <ul>{task.resource_refs.map((reference, index) => <li key={`${index}-${reference}`}>{reference}</li>)}</ul> : <p className="muted">No resource references.</p>}
          <p className="field-note">Resource references are inert stored text; they do not grant access.</p>
          <h2>Acceptance criteria</h2>
          <ul className="criteria-list">{task.acceptance_criteria.map((criterion) => <li key={criterion}>{criterion}</li>)}</ul>
          <h2>Dependencies</h2>
          {task.blocked_by_task_ids.length
            ? <ul>{task.blocked_by_task_ids.map((dependencyId) => <li key={dependencyId}><Link href={`/tasks/${dependencyId}`}>Task {dependencyId}</Link></li>)}</ul>
            : <p className="muted">No task dependencies.</p>}
          <h2>Revision history</h2>
          {revisionError ? <p role="alert">{revisionError}</p> : null}
          {hasOlderRevisions ? (
            <button
              className="button button-secondary"
              disabled={loadingOlderRevisions}
              onClick={() => void loadOlderRevisions()}
              type="button"
            >
              {loadingOlderRevisions ? "Loading older revisions…" : "Load older revisions"}
            </button>
          ) : null}
          <div className="revision-list">
            {revisions.map((revision) => (
              <details key={revision.revision} className="revision-item" open={revision.revision === task.revision}>
                <summary><span>Revision {revision.revision} · {revision.title}</span><time dateTime={revision.revision_created_at}>{new Date(revision.revision_created_at).toLocaleString()}</time></summary>
                <RevisionContents task={revision} />
              </details>
            ))}
          </div>
        </section>
        <aside className="task-detail-side">
          <section className="content-section">
            <h2>Task specification</h2>
            <dl className="preview-facts">
              <div><dt>Project</dt><dd><Link href={`/projects/${task.project_id}`}>{task.project_name}</Link></dd></div>
              <div><dt>Assignment</dt><dd>{task.agent_instance_name ?? "Unassigned"}</dd></div>
              <div><dt>Stage</dt><dd>{task.stage}</dd></div>
              <div><dt>Maximum duration</dt><dd>{task.max_duration_minutes === null ? "Not set" : `${task.max_duration_minutes} minutes`}</dd></div>
              <div><dt>Maximum child runs</dt><dd>{task.max_child_runs === null ? "Not set" : task.max_child_runs}</dd></div>
              <div><dt>Updated</dt><dd><time dateTime={task.updated_at}>{new Date(task.updated_at).toLocaleString()}</time></dd></div>
            </dl>
          </section>
          <TaskPreflightPanel key={task.id} taskId={task.id} />
          <section className="content-section">
            <h2>Run / Attempt history</h2>
            <p>Run / Attempt records preserve immutable configuration snapshots. Starting a Run queues it after preflight; persisted lifecycle events appear on Run detail. Task-stage transitions are not implemented.</p>
            <TaskRunHistory key={task.id} taskId={task.id} />
            <Link className="text-link" href="/runs">Open Runs index</Link>
          </section>
        </aside>
      </div>
    </div>
  );
}

