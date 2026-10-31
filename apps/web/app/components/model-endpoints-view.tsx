"use client";

import { useEffect, useState, type FormEvent } from "react";
import { StatusPill } from "./workspace-components";

type ModelEndpoint = {
  id: string;
  name: string;
  adapter: string;
  endpoint_url: string;
  credential_env: string;
  default_model_id: string;
  default_route: string | null;
  archived: boolean;
  created_at: string;
};

type ModelEndpointDraft = Pick<
  ModelEndpoint,
  "name" | "endpoint_url" | "credential_env" | "default_model_id" | "default_route"
>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    throw new Error("Your owner session expired. Sign in again to manage Model Endpoints.");
  }
  if (response.status === 422) {
    throw new Error("The Model Endpoint name, URL, credential reference, or default model is invalid.");
  }
  if (!response.ok) {
    throw new Error("Model Endpoints could not be loaded or saved. Check the API and database migration, then retry.");
  }
  return response.json() as Promise<T>;
}

export function ModelEndpointsView() {
  const [endpoints, setEndpoints] = useState<ModelEndpoint[] | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [provider, setProvider] = useState("ninerouter");
  const isCodex = provider === "codex";
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setEndpoints(null);
    setError(null);
    void request<ModelEndpoint[]>(
      `/api/v1/model-endpoints?include_archived=${includeArchived}&limit=200&offset=0`,
      { signal: controller.signal },
    )
      .then((data) => {
        if (!controller.signal.aborted) setEndpoints(data);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Model Endpoints are unavailable.");
        }
      });
    return () => controller.abort();
  }, [includeArchived, loadAttempt]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const formElement = event.currentTarget;
    const form = new FormData(event.currentTarget);
    const draft: ModelEndpointDraft = {
      name: String(form.get("name")),
      endpoint_url: isCodex ? "http://localhost:14000/v1" : String(form.get("endpoint_url")),
      credential_env: isCodex ? "OMP_GATEWAY_KEY" : "NINEROUTER_KEY",
      default_model_id: String(form.get("default_model_id")),
      default_route: isCodex ? null : String(form.get("default_route") || "") || null,
    };
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await request<ModelEndpoint>("/api/v1/model-endpoints", {
        method: "POST",
        body: JSON.stringify(draft),
      });
      formElement.reset();
      setMessage("Model Endpoint saved. This does not verify provider availability or run an agent.");
      setLoadAttempt((attempt) => attempt + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model Endpoint could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function archive(endpoint: ModelEndpoint) {
    if (saving || !window.confirm(`Archive “${endpoint.name}”? Existing Run snapshots remain unchanged.`)) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await request<ModelEndpoint>(`/api/v1/model-endpoints/${endpoint.id}/archive`, {
        method: "POST",
        body: "{}",
      });
      setMessage("Model Endpoint archived. It cannot be selected for new Agent Definition versions.");
      setLoadAttempt((attempt) => attempt + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model Endpoint could not be archived.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="content-section settings-card" aria-labelledby="model-endpoints-title">
      <div className="settings-card-heading">
        <div>
          <p className="eyebrow">Inference · OpenAI-compatible Chat Completions</p>
          <h2 id="model-endpoints-title">Model Endpoints</h2>
        </div>
        <StatusPill tone={endpoints?.some((endpoint) => !endpoint.archived) ? "green" : "amber"}>
          {endpoints?.some((endpoint) => !endpoint.archived) ? "Configured" : error ? "Unavailable" : "Not configured"}
        </StatusPill>
      </div>
      <p className="field-note">Endpoints are owner-scoped, reusable, and archived rather than deleted. Saved configuration is not proof of provider availability or runtime execution.</p>
      <form className="draft-form" onSubmit={(event) => void create(event)}>
        <label className="preview-field">Provider
          <select value={provider} onChange={(event) => setProvider(event.target.value)} disabled={saving}>
            <option value="ninerouter">9Router</option>
            <option value="codex">Codex Subscription · OMP gateway</option>
          </select>
          <span className="field-note">Runtime: LangChain. Each Run uses only its selected provider.</span>
        </label>
        <label className="preview-field">Endpoint name<input name="name" maxLength={120} required disabled={saving} /></label>
        <label className="preview-field">Endpoint URL<input key={provider} name="endpoint_url" defaultValue={isCodex ? "http://localhost:14000/v1" : ""} readOnly={isCodex} type="url" maxLength={2048} placeholder="http://localhost:20128/v1" required disabled={saving} /></label>
        <label className="preview-field">Default model ID<input name="default_model_id" maxLength={200} placeholder={isCodex ? "openai-codex/<model-id>" : "openrouter/provider/model:free"} required disabled={saving} /></label>
        <label className="preview-field">Default route <span className="field-note">Optional provider routing identifier.</span><input name="default_route" maxLength={120} disabled={saving || isCodex} /></label>
        <label className="preview-field">Credential environment reference
          <select name="credential_env" value={isCodex ? "OMP_GATEWAY_KEY" : "NINEROUTER_KEY"} disabled>
            <option value="OMP_GATEWAY_KEY">OMP_GATEWAY_KEY</option>
            <option value="NINEROUTER_KEY">NINEROUTER_KEY</option>
          </select>
          <span className="field-note">This is an environment variable name, not a secret value. The API and browser never receive the credential value.</span>
        </label>
        <div className="form-actions"><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save Model Endpoint"}</button></div>
      </form>
      {error ? <p className="inline-error" role="alert">{error} <button className="text-button" type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>Retry</button></p> : null}
      {message ? <p role="status" className="field-note">{message}</p> : null}
      <div className="list-toolbar">
        <h3>Saved endpoints</h3>
        <label className="filter-field"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> Include archived</label>
      </div>
      {endpoints === null && !error ? <p role="status">Loading Model Endpoints…</p> : null}
      {endpoints?.length === 0 ? <p className="muted">No Model Endpoints are saved for this owner.</p> : null}
      {endpoints?.length ? <ul className="resource-list">
        {endpoints.map((endpoint) => (
          <li className="resource-list-item" key={endpoint.id}>
            <div>
              <strong>{endpoint.name}</strong>
              <p className="muted">{endpoint.credential_env === "OMP_GATEWAY_KEY" ? "Codex Subscription · OMP gateway" : "9Router"} · {endpoint.adapter} · {endpoint.endpoint_url}</p>
              <p className="muted">Default model: {endpoint.default_model_id}{endpoint.default_route ? ` · route ${endpoint.default_route}` : ""} · credential reference: {endpoint.credential_env}</p>
            </div>
            <div className="list-item-actions">
              <StatusPill tone={endpoint.archived ? "amber" : "green"}>{endpoint.archived ? "Archived" : "Available"}</StatusPill>
              {!endpoint.archived ? <button className="button button-secondary" type="button" disabled={saving} onClick={() => void archive(endpoint)}>Archive</button> : null}
            </div>
          </li>
        ))}
      </ul> : null}
    </section>
  );
}
