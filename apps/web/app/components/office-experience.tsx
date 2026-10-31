"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { Component, useCallback, useEffect, useMemo, useState, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import { createOfficeLayout } from "./office-layout";
import { assignOfficeAvatarModels } from "./office-avatar";
import type { OfficeSceneAgent } from "./office-scene";
import { StatusPill } from "./workspace-components";
import {
  requestActivitySnapshot,
  requestAgentAvailability,
  subscribeToActivity,
  type ActivityConnection,
  type ActivitySnapshot,
  type AgentAvailability,
  type AgentAvailabilityState,
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

function availabilityMap(records: AgentAvailability[]): Map<string, AgentAvailabilityState> {
  return new Map(records.map((record) => [record.agent_instance_id, record.availability] as const));
}

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

function availabilityLabel(availability: AgentAvailabilityState | undefined): string {
  switch (availability ?? "unknown") {
    case "idle": return "Idle · verified";
    case "working": return "Working · verified";
    case "waiting": return "Waiting · unresolved Run";
    case "unknown": return "Unknown · state uncertain";
  }
}

function availabilityShortLabel(availability: AgentAvailabilityState | undefined): string {
  switch (availability ?? "unknown") {
    case "idle": return "Idle";
    case "working": return "Working";
    case "waiting": return "Waiting";
    case "unknown": return "Unknown";
  }
}

function availabilityPriority(availability: AgentAvailabilityState): number {
  return availability === "working" ? 2 : availability === "waiting" ? 1 : 0;
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

function OfficeFallback({ agents, runtimeByAgent, availabilityByAgent, onSelectAgent, onRetry }: {
  agents: OfficeAgentInstance[];
  runtimeByAgent: Map<string, RuntimeProjection>;
  availabilityByAgent: Map<string, AgentAvailabilityState>;
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
            <span><strong>{agent.display_name} · {agent.definition_name}</strong><small>{agent.project_name ?? "No project assigned"} · {availabilityShortLabel(availabilityByAgent.get(agent.id))} · {projectionLabel(runtimeByAgent.get(agent.id))}</small></span>
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
  const [availabilityByAgent, setAvailabilityByAgent] = useState<Map<string, AgentAvailabilityState>>(new Map());
  const [availabilityLoadError, setAvailabilityLoadError] = useState<string | null>(null);
  const [agentPaginationLimitReached, setAgentPaginationLimitReached] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);
  const [agentLoadGeneration, setAgentLoadGeneration] = useState(0);
  const [activitySnapshot, setActivitySnapshot] = useState<ActivitySnapshot | null>(null);
  const [activityLoadError, setActivityLoadError] = useState<string | null>(null);
  const [activityConnection, setActivityConnection] = useState<ActivityConnection>("connecting");
  const [agentSearch, setAgentSearch] = useState("");
  const [projectFilter, setProjectFilter] = useState("all");
  const [definitionFilter, setDefinitionFilter] = useState("all");

  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
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
  const selectedAvailability = selectedAgent ? availabilityByAgent.get(selectedAgent.id) : undefined;
  const selectedDetail = selectedAgent
    ? `${availabilityLabel(selectedAvailability)}. Availability is derived from persisted unresolved Run dispatches and fresh activity projection evidence; uncertain states are never sent to the rest room. ${selectedRuntime ? `Latest event projection: ${selectedRuntime.status} (${selectedRuntime.freshness}).` : "No current event projection is available."} The 3D anchor is presentation-only, not a saved workspace location.`
    : selectedAgentId && !agentRecords
      ? "Refreshing saved Agent Instance details."
      : selectedAgentId
        ? "This Agent Instance is not in the current active records; it may have been archived."
        : "Select a persisted Agent Instance to inspect its derived availability and latest event projection.";
  const selectionStatus = selectedAgent
    ? availabilityLabel(selectedAvailability)
    : selectedAgentId ? "Unavailable" : "No selection";

  useEffect(() => {
    setSceneReady(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setAgentRecords(null);
    setAvailabilityByAgent(new Map());
    setAvailabilityLoadError(null);
    setAgentLoadError(null);
    setAgentPaginationLimitReached(false);
    void loadOfficeAgentInstances(controller.signal)
      .then(async ({ records, paginationLimitReached }) => {
        let availability = new Map<string, AgentAvailabilityState>();
        let availabilityError: string | null = null;
        try {
          const values = await requestAgentAvailability(
            records.map((agent) => agent.id),
            controller.signal,
          );
          availability = availabilityMap(values);
        } catch (reason: unknown) {
          if (controller.signal.aborted) return;
          availabilityError = reason instanceof Error ? reason.message : "Could not load Agent Availability.";
        }
        if (controller.signal.aborted) return;
        setAvailabilityByAgent(availability);
        setAvailabilityLoadError(availabilityError);
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
    const agents = agentRecords;
    if (agents === null || agents.length === 0) return;
    const instanceIds = agents.map((agent) => agent.id);
    const controller = new AbortController();
    let active = true;
    let refreshing = false;

    async function refreshAvailability() {
      if (!active || refreshing) return;
      refreshing = true;
      try {
        const values = await requestAgentAvailability(
          instanceIds,
          controller.signal,
        );
        if (active) {
          setAvailabilityByAgent(availabilityMap(values));
          setAvailabilityLoadError(null);
        }
      } catch (reason: unknown) {
        if (active && !controller.signal.aborted) {
          setAvailabilityByAgent(new Map());
          setAvailabilityLoadError(reason instanceof Error ? reason.message : "Could not load Agent Availability.");
        }
      } finally {
        refreshing = false;
      }
    }

    void refreshAvailability();
    const refreshTimer = setInterval(() => void refreshAvailability(), 5_000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(refreshTimer);
    };
  }, [agentRecords]);


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

  const avatarModelsByAgent = useMemo(
    () => assignOfficeAvatarModels((agentRecords ?? []).map((agent) => agent.id)),
    [agentRecords],
  );
  const sceneAgents = useMemo<OfficeSceneAgent[]>(() => {
    const sortedById = [...(agentRecords ?? [])].sort((left, right) => left.id.localeCompare(right.id));
    const priority = [...sortedById].sort((left, right) => {
      const leftAvailability = availabilityByAgent.get(left.id) ?? "unknown";
      const rightAvailability = availabilityByAgent.get(right.id) ?? "unknown";
      const leftPriority = availabilityPriority(leftAvailability);
      const rightPriority = availabilityPriority(rightAvailability);
      if (leftPriority !== rightPriority) return rightPriority - leftPriority;
      const leftCursor = runtimeByAgent.get(left.id)?.last_event_cursor ?? -1;
      const rightCursor = runtimeByAgent.get(right.id)?.last_event_cursor ?? -1;
      if (leftCursor !== rightCursor) return rightCursor - leftCursor;
      return left.id.localeCompare(right.id);
    });
    const fullAvatarIds = new Set(priority.slice(0, simplifiedScene ? MAX_MOBILE_AVATARS : MAX_DETAILED_AVATARS).map((agent) => agent.id));
    const layout = createOfficeLayout(sortedById.length);
    return sortedById.map((agent, index) => {
      const projection = runtimeByAgent.get(agent.id);
      const availability = availabilityByAgent.get(agent.id) ?? "unknown";
      const station = layout.stations[index]!;
      return {
        id: agent.id,
        displayName: agent.display_name,
        statusLabel: availabilityShortLabel(availability),
        animation: availability === "working"
          ? "working"
          : availability === "idle" ? projectionAnimation(projection) : "neutral",
        modelUrl: avatarModelsByAgent.get(agent.id)!,
        fullAvatar: fullAvatarIds.has(agent.id),
        selected: agent.id === selectedAgentId,
        accent: AGENT_ACCENTS[hashAgentId(agent.id) % AGENT_ACCENTS.length],
        availability,
        position: availability === "idle" ? station.restPosition : station.chairPosition,
        stationPosition: station.workstationPosition,
        chairPosition: station.chairPosition,
        restPosition: station.restPosition,
        restSeatPosition: station.restSeatPosition,
        routeToWork: station.routeToWork,
        routeToRest: station.routeToRest,
      };
    });
  }, [agentRecords, availabilityByAgent, runtimeByAgent, selectedAgentId, simplifiedScene, avatarModelsByAgent]);

  const availabilitySummary = useMemo(() => {
    const counts: Record<AgentAvailabilityState, number> = {
      idle: 0,
      working: 0,
      waiting: 0,
      unknown: 0,
    };
    for (const agent of agentRecords ?? []) {
      counts[availabilityByAgent.get(agent.id) ?? "unknown"] += 1;
    }
    return `${counts.working} work · ${counts.idle} rest · ${counts.waiting} wait · ${counts.unknown} unknown`;
  }, [agentRecords, availabilityByAgent]);
  useEffect(() => {
    if (agentRecords === null) return;

    const resolveHash = () => {
      const encodedId = window.location.hash.slice(1);
      if (!encodedId) {
        setSelectedAgentId(null);
        setFocusTarget([0, 0.8, 0]);
        setSelectionNotice(null);
        return;
      }

      let agentId = "";
      try {
        agentId = decodeURIComponent(encodedId);
      } catch {
        // An invalid fragment is treated like a stale Agent Instance link.
      }

      const agent = agentRecords.find((record) => record.id === agentId);
      if (agent) {
        setSelectedAgentId(agent.id);
        const agentIndex = [...agentRecords]
          .sort((left, right) => left.id.localeCompare(right.id))
          .findIndex((record) => record.id === agent.id);
        const station = createOfficeLayout(agentRecords.length).stations[agentIndex];
        if (station) {
          const availability = availabilityByAgent.get(agent.id) ?? "unknown";
          const position = availability === "idle" ? station.restPosition : station.chairPosition;
          setFocusTarget([position[0], 0.8, position[2]]);
        }
        return;
      }

      setSelectedAgentId(null);
      setFocusTarget([0, 0.8, 0]);
      setSelectionNotice("The linked Agent Instance is unavailable or archived.");
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    };

    resolveHash();
    window.addEventListener("hashchange", resolveHash);
    return () => window.removeEventListener("hashchange", resolveHash);
  }, [agentRecords]);

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
    setSelectionNotice(null);
    const visual = sceneAgents.find((item) => item.id === agent.id);
    if (visual) setFocusTarget([visual.position[0], 0.8, visual.position[2]]);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}#${encodeURIComponent(agent.id)}`,
    );
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

  const createTaskHref = selectedAgent
    ? `/tasks/new?agent_instance_id=${encodeURIComponent(selectedAgent.id)}${selectedAgent.project_id ? `&project_id=${encodeURIComponent(selectedAgent.project_id)}` : ""}`
    : null;

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
          <span className="layout-version">{simplifiedScene ? "Dedicated rest room · up to 8 detailed agents" : "Dedicated rest room · up to 20 detailed agents"}</span>
        </div>
        <div className="office-canvas-wrap">
          {!sceneReady ? <div className="scene-loading" role="status">Preparing Agent Instance view…</div> : sceneFailed ? (
            <OfficeFallback
              agents={visibleAgents}
              runtimeByAgent={runtimeByAgent}
              availabilityByAgent={availabilityByAgent}
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
                    ? `${agentRecords.length} agents · ${availabilitySummary}`
                    : "No registered Agent Instances · no sample agents are shown"}
            </div>
          ) : null}
        </div>
        <div className="scene-bottom-row">
          <p className="scene-hint" role="status">Select an Agent Instance to inspect its status. Idle agents take short visual breaks in the rest room; working agents use their workstation. Movement and furniture are presentation-only.</p>
          <span className="scene-motion-label">{reducedMotion ? "Reduced motion enabled" : "Idle movement is decorative; fresh work evidence drives typing"}</span>
        </div>
      </div>

      <aside className="office-inspector" aria-label="Agent Instance inspection">
        {selectionNotice ? <p className="office-selection-notice" role="status">{selectionNotice}</p> : null}
        <section className="inspector-card focus-card">
          <div className="inspector-heading"><div><p className="eyebrow">Selection</p><h2>{selectedTitle}</h2></div><StatusPill>{selectionStatus}</StatusPill></div>
          <p>{selectedDetail}</p>
          {selectedAgent ? <div className="selected-agent-facts"><span><strong>Definition</strong>{selectedAgent.definition_name}</span><span><strong>Project</strong>{selectedAgent.project_name ?? "Unassigned"}</span><span><strong>Agent Availability</strong>{availabilityLabel(selectedAvailability)}</span><span><strong>Run projection</strong>{selectedRuntime ? `${selectedRuntime.status} · ${selectedRuntime.freshness}` : "No current projection"}</span><span><strong>3D anchor</strong>Presentation only</span></div> : null}
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
            {createTaskHref ? <Link className="button" href={createTaskHref}>Create Task for agent</Link> : null}
          </div>
          {selectedAgent ? <p className="field-note">Creating a Task does not start a Run.</p> : null}
        </section>

        <section className="inspector-card office-agents-card" aria-labelledby="office-agents-heading">
          <div className="inspector-heading"><div><p className="eyebrow">Persisted records</p><h2 id="office-agents-heading">Agent Instances</h2></div><button className="text-button" type="button" disabled={agentRecords === null} onClick={() => setAgentLoadGeneration((generation) => generation + 1)}>Refresh</button></div>
          {agentRecords && agentRecords.length > 0 ? (
            <div className="office-agent-filters">
              <label className="preview-field">Search<input type="search" value={agentSearch} onChange={(event) => setAgentSearch(event.target.value)} placeholder="Name, definition, or project" /></label>
              <div className="office-filter-group" role="group" aria-label="Filter by Project">
                <span className="office-filter-label">Project</span>
                <div className="office-filter-chips">
                  <button className={projectFilter === "all" ? "office-filter-chip is-active" : "office-filter-chip"} type="button" aria-pressed={projectFilter === "all"} onClick={() => setProjectFilter("all")}>All projects</button>
                  {projects.map(([id, name]) => <button key={id} className={projectFilter === id ? "office-filter-chip is-active" : "office-filter-chip"} type="button" aria-pressed={projectFilter === id} onClick={() => setProjectFilter(id)}>{name}</button>)}
                </div>
              </div>
              <div className="office-filter-group" role="group" aria-label="Filter by Agent Definition">
                <span className="office-filter-label">Definition</span>
                <div className="office-filter-chips">
                  <button className={definitionFilter === "all" ? "office-filter-chip is-active" : "office-filter-chip"} type="button" aria-pressed={definitionFilter === "all"} onClick={() => setDefinitionFilter("all")}>All definitions</button>
                  {definitions.map((definition) => <button key={definition} className={definitionFilter === definition ? "office-filter-chip is-active" : "office-filter-chip"} type="button" aria-pressed={definitionFilter === definition} onClick={() => setDefinitionFilter(definition)}>{definition}</button>)}
                </div>
              </div>
              {agentPaginationLimitReached ? <span className="field-note" role="status">API pagination boundary reached; additional records may not be listed.</span> : null}
            </div>
          ) : null}
          {agentRecords === null && !agentLoadError ? <p className="empty-state" role="status">Loading registered Agent Instances…</p> : null}
          {agentLoadError ? <div className="empty-state"><strong>Agent Instances unavailable</strong><span role="alert">{agentLoadError}</span><button className="button button-secondary" type="button" onClick={() => setAgentLoadGeneration((generation) => generation + 1)}>Retry</button></div> : null}
          {agentRecords?.length === 0 ? <div className="empty-state"><strong>No registered Agent Instances.</strong><span>Create an instance from a saved Agent Definition. No runtime status is inferred.</span><Link className="text-link" href="/agents/new">Register an Agent Instance</Link></div> : null}
          {agentRecords && agentRecords.length > 0 && visibleAgents.length === 0 ? <p className="empty-state">No registered Agent Instances match these filters.</p> : null}
          <div className="office-agent-list" aria-label="Persisted Agent Instances">
            {visibleAgents.map((agent) => {
              const statusLabel = availabilityShortLabel(availabilityByAgent.get(agent.id));
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
                  <span className="office-agent-presence">{statusLabel}</span>
                </button>
              );
            })}
          </div>
          <p className="field-note" role={availabilityLoadError || activityLoadError ? "alert" : "status"}>
            {availabilityLoadError
              ? `Agent Availability unavailable; all agents remain unknown and outside the rest-room policy: ${availabilityLoadError}`
              : activityLoadError
                ? `Runtime projections unavailable: ${activityLoadError}`
                : `Agent Availability is derived from persisted unresolved Run dispatches and fresh projections. Browser event feed: ${activityConnection}. Event-feed connectivity does not indicate agent liveness.`}
          </p>
        </section>
      </aside>
    </section>
  );
}
