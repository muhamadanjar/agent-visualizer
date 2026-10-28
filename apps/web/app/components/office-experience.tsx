"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { Component, useCallback, useEffect, useMemo, useState, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import type { OfficeSceneAgent } from "./office-scene";
import { DisabledWorkAction, StatusPill } from "./workspace-components";
import {
  requestActivitySnapshot,
  subscribeToActivity,
  type ActivityConnection,
  type ActivitySnapshot,
  type RuntimeProjection,
} from "../lib/activity";

const OfficeScene = dynamic(() => import("./office-scene").then((module) => module.OfficeScene), {
  ssr: false,
  loading: () => <div className="scene-loading" role="status">Preparing the Agent Instance scene…</div>,
});

type OfficeAgentInstance = {
  id: string;
  display_name: string;
  definition_name: string;
  project_id: string | null;
  project_name: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
};
type OfficeAgentInstancePage = { records: OfficeAgentInstance[]; paginationLimitReached: boolean };
type VisualAnimation = OfficeSceneAgent["animation"];

const OFFICE_AGENT_PAGE_SIZE = 200;
const OFFICE_AGENT_MAX_OFFSET = 1_000_000;
const MAX_DETAILED_AVATARS = 20;
const MAX_MOBILE_AVATARS = 8;
const AGENT_ACCENTS = ["#477b78", "#946a43", "#6c719b", "#a35b4d", "#557c59", "#7d6391", "#417892"];

function isOfficeAgentInstance(value: unknown): value is OfficeAgentInstance {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.id === "string"
    && typeof record.display_name === "string"
    && typeof record.definition_name === "string"
    && (typeof record.project_id === "string" || record.project_id === null)
    && (typeof record.project_name === "string" || record.project_name === null)
    && typeof record.archived === "boolean"
    && typeof record.created_at === "string"
    && typeof record.updated_at === "string";
}

async function loadOfficeAgentInstances(signal: AbortSignal): Promise<OfficeAgentInstancePage> {
  const records: OfficeAgentInstance[] = [];
  for (let offset = 0; ; offset += OFFICE_AGENT_PAGE_SIZE) {
    const response = await fetch(`/api/v1/agent-instances?include_archived=false&limit=${OFFICE_AGENT_PAGE_SIZE}&offset=${offset}`, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal,
    });
    if (response.status === 401) throw new Error("Your owner session expired. Sign in again to load Agent Instances.");
    if (!response.ok) throw new Error("Could not load registered Agent Instances. Check the API and retry.");
    const value: unknown = await response.json();
    if (!Array.isArray(value)) throw new Error("The Agent Instance response was not recognized.");
    const page: OfficeAgentInstance[] = [];
    for (const item of value) {
      if (!isOfficeAgentInstance(item)) throw new Error("The Agent Instance response was not recognized.");
      if (!item.archived) page.push(item);
    }
    records.push(...page);
    if (value.length < OFFICE_AGENT_PAGE_SIZE) return { records, paginationLimitReached: false };
    if (offset === OFFICE_AGENT_MAX_OFFSET) return { records, paginationLimitReached: true };
  }
}

function hashAgentId(id: string): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

function projectionLabel(projection: RuntimeProjection | undefined): string {
  if (!projection) return "Status unavailable";
  return projection.freshness === "fresh"
    ? `${projection.status} · fresh`
    : `Status unavailable · last verified ${projection.status} (${projection.freshness})`;
}

function projectionAnimation(projection: RuntimeProjection | undefined): VisualAnimation {
  if (!projection || projection.freshness !== "fresh") return "neutral";
  if (projection.status === "running" && projection.last_event_type !== "run.cancellation_pending") return "working";
  if (projection.status === "succeeded") return "success";
  return "neutral";
}

type SceneErrorBoundaryProps = { children: ReactNode; onError: () => void };
type SceneErrorBoundaryState = { failed: boolean };

class SceneErrorBoundary extends Component<SceneErrorBoundaryProps, SceneErrorBoundaryState> {
  state: SceneErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): SceneErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    this.props.onError();
  }

  render() {
    if (this.state.failed) return null;
    return this.props.children;
  }
}

