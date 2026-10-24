"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { PageHeader, StatusPill } from "./workspace-components";

type SkillResource = { path: string; content: string };
type Skill = {
  id: string;
  version: number;
  name: string;
  description: string;
  instructions: string;
  dependencies: string[];
  resources: SkillResource[];
  archived: boolean;
  created_at: string;
};
type SkillSummary = Omit<Skill, "instructions" | "resources">;
type SkillPayload = Pick<Skill, "name" | "description" | "instructions" | "dependencies" | "resources">;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("accept", "application/json");
  if (init?.body) headers.set("content-type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch { /* Keep the status-based error. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function SkillsView() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setSkills(null);
    setError(null);
    request<SkillSummary[]>(`/api/v1/skills?include_archived=${includeArchived}`)
      .then((items) => { if (active) setSkills(items); })
      .catch((reason: unknown) => { if (active) setError(errorMessage(reason, "Could not load skills.")); });
    return () => { active = false; };
  }, [includeArchived]);

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Agent library" title="Skills" description="Versioned text instructions and declared dependencies. Skills do not grant tools, permissions, or runtime compatibility.">
        <Link className="button button-primary" href="/skills/import">Import text skill</Link>
      </PageHeader>
      <div className="list-toolbar">
        <StatusPill>{skills === null ? "Loading" : `${skills.length} saved`}</StatusPill>
        <label className="filter-field"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> Include archived</label>
      </div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {skills === null && !error ? <main className="workspace-gate" role="status">Loading saved skills…</main> : null}
      {skills?.length === 0 ? <section className="content-section"><h2>{includeArchived ? "No skills found" : "Your skill library is empty"}</h2><p className="muted">Import a text skill to create its first immutable version. Nothing is installed or executed.</p></section> : null}
      {skills?.length ? <div className="preview-grid">
        {skills.map((skill) => (
          <article className="content-section preview-card" key={skill.id}>
            <div className="preview-card-heading">
              <div><p className="eyebrow">{skill.id.slice(0, 8)} · v{skill.version}</p><h2>{skill.name}</h2></div>
              <StatusPill tone={skill.archived ? "amber" : "neutral"}>{skill.archived ? "Archived" : "Saved"}</StatusPill>
            </div>
            <p>{skill.description || "No description."}</p>
            <dl className="preview-facts"><div><dt>Declared dependencies</dt><dd>{skill.dependencies.length ? skill.dependencies.join(", ") : "None declared"}</dd></div></dl>
            <div className="preview-actions"><Link className="text-link" href={`/skills/${skill.id}`}>Open skill</Link></div>
          </article>
        ))}
      </div> : null}
      <section className="content-section"><div className="section-heading"><h2>Import safety</h2><StatusPill>Text only</StatusPill></div><p className="muted">Scripts are not executed, packages are not installed, and external resources are not fetched. Declared dependencies are metadata only.</p></section>
    </div>
  );
}

