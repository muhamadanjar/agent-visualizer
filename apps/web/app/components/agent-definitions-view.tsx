"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { PageHeader, StatusPill } from "./workspace-components";

type AgentConfiguration = {
  role: string;
  model_endpoint_id: string;
  model_id: string;
  route: string | null;
  runtime_id: "langchain";
  temperature: number | null;
  max_iterations: number;
  concurrency_limit: number;
  execution_timeout_seconds: number;
  allowed_tools: string[];
};
type ModelEndpointOption = {
  id: string;
  name: string;
  endpoint_url: string;
  default_model_id: string;
  default_route: string | null;
  archived: boolean;
};
type Definition = {
  id: string;
  version: number;
  name: string;
  description: string;
  source_markdown: string;
  unmapped_metadata: Record<string, unknown>;
  configuration: AgentConfiguration | null;
  enabled: boolean;
  archived: boolean;
  created_at: string;
  skills: DefinitionSkillReference[];
};

type DefinitionSkillReference = { skill_id: string; version: number; name: string };
type SkillSummary = { id: string; version: number; name: string; archived: boolean };
type DefinitionSummary = {
  id: string;
  version: number;
  name: string;
  description: string;
  configured: boolean;
  enabled: boolean;
  archived: boolean;
  created_at: string;
};

const registeredTools = ["list_files", "read_file", "write_file"];

function configurationFromForm(form: FormData): AgentConfiguration {
  const temperature = String(form.get("temperature") ?? "");
  return {
    role: String(form.get("role") ?? "").trim(),
    model_endpoint_id: String(form.get("model_endpoint_id") ?? ""),
    model_id: String(form.get("model_id") ?? "").trim(),
    route: String(form.get("route") ?? "").trim() || null,
    runtime_id: "langchain",
    temperature: temperature === "" ? null : Number(temperature),
    max_iterations: Number(form.get("max_iterations")),
    concurrency_limit: Number(form.get("concurrency_limit")),
    execution_timeout_seconds: Number(form.get("execution_timeout_seconds")),
    allowed_tools: form.getAll("allowed_tools").map(String),
  };
}