function OfficeFallback({ agents, runtimeByAgent, onSelectAgent, onRetry }: {
  agents: OfficeAgentInstance[];
  runtimeByAgent: Map<string, RuntimeProjection>;
  onSelectAgent: (agent: OfficeAgentInstance) => void;
  onRetry: () => void;
}) {
  return (
    <div className="office-fallback">
      <div>
        <p className="eyebrow">Accessible Agent Instance view</p>
        <h2>3D scene unavailable</h2>
        <p>The interactive scene could not start. Persisted Agent Instances remain available in the keyboard-friendly list below; presentation anchors do not indicate workspace locations.</p>
        <button type="button" className="button button-secondary" onClick={onRetry}>Retry 3D scene</button>
      </div>
      <div className="fallback-agent-list">
        {agents.map((agent) => (
          <button className="fallback-agent" key={agent.id} type="button" onClick={() => onSelectAgent(agent)}>
            <span className="agent-avatar" aria-hidden="true">{agent.display_name.slice(0, 1)}</span>
            <span><strong>{agent.display_name} · {agent.definition_name}</strong><small>{agent.project_name ?? "No project assigned"} · {projectionLabel(runtimeByAgent.get(agent.id))}</small></span>
            <span aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function OfficeExperience() {
  const [zoomAdjustment, setZoomAdjustment] = useState(0);
  const [focusTarget, setFocusTarget] = useState<[number, number, number]>([0, 0.8, 0]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const [sceneFailed, setSceneFailed] = useState(false);
  const [sceneGeneration, setSceneGeneration] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [simplifiedScene, setSimplifiedScene] = useState(false);
  const [agentRecords, setAgentRecords] = useState<OfficeAgentInstance[] | null>(null);
  const [agentPaginationLimitReached, setAgentPaginationLimitReached] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);
  const [agentLoadGeneration, setAgentLoadGeneration] = useState(0);
  const [activitySnapshot, setActivitySnapshot] = useState<ActivitySnapshot | null>(null);
  const [activityLoadError, setActivityLoadError] = useState<string | null>(null);
  const [activityConnection, setActivityConnection] = useState<ActivityConnection>("connecting");
  const [agentSearch, setAgentSearch] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [definitionFilter, setDefinitionFilter] = useState("all");

  const runtimeByAgent = useMemo(() => {
    const latestByAgent = new Map<string, RuntimeProjection>();
    for (const projection of activitySnapshot?.projections ?? []) {
      const current = latestByAgent.get(projection.agent_instance_id);
      if (!current || projection.last_event_cursor > current.last_event_cursor) {
        latestByAgent.set(projection.agent_instance_id, projection);
      }
    }
    return latestByAgent;
  }, [activitySnapshot]);

  const selectedAgent = selectedAgentId ? agentRecords?.find((agent) => agent.id === selectedAgentId) : undefined;
  const selectedRuntime = selectedAgent ? runtimeByAgent.get(selectedAgent.id) : undefined;
  const selectedTitle = selectedAgent?.display_name
    ?? (selectedAgentId ? agentRecords === null && !agentLoadError ? "Refreshing Agent Instance…" : "Agent Instance unavailable" : "Select an Agent Instance");
  const selectedDetail = selectedAgent
    ? selectedRuntime
      ? `Persisted Agent Instance. Latest event projection: ${selectedRuntime.status} (${selectedRuntime.freshness}). The 3D anchor is presentation-only, not a saved workspace location.`
      : activityLoadError
        ? "Persisted Agent Instance. Runtime event projection could not be loaded; live status is unavailable. The 3D anchor is presentation-only."
        : "Persisted Agent Instance. No runtime event projection is available; status is unavailable. The 3D anchor is presentation-only."
    : selectedAgentId && !agentRecords
      ? "Refreshing saved Agent Instance details."
      : selectedAgentId
        ? "This Agent Instance is not in the current active records; it may have been archived."
        : "Select a persisted Agent Instance in the scene or list to inspect its last verified event projection.";
  const selectionStatus = selectedRuntime
    ? selectedRuntime.freshness === "fresh" ? `${selectedRuntime.status} · fresh` : `Stale · last ${selectedRuntime.status}`
    : selectedAgent ? "Status unavailable" : selectedAgentId ? "Unavailable" : "No selection";

  useEffect(() => {
    setSceneReady(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setAgentRecords(null);
    setAgentLoadError(null);
    setAgentPaginationLimitReached(false);
    void loadOfficeAgentInstances(controller.signal)
      .then(({ records, paginationLimitReached }) => {
        if (controller.signal.aborted) return;
        setAgentRecords(records);
        setAgentPaginationLimitReached(paginationLimitReached);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setAgentLoadError(reason instanceof Error ? reason.message : "Could not load registered Agent Instances.");
      });
    return () => controller.abort();
  }, [agentLoadGeneration]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    async function loadActivitySnapshot() {
      try {
        const snapshot = await requestActivitySnapshot(controller.signal);
        if (active) {
          setActivitySnapshot(snapshot);
          setActivityLoadError(null);
        }
      } catch (reason: unknown) {
        if (active && !controller.signal.aborted) {
          setActivityLoadError(reason instanceof Error ? reason.message : "Could not load runtime event projections.");
        }
      }
    }

    void loadActivitySnapshot();
    const refreshTimer = setInterval(() => void loadActivitySnapshot(), 5_000);
    const unsubscribe = subscribeToActivity((message) => {
      if (!active) return;
      if (message.type === "connection") {
        setActivityConnection(message.state);
      } else if (message.type === "snapshot") {
        setActivityConnection("connected");
        setActivitySnapshot(message.snapshot);
        setActivityLoadError(null);
      } else if (message.events.length > 0) {
        void loadActivitySnapshot();
      }
    });

    return () => {
      active = false;
      controller.abort();
      clearInterval(refreshTimer);
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const compactViewport = window.matchMedia("(max-width: 820px)");
    const update = () => {
      setReducedMotion(motionPreference.matches);
      setSimplifiedScene(compactViewport.matches);
    };
    update();
    motionPreference.addEventListener("change", update);
    compactViewport.addEventListener("change", update);
    return () => {
      motionPreference.removeEventListener("change", update);
      compactViewport.removeEventListener("change", update);
    };
  }, []);

  const sceneAgents = useMemo<OfficeSceneAgent[]>(() => {
    const sortedById = [...(agentRecords ?? [])].sort((left, right) => left.id.localeCompare(right.id));
    const priority = [...sortedById].sort((left, right) => {
      const leftProjection = runtimeByAgent.get(left.id);
      const rightProjection = runtimeByAgent.get(right.id);
      const leftActive = leftProjection?.freshness === "fresh" && leftProjection.status === "running" ? 1 : 0;
      const rightActive = rightProjection?.freshness === "fresh" && rightProjection.status === "running" ? 1 : 0;
      if (leftActive !== rightActive) return rightActive - leftActive;
      const leftCursor = leftProjection?.last_event_cursor ?? -1;
      const rightCursor = rightProjection?.last_event_cursor ?? -1;
      if (leftCursor !== rightCursor) return rightCursor - leftCursor;
      return left.id.localeCompare(right.id);
    });
    const fullAvatarIds = new Set(priority.slice(0, simplifiedScene ? MAX_MOBILE_AVATARS : MAX_DETAILED_AVATARS).map((agent) => agent.id));
    const columns = 16;
    const rows = Math.max(1, Math.ceil(sortedById.length / columns));
    return sortedById.map((agent, index) => {
      const projection = runtimeByAgent.get(agent.id);
      const row = Math.floor(index / columns);
      const column = index % columns;
      const rowLength = Math.min(columns, sortedById.length - row * columns);
      return {
        id: agent.id,
        displayName: agent.display_name,
        statusLabel: projectionLabel(projection),
        animation: projectionAnimation(projection),
        fullAvatar: fullAvatarIds.has(agent.id),
        selected: agent.id === selectedAgentId,
        accent: AGENT_ACCENTS[hashAgentId(agent.id) % AGENT_ACCENTS.length],
        position: [(column - (rowLength - 1) / 2) * 0.95, 0, (row - (rows - 1) / 2) * 1.1],
      };
    });
  }, [agentRecords, runtimeByAgent, selectedAgentId, simplifiedScene]);

  const projects = useMemo(() => {
    const values = new Map<string, string>();
    for (const agent of agentRecords ?? []) {
      if (agent.project_id && agent.project_name) values.set(agent.project_id, agent.project_name);
    }
    return [...values.entries()].sort((left, right) => left[1].localeCompare(right[1]));
  }, [agentRecords]);
  const definitions = useMemo(
    () => [...new Set((agentRecords ?? []).map((agent) => agent.definition_name))].sort(),
    [agentRecords],
  );
  const visibleAgents = useMemo(() => (agentRecords ?? []).filter((agent) => {
    const matchesSearch = `${agent.display_name} ${agent.definition_name} ${agent.project_name ?? ""}`
      .toLowerCase().includes(agentSearch.trim().toLowerCase());
    return matchesSearch
      && (projectFilter === "all" || agent.project_id === projectFilter)
      && (definitionFilter === "all" || agent.definition_name === definitionFilter);
  }), [agentRecords, agentSearch, projectFilter, definitionFilter]);

  const selectAgent = useCallback((agent: OfficeAgentInstance) => {
    setSelectedAgentId(agent.id);
    const visual = sceneAgents.find((item) => item.id === agent.id);
    if (visual) setFocusTarget([visual.position[0], 0.8, visual.position[2]]);
  }, [sceneAgents]);
  const selectSceneAgent = useCallback((agentId: string) => {
    const agent = agentRecords?.find((item) => item.id === agentId);
    if (agent) selectAgent(agent);
  }, [agentRecords, selectAgent]);

  function focusSelectedAgent() {
    const visual = sceneAgents.find((agent) => agent.id === selectedAgentId);
    if (!visual) return;
    setFocusTarget([visual.position[0], 0.8, visual.position[2]]);
  }

  function resetCamera() {
    setZoomAdjustment(0);
    setFocusTarget([0, 0.8, 0]);
  }

  return (
    <section className="office-experience" aria-label="Agent Instance 3D visualization">
      <div className="office-scene-column">
        <div className="scene-toolbar" aria-label="Agent scene controls">
          <div className="camera-actions">
            <button type="button" className="quiet-button" aria-label="Zoom in" disabled={sceneFailed || !sceneReady} onClick={() => setZoomAdjustment((value) => Math.max(-15, value - 1))}>Zoom +</button>
            <button type="button" className="quiet-button" aria-label="Zoom out" disabled={sceneFailed || !sceneReady} onClick={() => setZoomAdjustment((value) => Math.min(18, value + 1))}>Zoom −</button>
            <button type="button" className="quiet-button" disabled={sceneFailed || !sceneReady} onClick={focusSelectedAgent}>Focus selected</button>
            <button type="button" className="quiet-button" disabled={sceneFailed || !sceneReady} onClick={resetCamera}>Reset view</button>
          </div>
          <span className="layout-version">{simplifiedScene ? "Mobile simplified · up to 8 detailed rigs" : "Up to 20 detailed rigs · all positions are visual-only"}</span>
        </div>
        <div className="office-canvas-wrap">
          {!sceneReady ? <div className="scene-loading" role="status">Preparing Agent Instance view…</div> : sceneFailed ? (
            <OfficeFallback
              agents={visibleAgents}
              runtimeByAgent={runtimeByAgent}
              onSelectAgent={selectAgent}
              onRetry={() => {
                setSceneFailed(false);
                setSceneGeneration((value) => value + 1);
              }}
            />
          ) : (
            <SceneErrorBoundary key={sceneGeneration} onError={() => setSceneFailed(true)}>
              <OfficeScene
                agents={sceneAgents}
                zoom={15 + zoomAdjustment}
                focusTarget={focusTarget}
                reducedMotion={reducedMotion}
                simplified={simplifiedScene}
                onSelectAgent={selectSceneAgent}
              />
            </SceneErrorBoundary>
          )}
          {sceneReady && !sceneFailed ? (
            <div className="scene-view-label" aria-live="polite">
              {agentLoadError
                ? "Agent Instances unavailable"
                : agentRecords === null
                  ? "Loading persisted Agent Instances…"
                  : agentRecords.length
                    ? `${agentRecords.length} persisted Agent Instances · ${runtimeByAgent.size} event projections`
                    : "No registered Agent Instances · no sample agents are shown"}
            </div>
          ) : null}
        </div>
        <div className="scene-bottom-row">
          <p className="scene-hint" role="status">Select an Agent Instance to inspect it. Orbit, pan, and zoom the scene; display anchors are not workspace assignments.</p>
          <span className="scene-motion-label">{reducedMotion ? "Reduced motion enabled" : "Event-backed animation only"}</span>
        </div>
      </div>

      <aside className="office-inspector" aria-label="Agent Instance inspection">
        <section className="inspector-card focus-card">
          <div className="inspector-heading"><div><p className="eyebrow">Selection</p><h2>{selectedTitle}</h2></div><StatusPill>{selectionStatus}</StatusPill></div>
          <p>{selectedDetail}</p>
          {selectedAgent ? <div className="selected-agent-facts"><span><strong>Definition</strong>{selectedAgent.definition_name}</span><span><strong>Project</strong>{selectedAgent.project_name ?? "Unassigned"}</span><span><strong>Runtime status</strong>{selectedRuntime ? `${selectedRuntime.status} · ${selectedRuntime.freshness}` : "Status unavailable"}</span><span><strong>3D anchor</strong>Presentation only</span></div> : null}
          {selectedAgent ? (
            <div className="selected-agent-facts" role="group" aria-label="Verified Activity Event details">
              <span>
                <strong>Latest verified event</strong>
                {selectedRuntime ? (
                  <time dateTime={selectedRuntime.last_event_at}>
                    {selectedRuntime.last_event_type} · {new Date(selectedRuntime.last_event_at).toLocaleString()}
                  </time>
                ) : "No verified Activity Event"}
              </span>
              <span className="activity-summary">
                <strong>Last verified Safe Tool-Action Summary</strong>
                {selectedRuntime?.safe_action_summary ?? "No verified Safe Tool-Action Summary"}
              </span>
            </div>
          ) : null}
          <div className="inspector-actions">
            {selectedAgent ? <Link className="text-link" href={`/agents/${selectedAgent.id}`}>Open Agent Instance</Link> : null}
            {selectedAgent ? <DisabledWorkAction reason="The Engine/Runner is not connected.">Start or message agent</DisabledWorkAction> : null}
          </div>
        </section>

        <section className="inspector-card office-agents-card" aria-labelledby="office-agents-heading">
          <div className="inspector-heading"><div><p className="eyebrow">Persisted records</p><h2 id="office-agents-heading">Agent Instances</h2></div><button className="text-button" type="button" disabled={agentRecords === null} onClick={() => setAgentLoadGeneration((generation) => generation + 1)}>Refresh</button></div>
          {agentRecords && agentRecords.length > 0 ? (
            <div className="office-agent-filters">
              <label className="preview-field">Search<input type="search" value={agentSearch} onChange={(event) => setAgentSearch(event.target.value)} placeholder="Name, definition, or project" /></label>
              <label className="preview-field">Project<select value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}><option value="all">All projects</option>{projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
              <label className="preview-field">Definition<select value={definitionFilter} onChange={(event) => setDefinitionFilter(event.target.value)}><option value="all">All definitions</option>{definitions.map((definition) => <option key={definition}>{definition}</option>)}</select></label>
              {agentPaginationLimitReached ? <span className="field-note" role="status">API pagination boundary reached; additional records may not be listed.</span> : null}
            </div>
          ) : null}
          {agentRecords === null && !agentLoadError ? <p className="empty-state" role="status">Loading registered Agent Instances…</p> : null}
          {agentLoadError ? <div className="empty-state"><strong>Agent Instances unavailable</strong><span role="alert">{agentLoadError}</span><button className="button button-secondary" type="button" onClick={() => setAgentLoadGeneration((generation) => generation + 1)}>Retry</button></div> : null}
          {agentRecords?.length === 0 ? <div className="empty-state"><strong>No registered Agent Instances.</strong><span>Create an instance from a saved Agent Definition. No runtime status is inferred.</span><Link className="text-link" href="/agents/new">Register an Agent Instance</Link></div> : null}
          {agentRecords && agentRecords.length > 0 && visibleAgents.length === 0 ? <p className="empty-state">No registered Agent Instances match these filters.</p> : null}
          <div className="office-agent-list" aria-label="Persisted Agent Instances">
            {visibleAgents.map((agent) => {
              const runtime = runtimeByAgent.get(agent.id);
              const runtimeLabel = projectionLabel(runtime);
              return (
                <button
                  className={selectedAgent?.id === agent.id ? "office-agent-link is-selected" : "office-agent-link"}
                  key={agent.id}
                  type="button"
                  aria-pressed={selectedAgent?.id === agent.id}
                  onClick={() => selectAgent(agent)}
                >
                  <span className="agent-avatar" aria-hidden="true" style={{ "--avatar-color": AGENT_ACCENTS[hashAgentId(agent.id) % AGENT_ACCENTS.length] } as CSSProperties}>{agent.display_name.slice(0, 1)}</span>
                  <span className="office-agent-label"><strong>{agent.display_name}</strong><small>{agent.definition_name}</small><small>{agent.project_name ?? "No project assigned"}</small></span>
                  <span className="office-agent-presence">{runtimeLabel}</span>
                </button>
              );
            })}
          </div>
          <p className="field-note" role={activityLoadError ? "alert" : "status"}>
            {activityLoadError
              ? `Runtime projections unavailable: ${activityLoadError}`
              : `Browser event feed: ${activityConnection}. Feed connectivity does not indicate agent liveness. Scene anchors are presentation-only.`}
          </p>
        </section>
      </aside>
    </section>
  );
}