function SkillEditor({
  initial,
  submitLabel,
  saving,
  readOnly = false,
  error,
  onSave,
}: {
  initial?: Skill;
  submitLabel: string;
  saving: boolean;
  readOnly?: boolean;
  error: string | null;
  onSave: (payload: SkillPayload) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [dependenciesText, setDependenciesText] = useState(initial?.dependencies.join("\n") ?? "");
  const [resources, setResources] = useState<SkillResource[]>(initial?.resources ?? []);
  const [fileError, setFileError] = useState<string | null>(null);

  async function addResources(files: FileList | null) {
    if (!files?.length) return;
    setFileError(null);
    try {
      const additions = await Promise.all(Array.from(files).map(async (file) => {
        if (file.size > 100_000 || !/\.(md|markdown|txt)$/i.test(file.name)) {
          throw new Error("Choose Markdown or text resources no larger than 100,000 bytes each.");
        }
        return { path: file.name, content: await file.text() };
      }));
      if (resources.length + additions.length > 10) throw new Error("A skill can contain at most 10 text resources.");
      setResources((current) => [...current, ...additions]);
    } catch (reason) {
      setFileError(errorMessage(reason, "Could not read this resource as text."));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSave({
      name,
      description,
      instructions,
      dependencies: dependenciesText.split("\n").map((item) => item.trim()).filter(Boolean),
      resources,
    });
  }

  return (
    <form className="content-section draft-form" onSubmit={(event) => void submit(event)}>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <label className="preview-field">Name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required disabled={readOnly} /></label>
      <label className="preview-field">Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} maxLength={500} disabled={readOnly} /></label>
      <label className="preview-field">Text instructions<textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={16} maxLength={1_048_576} spellCheck={false} required disabled={readOnly} /></label>
      {!readOnly ? <label className="preview-field">Add text resource files<input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" multiple onChange={(event) => { void addResources(event.target.files); event.currentTarget.value = ""; }} /></label> : null}
      {fileError ? <p className="inline-error" role="alert">{fileError}</p> : null}
      {resources.length ? <section className="connection-placeholder"><div className="section-heading"><h2>Text resources</h2><StatusPill>{resources.length} attached</StatusPill></div><ul>{resources.map((resource, index) => <li key={`${resource.path}:${index}`}>{resource.path}{!readOnly ? <button className="text-button" type="button" onClick={() => setResources((current) => current.filter((_, resourceIndex) => resourceIndex !== index))}>Remove</button> : null}</li>)}</ul></section> : null}
      <label className="preview-field">Declared dependencies<textarea value={dependenciesText} onChange={(event) => setDependenciesText(event.target.value)} rows={4} placeholder="One declaration per line; metadata only" disabled={readOnly} /></label>
      <p className="field-note">Declared dependencies are not installed or checked for compatibility. Resource paths are filenames only; content remains text data.</p>
      {!readOnly ? <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Saving…" : submitLabel}</button></div> : null}
    </form>
  );
}

export function SkillImportView() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(payload: SkillPayload) {
    setSaving(true);
    setError(null);
    try {
      const skill = await request<Skill>("/api/v1/skills/import", { method: "POST", body: JSON.stringify(payload) });
      router.push(`/skills/${skill.id}`);
    } catch (reason) {
      setError(errorMessage(reason, "Could not import this skill."));
    } finally {
      setSaving(false);
    }
  }

  return <div className="page-stack"><PageHeader eyebrow="Skill library" title="Import text skill" description="Create a saved, immutable first version. Import stores text only and never installs or executes code."><Link className="button button-secondary" href="/skills">Back to skills</Link></PageHeader><SkillEditor submitLabel="Import skill" saving={saving} error={error} onSave={save} /><section className="content-section"><div className="section-heading"><h2>Safety boundary</h2><StatusPill tone="amber">No execution</StatusPill></div><p>Scripts are not executed, dependencies are not installed, and network references are not fetched.</p></section></div>;
}

export function SkillDetailView({ skillId }: { skillId: string }) {
  const [skill, setSkill] = useState<Skill | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [latestVersion, setLatestVersion] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    setSkill(null);
    setError(null);
    const suffix = version === null ? "" : `?version=${version}`;
    request<Skill>(`/api/v1/skills/${skillId}${suffix}`)
      .then((data) => { if (active) { setSkill(data); if (version === null) setLatestVersion(data.version); } })
      .catch((reason: unknown) => { if (active) setError(errorMessage(reason, "Could not load this skill.")); });
    return () => { active = false; };
  }, [skillId, version]);

  async function save(payload: SkillPayload) {
    if (!skill) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await request<Skill>(`/api/v1/skills/${skill.id}`, { method: "PUT", body: JSON.stringify(payload) });
      setLatestVersion(updated.version);
      setVersion(null);
      setSkill(updated);
    } catch (reason) {
      setError(errorMessage(reason, "Could not save a new skill version."));
    } finally {
      setSaving(false);
    }
  }

  async function archive() {
    if (!skill) return;
    setSaving(true);
    setError(null);
    try {
      const archived = await request<Skill>(`/api/v1/skills/${skill.id}/archive`, { method: "POST", body: "{}" });
      setSkill(archived);
    } catch (reason) {
      setError(errorMessage(reason, "Could not archive this skill."));
    } finally {
      setSaving(false);
    }
  }

  if (error && !skill) return <div className="page-stack"><PageHeader eyebrow="Skill library" title="Skill unavailable" description={error} /><Link className="text-link" href="/skills">Back to skills</Link></div>;
  if (!skill) return <main className="workspace-gate" role="status">Loading saved skill…</main>;
  const readOnly = skill.archived || skill.version !== latestVersion;

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Skill library" title={skill.name} description="Versioned text data. Saving changed content creates a new immutable version; prior versions remain available.">
        <Link className="button button-secondary" href="/skills">Back to skills</Link>
        <a className="button button-secondary" href={`/api/v1/skills/${skill.id}/export?version=${skill.version}`}>Export version</a>
      </PageHeader>
      <div className="list-toolbar"><StatusPill tone={skill.archived ? "amber" : "neutral"}>{skill.archived ? "Archived" : "Saved skill"}</StatusPill><label className="filter-field">Version<select value={version ?? skill.version} onChange={(event) => setVersion(Number(event.target.value))}>{Array.from({ length: latestVersion ?? skill.version }, (_, index) => (latestVersion ?? skill.version) - index).map((item) => <option key={item} value={item}>Version {item}</option>)}</select></label></div>
      <SkillEditor key={`${skill.id}:${skill.version}`} initial={skill} submitLabel="Save new version" saving={saving} readOnly={readOnly} error={error} onSave={save} />
      {!readOnly ? <div className="form-actions"><button className="button button-secondary" type="button" disabled={saving} onClick={() => void archive()}>Archive skill</button></div> : null}
      <section className="content-section"><div className="section-heading"><h2>Execution and compatibility</h2><StatusPill tone="amber">Not validated</StatusPill></div><p>Dependencies are declarations only. This saved version is not a runtime capability and does not grant tools, files, network access, or credentials.</p></section>
    </div>
  );
}
