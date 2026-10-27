"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { Component, useCallback, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import {
  isOfficePositionWalkable,
  OFFICE_LAYOUTS,
  OFFICE_LAYOUT_ID,
  SAFE_OFFICE_SPAWN,
  worldToOfficeCell,
  type OfficeLayoutId,
  type OfficePosition,
} from "./office-layout";
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
  loading: () => <div className="scene-loading" role="status">Preparing local 3D office…</div>,
});

type CameraMode = "follow" | "overview";
type SceneSelection = { kind: "object"; title: string; detail: string } | { kind: "agent"; agentId: string };
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
type StoredOfficeState = { schemaVersion: 2; position: OfficePosition };
const OFFICE_STORAGE_KEY = "agent-office:office:1";
const LEGACY_OFFICE_STORAGE_KEY = "agent-office:sample-office:1";

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

const OFFICE_AGENT_PAGE_SIZE = 200;
const OFFICE_AGENT_MAX_OFFSET = 1_000_000;

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
      page.push(item);
    }
    records.push(...page);
    if (page.length < OFFICE_AGENT_PAGE_SIZE) return { records, paginationLimitReached: false };
    if (offset === OFFICE_AGENT_MAX_OFFSET) return { records, paginationLimitReached: true };
  }
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

function OfficeFallback({ agents, onSelectAgent, onRetry }: {
  agents: OfficeAgentInstance[];
  onSelectAgent: (agent: OfficeAgentInstance) => void;
  onRetry: () => void;
}) {
  return (
    <div className="office-fallback">
      <div>
        <p className="eyebrow">Accessible office view</p>
        <h2>3D scene unavailable</h2>
        <p>The interactive scene could not start. Saved Agent Instances remain available in the keyboard-friendly list below; live status and spatial locations are unavailable.</p>
        <button type="button" className="button button-secondary" onClick={onRetry}>Retry 3D scene</button>
      </div>
      <div className="fallback-agent-list">
        {agents.map((agent) => <button className="fallback-agent" key={agent.id} type="button" onClick={() => onSelectAgent(agent)}><span className="agent-avatar" aria-hidden="true">{agent.display_name.slice(0, 1)}</span><span><strong>{agent.display_name} · {agent.definition_name}</strong><small>{agent.project_name ?? "No project assigned"} · Live status unavailable</small></span><span aria-hidden="true">→</span></button>)}
      </div>
    </div>
  );
}

