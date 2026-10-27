"use client";

import { useEffect, useState, type FormEvent } from "react";
import { StatusPill } from "./workspace-components";

const WORKSPACE_TOOLS = [
  ["list_files", "List files"],
  ["read_file", "Read files"],
  ["write_file", "Write files"],
] as const;

type WorkspacePolicy = {
  project_id: string;
  workspace_path: string;
  allowed_tools: string[];
  max_duration_seconds: number;
  max_inference_turns: number;
  max_tool_calls: number;
  enabled: boolean;
  revision: number;
};

type WorkspacePolicyDraft = Omit<WorkspacePolicy, "project_id" | "revision">;

async function requestPolicy(projectId: string, init?: RequestInit): Promise<WorkspacePolicy | null> {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/execution-policy`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : "Could not save project execution policy.";
    throw new Error(detail);
  }
  return response.json() as Promise<WorkspacePolicy | null>;
}

const defaults: WorkspacePolicyDraft = {
  workspace_path: "",
  allowed_tools: ["list_files", "read_file"],
  max_duration_seconds: 600,
  max_inference_turns: 8,
  max_tool_calls: 20,
  enabled: true,
};

export function ProjectExecutionPolicy({ projectId }: { projectId: string }) {
  const [draft, setDraft] = useState<WorkspacePolicyDraft>(defaults);
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void requestPolicy(projectId, { signal: controller.signal })
      .then((policy) => {
        if (controller.signal.aborted) return;
        setDraft(policy ? {
          workspace_path: policy.workspace_path,
          allowed_tools: policy.allowed_tools,
          max_duration_seconds: policy.max_duration_seconds,
          max_inference_turns: policy.max_inference_turns,
          max_tool_calls: policy.max_tool_calls,
          enabled: policy.enabled,
        } : defaults);
        setRevision(policy?.revision ?? null);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load execution policy.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId]);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || draft.allowed_tools.length === 0) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const policy = await requestPolicy(projectId, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!policy) throw new Error("Policy save returned no record.");
      setRevision(policy.revision);
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save execution policy.");
    } finally {
      setSaving(false);
    }
  }

  function update<K extends keyof WorkspacePolicyDraft>(key: K, value: WorkspacePolicyDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setSaved(false);
  }

  return (
    <section className="content-section" aria-labelledby="project-execution-policy-heading">
      <div className="section-heading">
        <h2 id="project-execution-policy-heading">Workspace execution policy</h2>
        <StatusPill tone={revision === null ? "neutral" : "green"}>{revision === null ? "Not configured" : `Revision ${revision}`}</StatusPill>
      </div>
      <p className="field-note">Workspace paths are relative to the host-mounted <code>./workspaces</code> directory. The Runner creates the selected directory if absent. No shell or command execution is available.</p>
      {loading ? <p role="status">Loading workspace policy…</p> : null}
      {!loading ? (
        <form className="draft-form" onSubmit={savePolicy}>
          <label className="preview-field">Workspace folder<input required maxLength={512} value={draft.workspace_path} onChange={(event) => update("workspace_path", event.currentTarget.value)} placeholder="project-one" autoComplete="off" /></label>
          <fieldset className="check-row">
            <legend>Allowed workspace tools</legend>
            {WORKSPACE_TOOLS.map(([tool, label]) => (
              <label key={tool}>
                <input
                  type="checkbox"
                  checked={draft.allowed_tools.includes(tool)}
                  onChange={(event) => update(
                    "allowed_tools",
                    event.currentTarget.checked
                      ? [...draft.allowed_tools, tool]
                      : draft.allowed_tools.filter((item) => item !== tool),
                  )}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <div className="preview-grid">
            <label className="preview-field">Max duration (seconds)<input type="number" min={1} max={3600} required value={draft.max_duration_seconds} onChange={(event) => update("max_duration_seconds", Number(event.currentTarget.value))} /></label>
            <label className="preview-field">Max inference turns<input type="number" min={1} max={12} required value={draft.max_inference_turns} onChange={(event) => update("max_inference_turns", Number(event.currentTarget.value))} /></label>
            <label className="preview-field">Max tool calls<input type="number" min={1} max={50} required value={draft.max_tool_calls} onChange={(event) => update("max_tool_calls", Number(event.currentTarget.value))} /></label>
          </div>
          <label className="check-row"><input type="checkbox" checked={draft.enabled} onChange={(event) => update("enabled", event.currentTarget.checked)} />Enable policy for new Runs</label>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {saved ? <p role="status">Execution policy saved. New Runs use revision {revision}.</p> : null}
          <div className="form-actions"><button className="button" type="submit" disabled={saving || loading || draft.allowed_tools.length === 0}>{saving ? "Saving…" : "Save execution policy"}</button></div>
        </form>
      ) : null}
    </section>
  );
}
