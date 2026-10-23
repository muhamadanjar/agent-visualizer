"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { sampleAgents, sampleTaskStages, sampleTasks } from "../lib/sample-data";
import { DisabledWorkAction, StatusPill } from "./workspace-components";

export function TaskBoard() {
  const [query, setQuery] = useState("");
  const [stageFilter, setStageFilter] = useState("All stages");
  const [projectFilter, setProjectFilter] = useState("All projects");
  const [agentFilter, setAgentFilter] = useState("All agents");
  const tasks = useMemo(() => sampleTasks.filter((task) => {
    const owner = sampleAgents.find((agent) => agent.id === task.agentId)?.name ?? "Unassigned";
    const matchesQuery = `${task.title} ${task.project} ${owner}`.toLowerCase().includes(query.trim().toLowerCase());
    return matchesQuery
      && (stageFilter === "All stages" || task.stage === stageFilter)
      && (projectFilter === "All projects" || task.project === projectFilter)
      && (agentFilter === "All agents" || owner === agentFilter);
  }), [agentFilter, projectFilter, query, stageFilter]);

  return (
    <>
      <div className="list-toolbar board-toolbar">
        <label className="search-field"><span className="visually-hidden">Search sample tasks</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search task, project, or agent…" /></label>
        <label className="filter-field"><span className="visually-hidden">Filter by workflow stage</span><select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}><option>All stages</option>{sampleTaskStages.map((stage) => <option key={stage}>{stage}</option>)}</select></label>
        <label className="filter-field"><span className="visually-hidden">Filter by project</span><select value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}><option>All projects</option>{[...new Set(sampleTasks.map((task) => task.project))].map((project) => <option key={project}>{project}</option>)}</select></label>
        <label className="filter-field"><span className="visually-hidden">Filter by assigned agent</span><select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}><option>All agents</option><option>Unassigned</option>{sampleAgents.map((agent) => <option key={agent.id}>{agent.name}</option>)}</select></label>
        <p className="workflow-note">Task stage and run status are separate sample fields.</p>
      </div>
      <div className="board-scroll" aria-label="Sample task board">
        <div className="task-board">
          {sampleTaskStages.map((stage) => {
            const columnTasks = tasks.filter((task) => task.stage === stage);
            return (
              <section className="task-column" key={stage} aria-label={`${stage}, ${columnTasks.length} sample tasks`}>
                <header className="task-column-heading"><h2>{stage}</h2><span>{columnTasks.length}</span></header>
                {columnTasks.map((task) => {
                  const agent = sampleAgents.find((item) => item.id === task.agentId);
                  return (
                    <article className="task-card" key={task.id}>
                      <div className="task-card-top"><span className="sample-card-label">Sample</span><span className="task-project">{task.project}</span></div>
                      <h3><Link className="task-title-link" href={`/tasks/${task.id}`}>{task.title}</Link></h3>
                      <p className="task-owner">{agent ? `Assigned sample agent · ${agent.name}` : "Unassigned sample task"}</p>
                      <div className="task-card-status">
                        <StatusPill tone="neutral">Task · {task.stage}</StatusPill>
                        <StatusPill tone={task.runStatus === "Running" ? "amber" : task.runStatus === "Succeeded" ? "green" : "neutral"}>Run · {task.runStatus ?? "None"}</StatusPill>
                      </div>
                      <p className="task-updated">{task.updated}</p>
                      <div className="task-card-actions">
                        {task.runId ? <Link className="text-link" href={`/runs/${task.runId}`}>View sample run</Link> : <span className="muted">No sample run</span>}
                        <DisabledWorkAction>{task.stage === "Ready" ? "Start" : "Assign"}</DisabledWorkAction>
                      </div>
                    </article>
                  );
                })}
                {!columnTasks.length ? <p className="column-empty">No matching sample tasks</p> : null}
              </section>
            );
          })}
        </div>
      </div>
    </>
  );
}