function ModelConfigurationFields({
  endpoints,
  configuration,
  disabled = false,
}: {
  endpoints: ModelEndpointOption[];
  configuration?: AgentConfiguration | null;
  disabled?: boolean;
}) {
  const available = endpoints.filter((endpoint) => !endpoint.archived);
  const configuredEndpoint = endpoints.find((endpoint) => endpoint.id === configuration?.model_endpoint_id);
  const initialEndpointId = configuredEndpoint && !configuredEndpoint.archived
    ? configuredEndpoint.id
    : available[0]?.id ?? configuredEndpoint?.id ?? "";
  const preserveConfiguration = initialEndpointId === configuration?.model_endpoint_id;
  const [endpointId, setEndpointId] = useState(initialEndpointId);
  const selectedEndpoint = endpoints.find((endpoint) => endpoint.id === endpointId);
  const [modelId, setModelId] = useState(
    preserveConfiguration ? configuration?.model_id ?? "" : selectedEndpoint?.default_model_id ?? "",
  );
  const [route, setRoute] = useState(
    preserveConfiguration
      ? configuration?.route ?? selectedEndpoint?.default_route ?? ""
      : selectedEndpoint?.default_route ?? "",
  );
  const currentArchived = endpoints.find(
    (endpoint) => endpoint.id === configuration?.model_endpoint_id && endpoint.archived,
  );
  return (
    <section className="content-section draft-form">
      <div className="section-heading"><h2>Agent configuration</h2><StatusPill>Immutable per version</StatusPill></div>
      <label className="preview-field">Role<input name="role" defaultValue={configuration?.role ?? ""} maxLength={120} required disabled={disabled} /></label>
      <label className="preview-field">Model Endpoint
        <select name="model_endpoint_id" value={endpointId} required disabled={disabled} onChange={(event) => {
          const nextId = event.currentTarget.value;
          const nextEndpoint = available.find((endpoint) => endpoint.id === nextId);
          setEndpointId(nextId);
          if (nextEndpoint) {
            setModelId(nextEndpoint.default_model_id);
            setRoute(nextEndpoint.default_route ?? "");
          }
        }}>
          {currentArchived ? <option value={currentArchived.id} disabled={currentArchived.id !== endpointId}>{currentArchived.name} · archived</option> : null}
          {available.map((endpoint) => <option key={endpoint.id} value={endpoint.id}>{endpoint.name} · {endpoint.default_model_id}</option>)}
        </select>
      </label>
      {available.length === 0 && !currentArchived ? <p className="field-note">Create a Model Endpoint before saving this Agent Definition.</p> : null}
      <label className="preview-field">Model ID<input name="model_id" value={modelId} onChange={(event) => setModelId(event.target.value)} maxLength={200} required disabled={disabled} /></label>
      <label className="preview-field">Route (optional)<input name="route" value={route} onChange={(event) => setRoute(event.target.value)} maxLength={120} disabled={disabled} /></label>
      <label className="preview-field">Temperature (optional)<input name="temperature" type="number" min="0" max="2" step="0.1" defaultValue={configuration?.temperature ?? ""} disabled={disabled} /></label>
      <label className="preview-field">Maximum iterations<input name="max_iterations" type="number" min="1" max="100" defaultValue={configuration?.max_iterations ?? 10} required disabled={disabled} /></label>
      <label className="preview-field">Concurrency limit<input name="concurrency_limit" type="number" min="1" max="20" defaultValue={configuration?.concurrency_limit ?? 1} required disabled={disabled} /></label>
      <label className="preview-field">Execution timeout (seconds)<input name="execution_timeout_seconds" type="number" min="1" max="86400" defaultValue={configuration?.execution_timeout_seconds ?? 3600} required disabled={disabled} /></label>
      <fieldset className="preview-field" disabled={disabled}>
        <legend>Allowed tools</legend>
        {registeredTools.map((tool) => <label className="filter-field" key={tool}><input name="allowed_tools" type="checkbox" value={tool} defaultChecked={configuration?.allowed_tools.includes(tool) ?? false} /> {tool}</label>)}
        <span className="field-note">Effective tools are intersected with project policy.</span>
      </fieldset>
    </section>
  );
}

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

export function AgentDefinitionsView() {
  const [definitions, setDefinitions] = useState<DefinitionSummary[] | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setDefinitions(null);
    setError(null);
    request<DefinitionSummary[]>(`/api/v1/agent-definitions?include_archived=${includeArchived}`)
      .then((data) => { if (active) setDefinitions(data); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load definitions."); });
    return () => { active = false; };
  }, [includeArchived]);

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Agent library" title="Definitions" description="Versioned owner-owned role instructions, separate from agent instances and runtime sessions.">
        <Link className="button button-secondary" href="/agents/definitions/import">Import Markdown</Link>
        <Link className="button button-primary" href="/agents/definitions/new">New definition</Link>
      </PageHeader>
      <div className="list-toolbar">
        <label className="filter-field"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> Include archived</label>
        {definitions ? <span className="result-count">{definitions.length} definitions</span> : null}
      </div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {!definitions && !error ? <p className="workspace-gate" role="status">Loading saved definitions…</p> : null}
      {definitions?.length ? <div className="definition-list">{definitions.map((definition) => (
        <article className="content-section definition-card" key={definition.id}>
          <div className="preview-card-heading">
            <div><p className="eyebrow">Version {definition.version}</p><h2><Link href={`/agents/definitions/${definition.id}`}>{definition.name}</Link></h2></div>
            <StatusPill tone={definition.archived ? "amber" : definition.enabled ? "green" : "neutral"}>{definition.archived ? "Archived" : definition.enabled ? "Enabled" : "Disabled"}</StatusPill>
            <StatusPill tone={definition.configured ? "green" : "amber"}>{definition.configured ? "Configured" : "Needs configuration"}</StatusPill>
          </div>
          <p>{definition.description || "No description."}</p>
          <div className="preview-actions"><Link className="text-link" href={`/agents/definitions/${definition.id}`}>Open definition</Link></div>
        </article>
      ))}</div> : null}
      {definitions?.length === 0 ? <div className="empty-state"><strong>No saved definitions.</strong><span>Import Markdown or create a definition. No sample records are shown here.</span></div> : null}
    </div>
  );
}

