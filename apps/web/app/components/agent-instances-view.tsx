"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { PageHeader, StatusPill } from "./workspace-components";

type AgentInstance = {
  id: string;
  display_name: string;
  definition_id: string;
  definition_name: string;
  project_id: string | null;
  project_name: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
};

type Definition = {
  id: string;
  version: number;
  name: string;
  description: string;
  enabled: boolean;
  archived: boolean;
  created_at: string;
};

type Project = { id: string; name: string; summary: string; created_at: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("accept", "application/json");
  if (init?.body) headers.set("content-type", "application/json");
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    let message = "Request failed. Check the API and try again.";
    try {
      const body: { detail?: unknown } = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Keep the generic error when the API response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function AgentInstancesView() {
  const [instances, setInstances] = useState<AgentInstance[] | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [projectId, setProjectId] = useState("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setInstances(null);
    setError(null);
    request<AgentInstance[]>(`/api/v1/agent-instances?include_archived=${includeArchived}`)
      .then((result) => { if (active) setInstances(result); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load agent instances."); });
    return () => { active = false; };
  }, [includeArchived]);

  const projects = useMemo(() => {
    const values = new Map<string, string>();
    for (const instance of instances ?? []) {
      if (instance.project_id && instance.project_name) values.set(instance.project_id, instance.project_name);
    }
    return [...values.entries()].sort((left, right) => left[1].localeCompare(right[1]));
  }, [instances]);
  const visible = useMemo(() => (instances ?? []).filter((instance) => {
    const matchesQuery = `${instance.display_name} ${instance.definition_name} ${instance.project_name ?? ""}`
      .toLowerCase().includes(query.trim().toLowerCase());
    return matchesQuery && (projectId === "all" || instance.project_id === projectId);
  }), [instances, projectId, query]);

  return (
    <>
      <div className="list-toolbar">
        <label className="search-field"><span className="visually-hidden">Search agent instances</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, definition, or project…" /></label>
        <label className="filter-field"><span className="visually-hidden">Filter by project</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="all">All projects</option>{projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label className="filter-field"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> Include archived</label>
        {instances ? <span className="result-count">{visible.length} of {instances.length}</span> : null}
      </div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {!instances && !error ? <p className="muted" role="status">Loading saved agent instances…</p> : null}
      {instances && visible.length > 0 ? (
        <div className="agent-grid">
          {visible.map((instance) => (
            <article className="agent-card" key={instance.id}>
              <div className="agent-card-head">
                <span className="agent-avatar" aria-hidden="true">{instance.display_name.slice(0, 1).toUpperCase()}</span>
                <div className="agent-card-title"><h2><Link href={`/agents/${instance.id}`}>{instance.display_name}</Link></h2><p>{instance.definition_name}</p></div>
                <StatusPill tone={instance.archived ? "amber" : "green"}>{instance.archived ? "Archived" : "Registered"}</StatusPill>
              </div>
              <p className="agent-summary">Persistent instance identity. Runtime session and live work status are separate and are not connected here.</p>
              <dl className="agent-meta"><div><dt>Project</dt><dd>{instance.project_name ?? "Unassigned"}</dd></div><div><dt>Office location</dt><dd>Not configured</dd></div></dl>
              <div className="agent-card-footer"><Link className="text-link" href={`/agents/${instance.id}`}>Open instance</Link><span className="muted">API-backed</span></div>
            </article>
          ))}
        </div>
      ) : null}
      {instances && visible.length === 0 ? <div className="empty-state"><strong>{instances.length === 0 ? "No saved agent instances." : "No instances match."}</strong><span>{instances.length === 0 ? "Create an instance from an active definition; no runtime session is started." : "Try another name or project filter."}</span></div> : null}
    </>
  );
}

export function AgentInstanceEditor({ instanceId }: { instanceId?: string }) {
  const router = useRouter();
  const [instance, setInstance] = useState<AgentInstance | null>(null);
  const [definitions, setDefinitions] = useState<Definition[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [definitionId, setDefinitionId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);

  useEffect(() => {
    let active = true;
    setLoadError(null);
    setInstance(null);
    const instancePath = instanceId ? `/api/v1/agent-instances/${encodeURIComponent(instanceId)}` : null;
    void Promise.all([
      request<Definition[]>("/api/v1/agent-definitions?include_archived=true"),
      request<Project[]>("/api/v1/projects"),
      instancePath ? request<AgentInstance>(instancePath) : Promise.resolve(null),
    ]).then(([loadedDefinitions, loadedProjects, loadedInstance]) => {
      if (!active) return;
      setDefinitions(loadedDefinitions);
      setProjects(loadedProjects);
      setInstance(loadedInstance);
      if (loadedInstance) {
        setDisplayName(loadedInstance.display_name);
        setDefinitionId(loadedInstance.definition_id);
        setProjectId(loadedInstance.project_id ?? "");
      } else if (loadedDefinitions.length > 0) {
        setDefinitionId(loadedDefinitions.find((definition) => definition.enabled && !definition.archived)?.id ?? "");
      }
    }).catch((reason: unknown) => {
      if (active) setLoadError(reason instanceof Error ? reason.message : "Could not load instance form data.");
    });
    return () => { active = false; };
  }, [instanceId]);

  const selectedDefinition = definitions?.find((definition) => definition.id === definitionId);
  const canEdit = !instance?.archived;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !definitionId || !displayName.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await request<AgentInstance>(
        instanceId ? `/api/v1/agent-instances/${encodeURIComponent(instanceId)}` : "/api/v1/agent-instances",
        {
          method: instanceId ? "PUT" : "POST",
          body: JSON.stringify({
            display_name: displayName.trim(),
            definition_id: definitionId,
            project_id: projectId || null,
          }),
        },
      );
      setInstance(result);
      setDisplayName(result.display_name);
      setDefinitionId(result.definition_id);
      setProjectId(result.project_id ?? "");
      if (!instanceId) router.replace(`/agents/${result.id}`);
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Could not save the agent instance.");
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    if (!instanceId || archiving || instance?.archived) return;
    setArchiving(true);
    setSaveError(null);
    try {
      const result = await request<AgentInstance>(`/api/v1/agent-instances/${encodeURIComponent(instanceId)}/archive`, { method: "POST" });
      setInstance(result);
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : "Could not archive this instance.");
    } finally {
      setArchiving(false);
    }
  }

  if (loadError) {
    return <div className="page-stack"><PageHeader eyebrow="Agent instance" title="Instance unavailable" description={loadError} /><Link className="text-link" href="/agents">Back to agent list</Link></div>;
  }
  if (!definitions || !projects || (instanceId && !instance)) {
    return <main className="workspace-gate" role="status">Loading agent instance…</main>;
  }

  const activeDefinitions = definitions.filter((definition) => definition.enabled && !definition.archived);
  const hasActiveDefinition = activeDefinitions.length > 0;
  const heading = instance ? instance.display_name : "New agent instance";
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Agent instance" title={heading} description="A persistent identity links an active Agent Definition and optionally a Project. It does not create a Runtime Session or start work.">
        <Link className="button button-secondary" href="/agents">Back to agent list</Link>
        {instance ? <StatusPill tone={instance.archived ? "amber" : "green"}>{instance.archived ? "Archived" : "Registered"}</StatusPill> : null}
      </PageHeader>
      <form className="content-section draft-form" onSubmit={(event) => void submit(event)}>
        <div className="section-heading"><h2>{instance ? "Instance configuration" : "Register identity"}</h2><StatusPill>Owner scoped · persisted</StatusPill></div>
        <label className="preview-field">Display name<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={1} maxLength={120} autoComplete="off" required disabled={!canEdit} /></label>
        <label className="preview-field">Agent definition<select value={definitionId} onChange={(event) => setDefinitionId(event.target.value)} required disabled={!canEdit || (!instance && !hasActiveDefinition)}><option value="" disabled>Select an enabled definition</option>{definitions.map((definition) => <option key={definition.id} value={definition.id} disabled={definition.archived || !definition.enabled}>{definition.name}{definition.archived ? " · Archived" : !definition.enabled ? " · Disabled" : ` · v${definition.version}`}</option>)}</select></label>
        <label className="preview-field">Project placement <span className="field-note">This associates the instance with project context only; it does not grant project permissions or place an avatar at a physical desk.</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)} disabled={!canEdit}><option value="">No project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        {selectedDefinition ? <div className="connection-placeholder"><strong>{selectedDefinition.name} · version {selectedDefinition.version}</strong><span>{selectedDefinition.description || "No description provided."}</span></div> : null}
        {instance ? <dl className="preview-facts"><div><dt>Instance ID</dt><dd>{instance.id}</dd></div><div><dt>Office location</dt><dd>Not configured</dd></div><div><dt>Runtime session</dt><dd>Not attached</dd></div><div><dt>Created</dt><dd>{new Date(instance.created_at).toLocaleString()}</dd></div></dl> : null}
        {!instance && !hasActiveDefinition ? <p className="inline-error" role="status">Create and enable a definition before registering an instance. <Link href="/agents/definitions/new">Create definition</Link></p> : null}
        {saveError ? <p className="inline-error" role="alert">{saveError}</p> : null}
        <div className="form-actions">
          {canEdit ? <button className="button button-primary" type="submit" disabled={saving || (!instance && !hasActiveDefinition) || !displayName.trim() || !definitionId}>{saving ? "Saving…" : instance ? "Save changes" : "Register instance"}</button> : null}
          {instance && canEdit ? <button className="button button-secondary" type="button" onClick={() => void archive()} disabled={archiving}>{archiving ? "Archiving…" : "Archive instance"}</button> : null}
          <span className="muted">No runtime connection, activity, or task state is inferred from registration.</span>
        </div>
      </form>
      <section className="content-section">
        <div className="section-heading"><h2>Definition and project links</h2><StatusPill>Configuration only</StatusPill></div>
        <dl className="preview-facts"><div><dt>Definition</dt><dd>{instance?.definition_name ?? selectedDefinition?.name ?? "Not selected"}</dd></div><div><dt>Project</dt><dd>{instance?.project_name ?? (projectId ? projects.find((project) => project.id === projectId)?.name : null) ?? "Unassigned"}</dd></div></dl>
        <p className="muted">Changing project association does not change project access. Runtime sessions and office scene locations are separate Phase 3/5 data.</p>
      </section>
    </div>
  );
}
