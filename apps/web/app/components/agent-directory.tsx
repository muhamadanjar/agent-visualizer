"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { sampleAgents } from "../lib/sample-data";
import { DisabledWorkAction, StatusPill, ViewLink } from "./workspace-components";

const presenceTone: Record<string, string> = { "At desk": "green", Idle: "neutral", "Needs attention": "amber" };

export function AgentDirectory() {
  const [query, setQuery] = useState("");
  const [presence, setPresence] = useState("All sample agents");
  const [project, setProject] = useState("All projects");
  const [role, setRole] = useState("All roles");
  const agents = useMemo(() => sampleAgents.filter((agent) => {
    const matchesQuery = `${agent.name} ${agent.role} ${agent.project} ${agent.room}`.toLowerCase().includes(query.trim().toLowerCase());
    return matchesQuery
      && (presence === "All sample agents" || agent.presence === presence)
      && (project === "All projects" || agent.project === project)
      && (role === "All roles" || agent.role === role);
  }), [presence, project, query, role]);

  return (
    <>
      <div className="list-toolbar">
        <label className="search-field"><span className="visually-hidden">Search sample agents</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search agents, role, or project…" /></label>
        <label className="filter-field"><span className="visually-hidden">Filter by sample presence</span><select value={presence} onChange={(event) => setPresence(event.target.value)}><option>All sample agents</option><option>At desk</option><option>Idle</option><option>Needs attention</option></select></label>
        <label className="filter-field"><span className="visually-hidden">Filter by project</span><select value={project} onChange={(event) => setProject(event.target.value)}><option>All projects</option>{[...new Set(sampleAgents.map((agent) => agent.project))].map((name) => <option key={name}>{name}</option>)}</select></label>
        <label className="filter-field"><span className="visually-hidden">Filter by role</span><select value={role} onChange={(event) => setRole(event.target.value)}><option>All roles</option>{[...new Set(sampleAgents.map((agent) => agent.role))].map((name) => <option key={name}>{name}</option>)}</select></label>
        <span className="result-count">{agents.length} of {sampleAgents.length}</span>
      </div>
      {agents.length ? (
        <div className="agent-grid">
          {agents.map((agent) => (
            <article className="agent-card" key={agent.id}>
              <div className="agent-card-head">
                <span className="agent-avatar" style={{ "--avatar-color": agent.color } as CSSProperties} aria-hidden="true">{agent.name.slice(0, 1)}</span>
                <div className="agent-card-title"><h2><Link href={`/agents/${agent.id}`}>{agent.name}</Link></h2><p>{agent.role}</p></div>
              </div>
              <p className="agent-summary">{agent.summary}</p>
              <dl className="agent-meta"><div><dt>Project</dt><dd>{agent.project}</dd></div><div><dt>Office</dt><dd>{agent.room}</dd></div></dl>
              <div className="agent-card-footer">
                {agent.runId ? <ViewLink href={`/runs/${agent.runId}`}>Open sample run</ViewLink> : <span className="muted">No sample run</span>}
                <DisabledWorkAction>Start task</DisabledWorkAction>
              </div>
              <span className="sample-card-label">Sample agent record</span>
            </article>
          ))}
        </div>
      ) : <div className="empty-state"><strong>No sample agents match.</strong><span>Try another name, role, project, or presence filter.</span></div>}
    </>
  );
}
