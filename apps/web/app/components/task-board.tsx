"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { requestTaskData, requestTasks, type Task } from "../lib/tasks";
import { StatusPill } from "./workspace-components";

const PAGE_SIZE = 100;

export function TaskBoard() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void requestTasks(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setTasks(result);
        setHasMore(result.length === PAGE_SIZE);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Could not load tasks.");
        }
      });
    return () => controller.abort();
  }, []);

  const filteredTasks = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return (tasks ?? []).filter((task) => {
      const matchesQuery = `${task.title} ${task.goal} ${task.project_name} ${task.agent_instance_name ?? "Unassigned"}`
        .toLowerCase().includes(normalizedQuery);
      return matchesQuery
        && (projectFilter === "all" || task.project_id === projectFilter)
        && (agentFilter === "all"
          || (agentFilter === "unassigned" ? task.agent_instance_id === null : task.agent_instance_id === agentFilter));
    });
  }, [agentFilter, projectFilter, query, tasks]);

  const projects = [...new Map((tasks ?? []).map((task) => [task.project_id, task.project_name])).entries()];
  const agents = [...new Map(
    (tasks ?? [])
      .filter((task) => task.agent_instance_id !== null)
      .map((task) => [task.agent_instance_id as string, task.agent_instance_name ?? "Agent instance"]),
  ).entries()];

  async function loadMore() {
    if (!tasks || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await requestTaskData<Task[]>(`/api/v1/tasks?limit=${PAGE_SIZE}&offset=${tasks.length}`);
      setTasks((current) => current ? [...current, ...next] : next);
      setHasMore(next.length === PAGE_SIZE);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load more tasks.");
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="content-section" aria-label="Persisted task board">
      <div className="section-heading">
        <h2>Backlog</h2>
        <StatusPill>Task stage</StatusPill>
      </div>
      <div className="list-toolbar board-toolbar">
        <label className="search-field">
          <span className="visually-hidden">Search tasks</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, project, or agent…" />
        </label>
        <label className="filter-field">
          <span className="visually-hidden">Filter by project</span>
          <select value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
            <option value="all">All projects</option>
            {projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label className="filter-field">
          <span className="visually-hidden">Filter by assigned agent</span>
          <select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}>
            <option value="all">All agents</option>
            <option value="unassigned">Unassigned</option>
            {agents.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
      </div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {tasks === null && !error ? <p className="muted" role="status">Loading tasks…</p> : null}
      {tasks?.length === 0 ? (
        <div className="empty-state">
          <strong>No tasks yet</strong>
          <span>Create a task to add real persisted work to the Backlog.</span>
          <Link className="text-link" href="/tasks/new">Create task</Link>
        </div>
      ) : null}
      {tasks && tasks.length > 0 && filteredTasks.length === 0 ? (
        <p className="muted">No tasks match the current filters.</p>
      ) : null}
      {filteredTasks.length > 0 ? (
        <div className="board-scroll">
          <div className="task-board">
            <section className="task-column" aria-label={`Backlog, ${filteredTasks.length} tasks`}>
              <header className="task-column-heading"><h3>Backlog</h3><span>{filteredTasks.length}</span></header>
              {filteredTasks.map((task) => (
                <article className="task-card" key={task.id}>
                  <div className="task-card-top"><StatusPill>Revision {task.revision}</StatusPill><span className="task-project">{task.project_name}</span></div>
                  <h3><Link className="task-title-link" href={`/tasks/${task.id}`}>{task.title}</Link></h3>
                  <p className="task-owner">{task.agent_instance_name ?? "Unassigned · assignment is explicit"}</p>
                  <p className="task-updated">Updated {new Date(task.updated_at).toLocaleString()}</p>
                  <div className="task-card-actions"><Link className="text-link" href={`/tasks/${task.id}`}>Open task</Link></div>
                </article>
              ))}
            </section>
          </div>
        </div>
      ) : null}
      {hasMore ? <button className="button button-secondary" type="button" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load more tasks"}</button> : null}
      <p className="field-note">Only persisted Backlog tasks are shown. Run/Attempt records and workflow transitions are not implemented in this slice.</p>
    </section>
  );
}
