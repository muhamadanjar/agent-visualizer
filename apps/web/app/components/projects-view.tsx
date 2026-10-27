"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ProjectExecutionPolicy } from "./project-execution-policy";
import { PageHeader, StatusPill } from "./workspace-components";

type Project = {
  id: string;
  name: string;
  summary: string;
  created_at: string;
};

type ProjectResponse = Project | Project[];

type AgentInstanceSummary = {
  id: string;
  display_name: string;
  definition_name: string;
  project_name: string | null;
};

async function requestAgentInstances(projectId: string, signal: AbortSignal): Promise<AgentInstanceSummary[]> {
  const response = await fetch(`/api/v1/agent-instances?project_id=${encodeURIComponent(projectId)}`, {
    cache: "no-store",
    credentials: "same-origin",
    signal,
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error("Could not load project agent instances.");
  return response.json() as Promise<AgentInstanceSummary[]>;
}

async function requestProjects(path: string, init?: RequestInit): Promise<ProjectResponse> {
  const headers = new Headers(init?.headers);
  headers.set("accept", "application/json");
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) throw new Error(response.status === 404 ? "Project not found or not accessible." : "Could not load project data. Check the API and try again.");
  return response.json() as Promise<ProjectResponse>;
}

export function ProjectsView() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void requestProjects("/api/v1/projects", { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!Array.isArray(result)) throw new Error("Unexpected project response.");
        setProjects(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : "Could not load project data.");
      });
    return () => controller.abort();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    setCreating(true);
    setCreateError(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const result = await requestProjects("/api/v1/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: form.get("name"), summary: form.get("summary") ?? "" }),
      });
      if (Array.isArray(result)) throw new Error("Unexpected project response.");
      setProjects((current) => current ? [...current, result] : [result]);
      formElement.reset();
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Could not create project.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Workspace context" title="Projects" description="Projects are persisted by the API and scoped to the authenticated owner.">
        <StatusPill tone="neutral">Owner scoped</StatusPill>
      </PageHeader>
      <form className="content-section draft-form" onSubmit={handleCreate}>
        <div className="section-heading"><h2>Create project</h2><StatusPill>API backed</StatusPill></div>
        <label className="preview-field">Name<input name="name" required minLength={1} maxLength={120} autoComplete="off" /></label>
        <label className="preview-field">Summary<textarea name="summary" rows={3} maxLength={500} /></label>
        {createError ? <p className="inline-error" role="alert">{createError}</p> : null}
        <div className="form-actions"><button className="button" type="submit" disabled={creating}>{creating ? "Creating…" : "Create project"}</button></div>
      </form>
      <section className="content-section" aria-labelledby="project-list-heading">
        <div className="section-heading"><h2 id="project-list-heading">Your projects</h2><StatusPill>{projects?.length ?? "…"}</StatusPill></div>
        {loadError ? <p className="inline-error" role="alert">{loadError}</p> : null}
        {projects === null && !loadError ? <p className="muted" role="status">Loading projects…</p> : null}
        {projects?.length === 0 ? <p className="muted">No projects yet. Create one above.</p> : null}
        {projects && projects.length > 0 ? (
          <div className="preview-grid">
            {projects.map((project) => (
              <article className="preview-card" key={project.id}>
                <div className="preview-card-heading"><h3>{project.name}</h3><StatusPill>Project</StatusPill></div>
                <p>{project.summary || "No summary provided."}</p>
                <Link className="text-link" href={`/projects/${project.id}`}>Open project</Link>
              </article>
            ))}
          </div>
        ) : null}
      </section>
      <p className="muted">Agent instances can be linked to projects. Repository connections, tasks, and runtime execution remain unconfigured.</p>
    </div>
  );
}

export function ProjectDetailView({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [instances, setInstances] = useState<AgentInstanceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [instanceError, setInstanceError] = useState<string | null>(null);

  useEffect(() => {
    setProject(null);
    setInstances(null);
    setError(null);
    setInstanceError(null);
    const controller = new AbortController();
    void requestProjects(`/api/v1/projects/${encodeURIComponent(projectId)}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (Array.isArray(result)) throw new Error("Unexpected project response.");
        setProject(result);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load project data.");
      });
    void requestAgentInstances(projectId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setInstances(result); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setInstanceError(reason instanceof Error ? reason.message : "Could not load project agents.");
      });
    return () => controller.abort();
  }, [projectId]);

  if (error) {
    return <div className="page-stack"><PageHeader eyebrow="Project scope" title="Project unavailable" description={error} /><Link className="text-link" href="/projects">Back to projects</Link></div>;
  }
  if (!project) return <main className="workspace-gate" role="status">Loading project…</main>;

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Project scope" title={project.name} description={project.summary || "No summary provided."}>
        <StatusPill>Owner scoped</StatusPill>
      </PageHeader>
      <section className="content-section">
        <div className="section-heading"><h2>Project details</h2><StatusPill>Persisted</StatusPill></div>
        <dl className="preview-facts">
          <div><dt>Project ID</dt><dd>{project.id}</dd></div>
          <div><dt>Created</dt><dd>{new Date(project.created_at).toLocaleString()}</dd></div>
        </dl>
      </section>
      <ProjectExecutionPolicy projectId={project.id} />
      <section className="content-section">
        <div className="section-heading"><h2>Assigned agent instances</h2><Link className="text-link" href="/agents/new">Add instance</Link></div>
        {instanceError ? <p className="inline-error" role="alert">{instanceError}</p> : null}
        {!instances && !instanceError ? <p className="muted" role="status">Loading project agents…</p> : null}
        {instances?.length === 0 ? <p className="muted">No active agent instances are assigned to this project.</p> : null}
        {instances?.length ? <div className="preview-grid">{instances.map((instance) => (
          <article className="preview-card" key={instance.id}>
            <div className="preview-card-heading"><h3><Link href={`/agents/${instance.id}`}>{instance.display_name}</Link></h3><StatusPill>Registered</StatusPill></div>
            <p>{instance.definition_name}</p>
          </article>
        ))}</div> : null}
      </section>
      <p className="muted">Project association is context only; it does not grant repository access. Runner access is limited to the workspace path and tools configured above.</p>
      <Link className="text-link" href="/projects">Back to projects</Link>
    </div>
  );
}