export function OfficeExperience() {
  const [cameraMode, setCameraMode] = useState<CameraMode>("overview");
  const [zoom, setZoom] = useState(15);
  const [focusTarget, setFocusTarget] = useState<[number, number, number]>([0, 0, 0]);
  const [selected, setSelected] = useState<SceneSelection | null>(null);
  const layoutId: OfficeLayoutId = OFFICE_LAYOUT_ID;
  const [playerPosition, setPlayerPosition] = useState<OfficePosition>(SAFE_OFFICE_SPAWN);
  const [avatarPosition, setAvatarPosition] = useState<OfficePosition>(SAFE_OFFICE_SPAWN);
  const [activeDestination, setActiveDestination] = useState<OfficePosition | null>(null);
  const [sceneReady, setSceneReady] = useState(false);
  const [sceneFailed, setSceneFailed] = useState(false);
  const [sceneGeneration, setSceneGeneration] = useState(0);
  const [positionNotice, setPositionNotice] = useState("Loading office view settings…");
  const [movementMessage, setMovementMessage] = useState("Use WASD or arrow keys, or click a clear floor tile to move your local avatar.");
  const [reducedMotion, setReducedMotion] = useState(false);
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
  const lastSavedCellRef = useRef<string | null>(null);
  const firstPositionReportRef = useRef(true);
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
  const selectedAgent = selected?.kind === "agent"
    ? agentRecords?.find((agent) => agent.id === selected.agentId)
    : undefined;
  const selectedRuntime = selectedAgent ? runtimeByAgent.get(selectedAgent.id) : undefined;
  const selectedTitle = selected?.kind === "agent"
    ? selectedAgent?.display_name ?? (agentRecords === null && !agentLoadError ? "Refreshing Agent Instance…" : "Agent Instance unavailable")
    : selected?.title ?? "Your local avatar";
  const selectedDetail = selected?.kind === "agent"
    ? selectedAgent
      ? selectedRuntime
        ? `Persisted Agent Instance. Latest stored event projection: ${selectedRuntime.status} (${selectedRuntime.freshness}); no verified 3D position is available.`
        : activityLoadError
          ? "Persisted Agent Instance. Runtime event projection could not be loaded; office position remains unavailable."
          : "Persisted Agent Instance. No runtime event projection is available; office position remains unavailable."
      : agentLoadError
        ? "Saved Agent Instance details could not be loaded. Retry the list request."
        : agentRecords === null
          ? "Refreshing saved Agent Instance details."
          : "This Agent Instance is not in the current active records; it may have been archived."
    : selected?.detail ?? "The office layout is a built-in template. Agent runtime status and spatial positions are unavailable.";
  const selectionStatus = selected?.kind === "agent"
    ? selectedRuntime
      ? `${selectedRuntime.freshness} projection`
      : selectedAgent ? "No runtime projection" : agentRecords === null && !agentLoadError ? "Refreshing" : "Unavailable"
    : "Office template";
  useEffect(() => {
    let nextPosition: OfficePosition = SAFE_OFFICE_SPAWN;
    let hasRestoredPosition = false;
    let notice = "Using the built-in office template; this browser stores only your local avatar position.";
    try {
      const stored = window.localStorage.getItem(OFFICE_STORAGE_KEY)
        ?? window.localStorage.getItem(LEGACY_OFFICE_STORAGE_KEY);
      if (stored) {
        const value: unknown = JSON.parse(stored);
        if (value && typeof value === "object") {
          const record = value as Record<string, unknown>;
          const position = record.position;
          const legacyLayout = record.layoutId === "sample-v1" || record.layoutId === "sample-v2";
          const validVersion = record.schemaVersion === 2
            || (record.schemaVersion === 1 && (legacyLayout || record.layoutId === OFFICE_LAYOUT_ID));
          if (validVersion && Array.isArray(position) && position.length === 2
            && typeof position[0] === "number" && Number.isFinite(position[0])
            && typeof position[1] === "number" && Number.isFinite(position[1])) {
            const savedPosition: OfficePosition = [position[0], position[1]];
            if (isOfficePositionWalkable(savedPosition, layoutId)) {
              nextPosition = savedPosition;
              hasRestoredPosition = true;
              notice = "Office template and local avatar position restored from this browser.";
            } else {
              notice = "Saved local avatar position is blocked by the office template; using its safe spawn.";
            }
          } else {
            notice = "Saved office-view settings are invalid or outdated; using the safe spawn.";
          }
        }
      }
    } catch {
      notice = "Browser storage is unavailable; movement remains local to this page.";
    }
    const cell = worldToOfficeCell(nextPosition[0], nextPosition[1]);
    lastSavedCellRef.current = hasRestoredPosition && cell ? `${cell.x},${cell.z}` : null;
    setPlayerPosition(nextPosition);
    setAvatarPosition(nextPosition);
    setPositionNotice(notice);
    setSceneReady(true);
  }, [layoutId]);

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
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);

  const saveOfficeState = useCallback((position: OfficePosition, announce = true) => {
    const cell = worldToOfficeCell(position[0], position[1]);
    lastSavedCellRef.current = cell ? `${cell.x},${cell.z}` : null;
    try {
      const saved: StoredOfficeState = { schemaVersion: 2, position };
      window.localStorage.setItem(OFFICE_STORAGE_KEY, JSON.stringify(saved));
      if (announce) setPositionNotice("Local avatar position saved in this browser only.");
    } catch {
      if (announce) setPositionNotice("Browser storage is unavailable; local movement will reset when this page closes.");
    }
  }, []);

  const handlePlayerPosition = useCallback((x: number, z: number) => {
    const announce = !firstPositionReportRef.current;
    firstPositionReportRef.current = false;
    setPlayerPosition([x, z]);
    const cell = worldToOfficeCell(x, z);
    if (!cell || lastSavedCellRef.current === `${cell.x},${cell.z}`) return;
    saveOfficeState([x, z], announce);
  }, [saveOfficeState]);

  const handleDestinationChange = useCallback((position: OfficePosition | null) => {
    setActiveDestination((current) => {
      if (current === null && position === null) return current;
      if (current && position && current[0] === position[0] && current[1] === position[1]) return current;
      return position;
    });
  }, []);

  const selectAgent = useCallback((agent: OfficeAgentInstance) => {
    setSelected({ kind: "agent", agentId: agent.id });
  }, []);

  const officeObjects = useMemo(() => [
    ...OFFICE_LAYOUTS[layoutId].furniture.map((item) => ({ title: item.title, point: [item.x, item.height + 0.08, item.z] as [number, number, number], detail: item.detail })),
    { title: "Workspace task board", point: [0, 1.2, -5.8] as [number, number, number], detail: "A fixed office-template object. Open the persisted Task Board to inspect real Tasks." },
    { title: "Office entrance", point: [0, 0, 6] as [number, number, number], detail: "A visual doorway marker; workspace permissions are not determined by room or proximity." },
  ], [layoutId]);
  const selectObject = useCallback((title: string, detail: string) => {
    setSelected({ kind: "object", title, detail });
    const object = officeObjects.find((item) => item.title === title);
    if (object) {
      setCameraMode("overview");
      setFocusTarget(object.point);
    }
  }, [officeObjects]);

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


  function resetCamera() {
    setCameraMode("overview");
    setZoom(15);
    setFocusTarget([0, 0, 0]);
  }

  function focusLocalAvatar() {
    setSelected({ kind: "object", title: "Your local avatar", detail: "Presentation-only movement; not an Agent Instance or runtime session." });
    setFocusTarget([playerPosition[0], 0, playerPosition[1]]);
  }

  return (
    <section className="office-experience" aria-label="Interactive office template">
      <div className="office-scene-column">
        <div className="scene-toolbar" aria-label="Office scene controls">
          <div className="camera-switch" role="group" aria-label="Camera mode">
            <button type="button" className={cameraMode === "follow" ? "camera-button is-active" : "camera-button"} disabled={sceneFailed || !sceneReady} onClick={() => setCameraMode("follow")}>Follow</button>
            <button type="button" className={cameraMode === "overview" ? "camera-button is-active" : "camera-button"} disabled={sceneFailed || !sceneReady} onClick={() => setCameraMode("overview")}>Overview</button>
          </div>
          <div className="camera-actions">
            <button type="button" className="quiet-button" aria-label="Zoom in" disabled={sceneFailed || !sceneReady} onClick={() => setZoom((value) => Math.max(9, value - 1))}>Zoom +</button>
            <button type="button" className="quiet-button" aria-label="Zoom out" disabled={sceneFailed || !sceneReady} onClick={() => setZoom((value) => Math.min(23, value + 1))}>Zoom −</button>
            <button type="button" className="quiet-button" disabled={sceneFailed || !sceneReady} onClick={resetCamera}>Reset view</button>
          </div>
          <span className="layout-version">Office template · v1</span>
        </div>
        <div className="office-canvas-wrap">
          {!sceneReady ? <div className="scene-loading" role="status">Restoring office view…</div> : sceneFailed ? (
            <OfficeFallback
              agents={visibleAgents}
              onSelectAgent={selectAgent}
              onRetry={() => {
                const retryPosition = isOfficePositionWalkable(playerPosition, layoutId) ? playerPosition : SAFE_OFFICE_SPAWN;
                setPlayerPosition(retryPosition);
                setAvatarPosition(retryPosition);
                saveOfficeState(retryPosition);
                setSceneFailed(false);
                setSceneGeneration((value) => value + 1);
              }}
            />
          ) : (
            <SceneErrorBoundary key={sceneGeneration} onError={() => setSceneFailed(true)}>
              <OfficeScene
                cameraMode={cameraMode}
                zoom={zoom}
                focusTarget={focusTarget}
                reducedMotion={reducedMotion}
                layoutId={layoutId}
                spawnPosition={avatarPosition}
                activeDestination={activeDestination}
                onSelectObject={selectObject}
                onPlayerPosition={handlePlayerPosition}
                onMovementMessage={setMovementMessage}
                onDestinationChange={handleDestinationChange}
              />
            </SceneErrorBoundary>
          )}
          {sceneReady && !sceneFailed ? <div className="scene-view-label" aria-live="polite">{agentLoadError ? "Agent Instances unavailable" : agentRecords === null ? "Loading Agent Instances…" : agentRecords.length ? `${agentRecords.length} registered · ${runtimeByAgent.size} event-backed runtime projections · agent locations unavailable` : "No registered Agent Instances"}</div> : null}
        </div>
        <div className="scene-bottom-row">
          <p className="movement-help" role="status">{movementMessage}</p>
          <span className="scene-motion-label" aria-live="polite">{positionNotice}</span>
          <span className="scene-motion-label">{reducedMotion ? "Reduced motion" : "Local movement"}</span>
        </div>
      </div>

      <aside className="office-inspector" aria-label="Office information and navigation">
        <section className="inspector-card focus-card">
          <div className="inspector-heading"><div><p className="eyebrow">Selection</p><h2>{selectedTitle}</h2></div><StatusPill>{selectionStatus}</StatusPill></div>
          <p>{selectedDetail}</p>
          {selectedAgent ? <div className="selected-agent-facts"><span><strong>Definition</strong>{selectedAgent.definition_name}</span><span><strong>Project</strong>{selectedAgent.project_name ?? "Unassigned"}</span><span><strong>Runtime status</strong>{selectedRuntime ? `${selectedRuntime.status} · ${selectedRuntime.freshness}` : "No event projection"}</span><span><strong>Office position</strong>Not available</span></div> : null}
          <div className="inspector-actions">
            <button type="button" className="button button-secondary" onClick={focusLocalAvatar}>Focus me</button>
            {selectedAgent ? <Link className="text-link" href={`/agents/${selectedAgent.id}`}>Open Agent Instance</Link> : null}
            {selected?.kind === "object" && selected.title.includes("board") ? <Link className="text-link" href="/tasks">Open persisted Tasks</Link> : null}
            {selected?.kind === "object" && selected.title.includes("desk") ? <Link className="text-link" href="/agents">Browse registered agents</Link> : null}
            {selectedAgent ? <DisabledWorkAction reason="The Engine/Runner is not connected.">Start or message agent</DisabledWorkAction> : null}
          </div>
        </section>

        <section className="inspector-card minimap-card" aria-labelledby="minimap-heading">
          <div className="inspector-heading"><div><p className="eyebrow">Wayfinding</p><h2 id="minimap-heading">Office map</h2></div><button type="button" className="text-button" onClick={resetCamera}>Reset</button></div>
          <div className="office-minimap" role="img" aria-label="Built-in office template map showing only the local owner avatar. Agent locations are unavailable.">
            <div className="map-room map-research"><span>Research</span></div><div className="map-room map-build"><span>Build</span></div><div className="map-room map-review"><span>Review</span></div><div className="map-room map-lounge"><span>Lounge</span></div>
            <span className="map-player" style={{ left: `${((playerPosition[0] + 8) / 16) * 100}%`, top: `${((playerPosition[1] + 5) / 10) * 100}%` }} aria-label="Your local avatar position" />
          </div>
          <div className="map-legend"><span><i className="map-player-key" />You</span></div>
          <p className="field-note">Agent locations are not available from persisted Agent Instance records.</p>
        </section>

        <section className="inspector-card rooms-card">
          <div className="inspector-heading"><div><p className="eyebrow">Explore</p><h2>Rooms</h2></div><span className="muted">Click to focus</span></div>
          <div className="room-list">
            {[{ name: "Research room", point: [-5, 0, -2.8] as [number, number, number] }, { name: "Build room", point: [4.8, 0, -2.5] as [number, number, number] }, { name: "Review room", point: [4.5, 0, 2.8] as [number, number, number] }, { name: "Shared studio", point: [-4.6, 0, 2.6] as [number, number, number] }].map((room) => <button className="room-link" type="button" key={room.name} onClick={() => { setCameraMode("overview"); setFocusTarget(room.point); setSelected({ kind: "object", title: room.name, detail: "Room label and furniture are local scene props, not a connected project or team room." }); }}><span>{room.name}</span><span aria-hidden="true">↗</span></button>)}
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
          <div className="office-agent-list">
            {visibleAgents.map((agent) => {
              const runtime = runtimeByAgent.get(agent.id);
              const runtimeLabel = runtime
                ? `${runtime.status} · ${runtime.freshness}`
                : "Unavailable";
              return <button className={selectedAgent?.id === agent.id ? "office-agent-link is-selected" : "office-agent-link"} key={agent.id} type="button" aria-pressed={selectedAgent?.id === agent.id} onClick={() => selectAgent(agent)}><span className="agent-avatar" aria-hidden="true">{agent.display_name.slice(0, 1)}</span><span className="office-agent-label"><strong>{agent.display_name}</strong><small>{agent.definition_name}</small><small>{agent.project_name ?? "No project assigned"}</small></span><span className="office-agent-presence">{runtimeLabel}</span></button>;
            })}
          </div>
          <p className="field-note" role={activityLoadError ? "alert" : "status"}>
            {activityLoadError
              ? `Runtime projections unavailable: ${activityLoadError}`
              : `Browser event feed: ${activityConnection}. Connection state does not indicate agent liveness. 3D agent positions are not reported by the event contract.`}
          </p>
        </section>
        <section className="inspector-card rooms-card">
          <div className="inspector-heading"><div><p className="eyebrow">Inspect</p><h2>Objects</h2></div><span className="muted">Local props</span></div>
          <div className="room-list">
            {officeObjects.map((object) => <button className="room-link" type="button" key={object.title} onClick={() => selectObject(object.title, object.detail)}><span>{object.title}</span><span aria-hidden="true">↗</span></button>)}
          </div>
        </section>
        <div className="office-disabled-action"><DisabledWorkAction reason="The Engine/Runner is not connected.">Start agent</DisabledWorkAction><p>Starting work remains unavailable.</p></div>
      </aside>
    </section>
  );
}
