"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { requestRun, requestTaskRunPage, type RunRecord } from "../lib/runs";
import { requestTaskData, type Task } from "../lib/tasks";
import { PageHeader, StatusPill } from "./workspace-components";

const TASK_PAGE_SIZE = 100;
const RUN_PAGE_SIZE = 20;

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Timestamp unavailable" : date.toLocaleString();
}

function RunRow({ run }: { run: RunRecord }) {
  const snapshot = run.configuration_snapshot;
  const status = run.status === "created" ? "Created · not dispatched" : `Unknown status · ${run.status}`;
  return (
    <article className="run-record-card">
      <div className="run-record-heading">
        <div><p className="eyebrow">Attempt {run.attempt_number} · Task revision {run.task_revision}</p><h4><Link className="task-title-link" href={`/runs/${run.id}`}>Run {run.id}</Link></h4></div>
        <StatusPill>{status}</StatusPill>
      </div>
      <dl className="run-record-facts">
        <div><dt>Created</dt><dd><time dateTime={run.created_at}>{formatDate(run.created_at)}</time></dd></div>
        <div><dt>Agent instance</dt><dd>{snapshot.agent_instance.display_name}</dd></div>
        <div><dt>Definition version</dt><dd>{snapshot.agent_definition.name} · v{snapshot.agent_definition.version}</dd></div>
        <div><dt>Skills pinned</dt><dd>{snapshot.skills.length}</dd></div>
      </dl>
      <p className="field-note">Immutable creation record only. No runtime events, dispatch, or execution status are available.</p>
    </article>
  );
}

export function TaskRunHistory({ taskId, enabled = true }: { taskId: string; enabled?: boolean }) {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!enabled || runs !== null) return;
    const controller = new AbortController();
    setError(null);
    void requestTaskRunPage(taskId, { limit: RUN_PAGE_SIZE, signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        setRuns(page.runs);
        setHasMore(page.hasMore);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load Run records.");
      });
    return () => controller.abort();
  }, [enabled, reloadKey, runs, taskId]);

  async function loadMore() {
    if (!runs || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await requestTaskRunPage(taskId, { limit: RUN_PAGE_SIZE, offset: runs.length });
      setRuns((current) => current ? [...current, ...page.runs] : page.runs);
      setHasMore(page.hasMore);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load more Run records.");
    } finally {
      setLoadingMore(false);
    }
  }

  if (!enabled && runs === null) return null;
  if (error && runs === null) return <div><p className="inline-error" role="alert">{error}</p><button className="button button-secondary" type="button" onClick={() => { setError(null); setReloadKey((current) => current + 1); }}>Retry loading attempts</button></div>;
  if (runs === null) return <p className="muted" role="status">Loading Run records…</p>;
  return (
    <div className="run-history-list" aria-live="polite">
      {runs.length === 0 ? <p className="muted">No Run / Attempt creation records are persisted for this task.</p> : runs.map((run) => <RunRow key={run.id} run={run} />)}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {hasMore ? <button className="button button-secondary" type="button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load more attempts"}</button> : null}
    </div>
  );
}

function TaskRunGroup({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="run-task-group" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="run-task-summary"><strong>{task.title}</strong><span>{task.project_name} · {task.agent_instance_name ?? "Unassigned"} · Revision {task.revision}</span></span>
        <span className="run-task-open-label">{open ? "Hide attempts" : "View attempts"}</span>
      </summary>
      <TaskRunHistory taskId={task.id} enabled={open} />
    </details>
  );
}

