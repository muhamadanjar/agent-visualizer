"use client";

import { useEffect, useState, type FormEvent } from "react";
import { StatusPill } from "./workspace-components";

type ProviderConfig = {
  version: number;
  adapter: string;
  endpoint_url: string;
  model_id: string;
  route: string | null;
  updated_at: string;
};

type LoadState = "loading" | "ready" | "error";

async function requestConfig(
  method: "GET" | "PUT",
  body?: { endpoint_url: string; model_id: string; route?: string },
  signal?: AbortSignal,
): Promise<ProviderConfig | null> {
  const response = await fetch("/api/v1/provider-config", {
    method,
    credentials: "same-origin",
    cache: "no-store",
    signal,
    headers: {
      accept: "application/json",
      ...(body ? { "content-type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (method === "GET" && response.status === 404) return null;
  if (response.status === 401) throw new Error("Your owner session expired. Sign in again to manage provider settings.");
  if (response.status === 422) throw new Error("Endpoint URL, model ID, or route is invalid. Local HTTP is limited to localhost:20128; other endpoints must use HTTPS.");
  if (!response.ok) throw new Error("Provider settings could not be loaded or saved. Check the API and database migration, then retry.");

  const value: unknown = await response.json();
  if (
    typeof value !== "object" || value === null ||
    !("version" in value) || typeof value.version !== "number" ||
    !("adapter" in value) || typeof value.adapter !== "string" ||
    !("endpoint_url" in value) || typeof value.endpoint_url !== "string" ||
    !("model_id" in value) || typeof value.model_id !== "string" ||
    !("route" in value) || (typeof value.route !== "string" && value.route !== null) ||
    !("updated_at" in value) || typeof value.updated_at !== "string"
  ) {
    throw new Error("The provider settings response was not recognized.");
  }
  return value as ProviderConfig;
}

export function ProviderConnectionForm() {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [endpointUrl, setEndpointUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [route, setRoute] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadState("loading");
    setError(null);
    void requestConfig("GET", undefined, controller.signal)
      .then((saved) => {
        if (controller.signal.aborted) return;
        setConfig(saved);
        setEndpointUrl(saved?.endpoint_url ?? "");
        setModelId(saved?.model_id ?? "");
        setRoute(saved?.route ?? "");
        setLoadState("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Provider settings are unavailable.");
        setLoadState("error");
      });
    return () => controller.abort();
  }, [loadAttempt]);

  function retryLoad() {
    setLoadAttempt((attempt) => attempt + 1);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || loadState !== "ready") return;
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const saved = await requestConfig("PUT", {
        endpoint_url: endpointUrl,
        model_id: modelId,
        ...(route.trim() ? { route: route.trim() } : {}),
      });
      if (!saved) throw new Error("Provider settings were not returned after saving.");
      setConfig(saved);
      setEndpointUrl(saved.endpoint_url);
      setModelId(saved.model_id);
      setRoute(saved.route ?? "");
      setMessage(`Saved provider configuration version ${saved.version}. This does not test or start a run.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Provider settings could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="content-section settings-card" aria-labelledby="provider-config-title">
      <div className="settings-card-heading">
        <div><p className="eyebrow">Inference · OpenAI-compatible Chat Completions</p><h2 id="provider-config-title">Model provider</h2></div>
        <StatusPill tone={config ? "green" : "amber"}>
          {config ? `Configured · v${config.version}` : loadState === "error" ? "Unavailable" : "Not configured"}
        </StatusPill>
      </div>
      <p>Save one provider endpoint and model selection for future Run snapshots. Saving does not verify provider availability or enable task execution.</p>
      {loadState === "loading" ? <p role="status" className="muted">Loading saved provider settings…</p> : null}
      {loadState === "error" ? (
        <div>
          <p className="inline-error" role="alert">{error}</p>
          <button className="button button-secondary" type="button" onClick={retryLoad}>Retry loading settings</button>
        </div>
      ) : null}
      {loadState === "ready" ? (
        <form className="draft-form" onSubmit={(event) => void save(event)}>
          <label className="preview-field" htmlFor="provider-endpoint-url">
            Endpoint URL
            <input
              id="provider-endpoint-url"
              type="url"
              required
              maxLength={2048}
              autoComplete="url"
              placeholder="http://localhost:20128/v1"
              value={endpointUrl}
              onChange={(event) => setEndpointUrl(event.target.value)}
            />
          </label>
          <label className="preview-field" htmlFor="provider-model-id">
            Model ID
            <input
              id="provider-model-id"
              type="text"
              required
              maxLength={200}
              autoComplete="off"
              placeholder="openrouter/cohere/north-mini-code:free"
              value={modelId}
              onChange={(event) => setModelId(event.target.value)}
            />
          </label>
          <label className="preview-field" htmlFor="provider-route">
            Route (optional)
            <input
              id="provider-route"
              type="text"
              maxLength={120}
              autoComplete="off"
              placeholder="free"
              value={route}
              onChange={(event) => setRoute(event.target.value)}
            />
          </label>
          <p className="field-note">No API key field. Provider credentials stay out of the browser and are not stored by this endpoint. Runner-side credentials, endpoint egress, connection testing, and execution are Phase 6 work.</p>
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {message ? <p className="success-message" role="status">{message}</p> : null}
          <div className="form-actions">
            <button className="button button-primary" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save provider settings"}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
