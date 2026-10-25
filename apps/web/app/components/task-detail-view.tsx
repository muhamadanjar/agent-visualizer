"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { requestTask, requestTaskRevisions, type Task } from "../lib/tasks";
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
          <section className="content-section">
            <h2>Run / Attempt history</h2>
            <p>Run / Attempt creation records are append-only snapshots. No dispatcher, runtime execution, event stream, or Task-stage transition is connected.</p>
            <TaskRunHistory key={task.id} taskId={task.id} />
            <Link className="text-link" href="/runs">Open Runs index</Link>
          </section>
        </aside>
      </div>
    </div>
  );
}