export function AgentDefinitionForm({ mode }: { mode: "import" | "new" }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState("");
  const [endpoints, setEndpoints] = useState<ModelEndpointOption[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    request<ModelEndpointOption[]>("/api/v1/model-endpoints?include_archived=false&limit=200&offset=0")
      .then((data) => { if (active) setEndpoints(data); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load Model Endpoints."); });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !endpoints?.some((endpoint) => !endpoint.archived)) return;
    const form = new FormData(event.currentTarget);
    setSaving(true);
    setError(null);
    try {
      const result = await request<Definition>("/api/v1/agent-definitions/import", {
        method: "POST",
        body: JSON.stringify({
          source_markdown: source,
          configuration: configurationFromForm(form),
          ...(name.trim() ? { name: name.trim() } : {}),
          ...(description.trim() ? { description: description.trim() } : {}),
        }),
      });
      router.push(`/agents/definitions/${result.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the definition.");
    } finally {
      setSaving(false);
    }
  }


  async function readFile(file?: File) {
    setError(null);
    if (!file) return;
    if (!/\.(md|markdown|txt)$/i.test(file.name) || file.size > 1_048_576) {
      setError("Choose a Markdown/text file no larger than 1 MiB.");
      return;
    }
    try {
      setSource(new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()));
    } catch {
      setError("The file is not valid UTF-8 text.");
    }
  }

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Agent library" title={mode === "import" ? "Import definition" : "New definition"} description="Markdown is parsed as data, preserved byte-for-byte as UTF-8 text, and never executed.">
        <Link className="button button-secondary" href="/agents/definitions">Back to definitions</Link>
      </PageHeader>
      <form className="content-section draft-form" onSubmit={(event) => void submit(event)}>
        <div className="section-heading"><h2>Definition source</h2><StatusPill>Owner scoped · versioned</StatusPill></div>
        <label className="preview-field">Markdown or text file<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" onChange={(event) => void readFile(event.target.files?.[0])} /></label>
        <label className="preview-field">Name override <span className="field-note">Required when Markdown has no `name` front matter.</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} /></label>
        <label className="preview-field">Description override<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} maxLength={500} /></label>
        <label className="preview-field">Original Markdown<textarea value={source} onChange={(event) => setSource(event.target.value)} rows={18} spellCheck={false} required /></label>
        {endpoints ? <ModelConfigurationFields endpoints={endpoints} /> : <p className="workspace-gate" role="status">Loading Model Endpoints…</p>}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving || !source || !endpoints?.some((endpoint) => !endpoint.archived)}>{saving ? "Saving…" : "Save version 1"}</button><span className="muted">Unknown front-matter fields are retained as metadata; scripts are not run.</span></div>
      </form>
    </div>
  );
}

export function AgentDefinitionDetailView({ definitionId }: { definitionId: string }) {
  const [definition, setDefinition] = useState<Definition | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [latestVersion, setLatestVersion] = useState<number | null>(null);
  const [endpoints, setEndpoints] = useState<ModelEndpointOption[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [selectedPins, setSelectedPins] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    setDefinition(null);
    setError(null);
    const suffix = version === null ? "" : `?version=${version}`;
    request<Definition>(`/api/v1/agent-definitions/${definitionId}${suffix}`)
      .then((data) => {
        if (active) {
          setDefinition(data);
          setSelectedPins(data.skills.map((item) => `${item.skill_id}:${item.version}`));
          if (version === null) setLatestVersion(data.version);
        }
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load this definition."); });
    return () => { active = false; };
  }, [definitionId, version]);
  useEffect(() => {
    let active = true;
    request<ModelEndpointOption[]>("/api/v1/model-endpoints?include_archived=true&limit=200&offset=0")
      .then((data) => { if (active) setEndpoints(data); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load Model Endpoints."); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    request<SkillSummary[]>("/api/v1/skills")
      .then((data) => { if (active) setSkills(data); })
      .catch((reason: unknown) => { if (active) setSkillsError(reason instanceof Error ? reason.message : "Could not load skills."); });
    return () => { active = false; };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!definition) return;
    const form = new FormData(event.currentTarget);
    const changes: Record<string, unknown> = {
      name: String(form.get("name")),
      description: String(form.get("description")),
      source_markdown: String(form.get("source_markdown")),
    };
    if (form.get("model_endpoint_id")) changes.configuration = configurationFromForm(form);
    setSaving(true);
    setError(null);
    try {
      const updated = await request<Definition>(`/api/v1/agent-definitions/${definition.id}`, {
        method: "PUT",
        body: JSON.stringify(changes),
      });
      setLatestVersion(updated.version);
      setDefinition(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save this definition.");
    } finally {
      setSaving(false);
    }
  }

  async function saveSkills() {
    if (!definition) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await request<Definition>(`/api/v1/agent-definitions/${definition.id}/skills`, {
        method: "PUT",
        body: JSON.stringify({
          skills: selectedPins.map((item) => {
            const [skillId, selectedVersion] = item.split(":");
            return { skill_id: skillId, version: Number(selectedVersion) };
          }),
        }),
      });
      setDefinition(updated);
      setLatestVersion(updated.version);
      setVersion(null);
      setSelectedPins(updated.skills.map((item) => `${item.skill_id}:${item.version}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update pinned skills.");
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    if (!definition) return;
    setSaving(true);
    setError(null);
    try {
      const archived = await request<Definition>(`/api/v1/agent-definitions/${definition.id}/archive`, { method: "POST", body: "{}" });
      setDefinition(archived);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not archive this definition.");
    } finally {
      setSaving(false);
    }
  }
  async function setEnabled(enabled: boolean) {
    if (!definition || definition.archived || definition.version !== latestVersion || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await request<Definition>(`/api/v1/agent-definitions/${definition.id}/enabled`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      });
      setDefinition(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not change the definition status.");
    } finally {
      setSaving(false);
    }
  }

  if (error && !definition) return <div className="page-stack"><PageHeader eyebrow="Agent definition" title="Definition unavailable" description={error} /><Link className="text-link" href="/agents/definitions">Back to definitions</Link></div>;
  if (!definition) return <main className="workspace-gate" role="status">Loading saved definition…</main>;
  const readOnly = definition.archived || definition.version !== latestVersion;
  const activeEndpoints = endpoints?.filter((endpoint) => !endpoint.archived) ?? [];
  const canEnable = Boolean(
    definition.configuration
      && activeEndpoints.some((endpoint) => endpoint.id === definition.configuration?.model_endpoint_id),
  );
  const skillOptions = new Map<string, string>();
  for (const skill of skills ?? []) {
    for (let skillVersion = 1; skillVersion <= skill.version; skillVersion += 1) {
      skillOptions.set(`${skill.id}:${skillVersion}`, `${skill.name} · v${skillVersion}`);
    }
  }
  for (const pin of definition.skills) {
    const key = `${pin.skill_id}:${pin.version}`;
    if (!skillOptions.has(key)) skillOptions.set(key, `${pin.name} · v${pin.version} (currently pinned; unavailable)`);
  }

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Agent definition" title={definition.name} description="Edits create immutable versions. Existing task/run snapshots are not rewritten.">
        <Link className="button button-secondary" href="/agents/definitions">Back to definitions</Link>
        <a className="button button-secondary" href={`/api/v1/agent-definitions/${definition.id}/export?version=${definition.version}`}>Export source</a>
        {!definition.archived && definition.version === latestVersion ? <button className="button button-secondary" type="button" disabled={saving || (!definition.enabled && !canEnable)} onClick={() => void setEnabled(!definition.enabled)}>{definition.enabled ? "Disable" : "Enable"}</button> : null}
      </PageHeader>
      <div className="list-toolbar"><StatusPill tone={definition.archived ? "amber" : definition.enabled ? "green" : "neutral"}>{definition.archived ? "Archived" : definition.enabled ? "Enabled" : "Disabled"}</StatusPill><StatusPill tone={definition.configuration ? "green" : "amber"}>{definition.configuration ? "Configured" : "Needs configuration"}</StatusPill><label className="filter-field">Version<select value={version ?? definition.version} onChange={(event) => setVersion(Number(event.target.value))}>{Array.from({ length: latestVersion ?? definition.version }, (_, index) => (latestVersion ?? definition.version) - index).map((item) => <option key={item} value={item}>Version {item}</option>)}</select></label></div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <form className="content-section draft-form" key={`${definition.id}:${definition.version}`} onSubmit={(event) => void save(event)}>
        <label className="preview-field">Name<input name="name" defaultValue={definition.name} maxLength={120} required disabled={readOnly} /></label>
        <label className="preview-field">Description<textarea name="description" defaultValue={definition.description} rows={3} maxLength={500} disabled={readOnly} /></label>
        <label className="preview-field">Original Markdown<textarea name="source_markdown" defaultValue={definition.source_markdown} rows={18} spellCheck={false} required disabled={readOnly} /></label>
        {endpoints ? (activeEndpoints.length > 0 || definition.configuration
          ? <ModelConfigurationFields endpoints={endpoints} configuration={definition.configuration} disabled={readOnly || (activeEndpoints.length === 0 && Boolean(definition.configuration))} />
          : <p className="field-note">No active Model Endpoint exists. Save this definition unconfigured or create a Model Endpoint to configure it.</p>)
          : <p className="workspace-gate" role="status">Loading Model Endpoints…</p>}
        {!readOnly ? <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving || !endpoints}>{saving ? "Saving…" : "Save new version"}<span className="visually-hidden"> Current version {definition.version}; the next saved version is immutable.</span></button><button className="button button-secondary" type="button" disabled={saving} onClick={() => void archive()}>Archive</button></div> : null}
      </form>
      {Object.keys(definition.unmapped_metadata).length ? <section className="content-section"><h2>Preserved unmapped metadata</h2><pre>{JSON.stringify(definition.unmapped_metadata, null, 2)}</pre></section> : null}
      <section className="content-section draft-form">
        <div className="section-heading"><h2>Pinned skill versions</h2><StatusPill>{definition.skills.length} pinned</StatusPill></div>
        <p className="field-note">Pins reference exact saved versions. A changed pin list creates a new immutable definition version; skill updates do not rewrite existing pins.</p>
        {skillsError ? <p className="inline-error" role="alert">{skillsError}</p> : null}
        {skills === null && !skillsError ? <p role="status">Loading available skills…</p> : null}
        {skills?.length === 0 ? <p className="muted">No saved skills yet. <Link className="text-link" href="/skills/import">Import a text skill</Link>.</p> : null}
        {skills !== null ? <>
          <label className="preview-field">Select skill versions
            <select multiple size={Math.min(8, Math.max(4, skillOptions.size))} value={selectedPins} disabled={readOnly || saving || Boolean(skillsError)} onChange={(event) => {
              const selected = Array.from(event.currentTarget.selectedOptions, (option) => option.value);
              setSelectedPins((current) => [
                ...current.filter((value) => selected.includes(value)),
                ...selected.filter((value) => !current.includes(value)),
              ]);
            }}>
              {Array.from(skillOptions, ([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          {!readOnly ? <div className="form-actions"><button className="button button-primary" type="button" disabled={saving || Boolean(skillsError)} onClick={() => void saveSkills()}>{saving ? "Saving…" : "Save pinned versions"}</button></div> : null}
        </> : null}
      </section>
    </div>
  );
}