export function RunList() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [query, setQuery] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void requestTaskData<Task[]>(`/api/v1/tasks?limit=${TASK_PAGE_SIZE}&offset=0`, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        setTasks(page);
        setHasMore(page.length === TASK_PAGE_SIZE);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load tasks.");
      });
    return () => controller.abort();
  }, [reloadKey]);

  const filteredTasks = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (tasks ?? []).filter((task) => `${task.title} ${task.project_name} ${task.agent_instance_name ?? "Unassigned"}`.toLocaleLowerCase().includes(needle));
  }, [query, tasks]);

  async function loadMoreTasks() {
    if (!tasks || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await requestTaskData<Task[]>(`/api/v1/tasks?limit=${TASK_PAGE_SIZE}&offset=${tasks.length}`);
      setTasks((current) => current ? [...current, ...page] : page);
      setHasMore(page.length === TASK_PAGE_SIZE);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load more tasks.");
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="content-section" aria-label="Persisted tasks and Run attempts">
      <div className="section-heading"><h2>Attempts by task</h2><StatusPill>API-backed records</StatusPill></div>
      <p className="muted">Run records are listed under their persisted task because the current API exposes attempts per task. Expand a task to load its immutable creation records. Loading a record does not start work.</p>
      <label className="search-field run-index-search"><span className="visually-hidden">Search tasks with Run records</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, project, or agent…" /></label>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {error && tasks === null ? <button className="button button-secondary" type="button" onClick={() => { setError(null); setReloadKey((current) => current + 1); }}>Retry loading tasks</button> : null}
      {tasks === null && !error ? <p className="muted" role="status">Loading tasks…</p> : null}
      {tasks?.length === 0 ? <div className="empty-state"><strong>No persisted tasks</strong><span>Run records can only be associated with a task.</span><Link className="text-link" href="/tasks/new">Create task</Link></div> : null}
      {tasks && tasks.length > 0 && filteredTasks.length === 0 ? <p className="muted" role="status">No tasks match this search.</p> : null}
      {filteredTasks.length ? <div className="run-index-list">{filteredTasks.map((task) => <TaskRunGroup key={task.id} task={task} />)}</div> : null}
      {hasMore ? <button className="button button-secondary" type="button" onClick={() => void loadMoreTasks()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load more tasks"}</button> : null}
    </section>
  );
}

export function RunDetailView({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setRun(null);
    setError(null);
    const controller = new AbortController();
    void requestRun(runId, controller.signal)
      .then((record) => {
        if (!controller.signal.aborted) setRun(record);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load this Run record.");
      });
    return () => controller.abort();
  }, [reloadKey, runId]);

  if (error) return <div className="page-stack"><PageHeader eyebrow="Run / Attempt" title="Run record unavailable" description={error} /><button className="button button-secondary" type="button" onClick={() => { setError(null); setReloadKey((current) => current + 1); }}>Retry loading Run record</button><Link className="text-link" href="/runs">Back to Runs</Link></div>;
  if (!run) return <p className="muted" role="status">Loading Run record…</p>;

  const snapshot = run.configuration_snapshot;
  const task = snapshot.task;
  const createdStatus = run.status === "created";
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Persisted Run / Attempt" title={`Attempt ${run.attempt_number}`} description="This immutable record freezes the selected Task revision and configuration at creation. It is not a runtime session and has not been dispatched." >
        <Link className="button button-secondary" href={`/tasks/${run.task_id}`}>Open task</Link>
        <Link className="text-link" href="/runs">All Runs</Link>
      </PageHeader>
      <div className="run-layout">
        <section className="content-section run-main" aria-labelledby="run-record-heading">
          <div className="run-title-row"><div><p className="eyebrow">Run ID · {run.id}</p><h2 id="run-record-heading">{task.title}</h2></div><StatusPill tone={createdStatus ? "neutral" : "amber"}>{createdStatus ? "Created · not dispatched" : `Unknown status · ${run.status}`}</StatusPill></div>
          <p className="muted">Creation timestamp: <time dateTime={run.created_at}>{formatDate(run.created_at)}</time></p>
          <section className="run-evidence-section" aria-labelledby="snapshot-heading"><h3 id="snapshot-heading">Immutable configuration snapshot</h3>
            <div className="settings-row"><span>Task revision</span><strong>{task.revision}</strong></div>
            <div className="settings-row"><span>Project</span><strong>{task.project_name}</strong></div>
            <div className="settings-row"><span>Assigned Agent Instance</span><strong>{snapshot.agent_instance.display_name}</strong></div>
            <div className="settings-row"><span>Agent Definition version</span><strong>{snapshot.agent_definition.name} · v{snapshot.agent_definition.version}</strong></div>
            <div className="settings-row"><span>Pinned Skills</span><strong>{snapshot.skills.length ? snapshot.skills.map((skill) => `${skill.name} · v${skill.version}`).join(", ") : "None"}</strong></div>
            <div className="settings-row"><span>Provider configuration</span><strong>{snapshot.provider === null ? "Not configured" : "Present in snapshot"}</strong></div>
            <div className="settings-row"><span>Runtime configuration</span><strong>{snapshot.runtime === null ? "Not configured" : "Present in snapshot"}</strong></div>
          </section>
          <section className="run-evidence-section" aria-labelledby="evidence-heading"><h3 id="evidence-heading">Runtime evidence</h3><div className="empty-state"><strong>No runtime evidence is persisted</strong><span>Event ingestion, dispatch, execution, artifacts, approvals, and completion state are not available.</span></div></section>
        </section>
        <aside className="run-side-panel">
          <section className="content-section"><h2>Task context</h2><StatusPill>Task revision {task.revision}</StatusPill><p>{task.goal}</p><Link className="text-link" href={`/tasks/${run.task_id}`}>Open task record</Link></section>
          <section className="content-section"><h2>Attempt identity</h2><dl className="preview-facts"><div><dt>Attempt number</dt><dd>{run.attempt_number}</dd></div><div><dt>Request ID</dt><dd>{run.request_id}</dd></div><div><dt>Task</dt><dd><Link href={`/tasks/${run.task_id}`}>{run.task_id}</Link></dd></div></dl><p className="field-note">Run status is separate from Task stage. No runtime lifecycle or status transition is implied.</p></section>
        </aside>
      </div>
    </div>
  );
}

