"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { Component, useCallback, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from "react";
import type { CSSProperties } from "react";
import { sampleAgents, type SampleAgent } from "../lib/sample-data";
import { DisabledWorkAction, StatusPill } from "./workspace-components";

const OfficeScene = dynamic(() => import("./office-scene").then((module) => module.OfficeScene), {
  ssr: false,
  loading: () => <div className="scene-loading" role="status">Preparing local 3D office…</div>,
});

type CameraMode = "follow" | "overview";
type SceneSelection = { kind: "object" | "agent"; title: string; detail: string; agentId?: string };
const officeObjects = [
  { title: "Research desk", point: [-6.4, 0.8, -2.8] as [number, number, number], detail: "A sample desk prop; no agent assignment or access scope is connected." },
  { title: "Build desk", point: [6.2, 0.8, -2.7] as [number, number, number], detail: "A sample desk prop; no agent assignment or access scope is connected." },
  { title: "Review desk", point: [6, 0.8, 2.8] as [number, number, number], detail: "A sample desk prop; no agent assignment or access scope is connected." },
  { title: "Meeting table", point: [0, 0.8, 0] as [number, number, number], detail: "A local meeting table. No real task or team is attached." },
  { title: "Lounge table", point: [0, 0.6, 5] as [number, number, number], detail: "A local furniture prop; no task, team, or approval is attached." },
  { title: "Atlas project board", point: [0, 1.2, -5.8] as [number, number, number], detail: "A local project-board prop. Opening it shows the sample Task Board only; no work is sent." },
  { title: "Office entrance", point: [0, 0, 6] as [number, number, number], detail: "A visual doorway marker; workspace permissions are not determined by room or proximity." },
];
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

function OfficeFallback({ agents, onSelectAgent }: { agents: SampleAgent[]; onSelectAgent: (agent: SampleAgent) => void }) {
  return (
    <div className="office-fallback">
      <div><p className="eyebrow">Accessible office view</p><h2>3D scene unavailable</h2><p>WebGL could not start in this browser. The filtered sample agents and rooms remain available as a keyboard-friendly list.</p></div>
      <div className="fallback-agent-list">
        {agents.map((agent) => <button className="fallback-agent" key={agent.id} type="button" onClick={() => onSelectAgent(agent)}><span className="agent-avatar" style={{ "--avatar-color": agent.color } as CSSProperties} aria-hidden="true">{agent.name.slice(0, 1)}</span><span><strong>{agent.name} · {agent.role}</strong><small>{agent.room} · Sample agent</small></span><span aria-hidden="true">→</span></button>)}
      </div>
    </div>
  );
}

export function OfficeExperience() {
  const [cameraMode, setCameraMode] = useState<CameraMode>("overview");
  const [zoom, setZoom] = useState(15);
  const [focusTarget, setFocusTarget] = useState<[number, number, number]>([0, 0, 0]);
  const [selected, setSelected] = useState<SceneSelection | null>(null);
  const [playerPosition, setPlayerPosition] = useState<[number, number]>([-2, 4]);
  const [movementMessage, setMovementMessage] = useState("Use WASD or arrow keys, or click a clear floor tile to move your local avatar.");
  const [sceneFailed, setSceneFailed] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [agentSearch, setAgentSearch] = useState("");
  const [projectFilter, setProjectFilter] = useState("All projects");
  const [roleFilter, setRoleFilter] = useState("All roles");
  const [presenceFilter, setPresenceFilter] = useState("All presence");
  const selectedAgent = selected?.kind === "agent"
    ? sampleAgents.find((agent) => agent.id === selected.agentId)
    : undefined;

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);

  const handlePlayerPosition = useCallback((x: number, z: number) => setPlayerPosition([x, z]), []);
  const selectAgent = useCallback((agent: SampleAgent) => {
    setSelected({ kind: "agent", title: agent.name, detail: `${agent.role} · ${agent.room} · Sample agent record`, agentId: agent.id });
    setFocusTarget([agent.position[0], 0, agent.position[1]]);
    setCameraMode("overview");
  }, []);
  const selectObject = useCallback((title: string, detail: string) => {
    setSelected({ kind: "object", title, detail });
    const object = officeObjects.find((item) => item.title === title);
    if (object) {
      setCameraMode("overview");
      setFocusTarget(object.point);
    }
  }, []);
  const visibleAgents = useMemo(() => sampleAgents.filter((agent) => {
    const matchesSearch = `${agent.name} ${agent.role} ${agent.project} ${agent.room}`.toLowerCase().includes(agentSearch.trim().toLowerCase());
    return matchesSearch
      && (projectFilter === "All projects" || agent.project === projectFilter)
      && (roleFilter === "All roles" || agent.role === roleFilter)
      && (presenceFilter === "All presence" || agent.presence === presenceFilter);
  }), [agentSearch, presenceFilter, projectFilter, roleFilter]);

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
    <section className="office-experience" aria-label="Interactive sample office">
      <div className="office-scene-column">
        <div className="scene-toolbar" aria-label="Office camera controls">
          <div className="camera-switch" role="group" aria-label="Camera mode">
            <button type="button" className={cameraMode === "follow" ? "camera-button is-active" : "camera-button"} onClick={() => setCameraMode("follow")}>Follow</button>
            <button type="button" className={cameraMode === "overview" ? "camera-button is-active" : "camera-button"} onClick={() => setCameraMode("overview")}>Overview</button>
          </div>
          <div className="camera-actions">
            <button type="button" className="quiet-button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.max(9, value - 1))}>Zoom +</button>
            <button type="button" className="quiet-button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.min(23, value + 1))}>Zoom −</button>
            <button type="button" className="quiet-button" onClick={resetCamera}>Reset view</button>
          </div>
        </div>
        <div className="office-canvas-wrap">
          {sceneFailed ? <OfficeFallback agents={visibleAgents} onSelectAgent={selectAgent} /> : (
            <SceneErrorBoundary onError={() => setSceneFailed(true)}>
              <OfficeScene
                cameraMode={cameraMode}
                zoom={zoom}
                focusTarget={focusTarget}
                reducedMotion={reducedMotion}
                onSelectAgent={selectAgent}
                visibleAgentIds={visibleAgents.map((agent) => agent.id)}
                onSelectObject={selectObject}
                onPlayerPosition={handlePlayerPosition}
                onMovementMessage={setMovementMessage}
              />
            </SceneErrorBoundary>
          )}
          {!sceneFailed ? <div className="scene-view-label" aria-live="polite">{cameraMode === "follow" ? "Follow view" : "Overview · drag to pan or rotate"}</div> : null}
        </div>
        <div className="scene-bottom-row">
          <p className="movement-help" role="status">{movementMessage}</p>
          <span className="scene-motion-label">{reducedMotion ? "Reduced motion" : "Local movement"}</span>
        </div>
      </div>

      <aside className="office-inspector" aria-label="Office information and navigation">
        <section className="inspector-card focus-card">
          <div className="inspector-heading"><div><p className="eyebrow">Selected object</p><h2>{selected?.title ?? "Your local avatar"}</h2></div><StatusPill>Sample</StatusPill></div>
          <p>{selected?.detail ?? "This avatar belongs to the signed-in owner and is distinct from sample agent avatars."}</p>
          {selectedAgent ? <div className="selected-agent-facts"><span><strong>Role</strong>{selectedAgent.role}</span><span><strong>Room</strong>{selectedAgent.room}</span><span><strong>Sample presence</strong>{selectedAgent.presence}</span></div> : null}
          <div className="inspector-actions">
            <button type="button" className="button button-secondary" onClick={focusLocalAvatar}>Focus me</button>
            {selectedAgent?.runId ? <Link className="text-link" href={`/runs/${selectedAgent.runId}`}>Open sample run</Link> : null}
            {selected?.kind === "object" && selected.title.includes("board") ? <Link className="text-link" href="/tasks">Open sample task board</Link> : null}
            {selected?.kind === "object" && selected.title.includes("desk") ? <Link className="text-link" href="/agents">Browse sample agents</Link> : null}
            {selectedAgent ? <><DisabledWorkAction>Assign task</DisabledWorkAction><DisabledWorkAction>Send input</DisabledWorkAction></> : null}
          </div>
        </section>

        <section className="inspector-card minimap-card" aria-labelledby="minimap-heading">
          <div className="inspector-heading"><div><p className="eyebrow">Wayfinding</p><h2 id="minimap-heading">Office map</h2></div><button type="button" className="text-button" onClick={resetCamera}>Reset</button></div>
          <div className="office-minimap" role="img" aria-label="Map with four rooms and local avatar position">
            <div className="map-room map-research"><span>Research</span></div><div className="map-room map-build"><span>Build</span></div><div className="map-room map-review"><span>Review</span></div><div className="map-room map-lounge"><span>Lounge</span></div>
            {visibleAgents.map((agent) => <span className="map-agent" key={agent.id} style={{ left: `${((agent.position[0] + 8) / 16) * 100}%`, top: `${((agent.position[1] + 5) / 10) * 100}%`, "--avatar-color": agent.color } as CSSProperties} aria-hidden="true" />)}
            <span className="map-player" style={{ left: `${((playerPosition[0] + 8) / 16) * 100}%`, top: `${((playerPosition[1] + 5) / 10) * 100}%` }} aria-label="Your local avatar position" />
          </div>
          <div className="map-legend"><span><i className="map-player-key" />You</span><span><i className="map-agent-key" />Sample agents</span></div>
        </section>

        <section className="inspector-card rooms-card">
          <div className="inspector-heading"><div><p className="eyebrow">Explore</p><h2>Rooms</h2></div><span className="muted">Click to focus</span></div>
          <div className="room-list">
            {[{ name: "Research room", point: [-5, 0, -2.8] as [number, number, number] }, { name: "Build room", point: [4.8, 0, -2.5] as [number, number, number] }, { name: "Review room", point: [4.5, 0, 2.8] as [number, number, number] }, { name: "Shared studio", point: [-4.6, 0, 2.6] as [number, number, number] }].map((room) => <button className="room-link" type="button" key={room.name} onClick={() => { setCameraMode("overview"); setFocusTarget(room.point); setSelected({ kind: "object", title: room.name, detail: "Room label and furniture are local scene props, not a connected project or team room." }); }}><span>{room.name}</span><span aria-hidden="true">↗</span></button>)}
          </div>
        </section>
        <section className="inspector-card office-agents-card" aria-labelledby="office-agents-heading">
          <div className="inspector-heading"><div><p className="eyebrow">Sample records</p><h2 id="office-agents-heading">Agents in office</h2></div><span className="muted">Focus</span></div>
          <div className="office-agent-filters">
            <label className="preview-field">Search<input type="search" value={agentSearch} onChange={(event) => setAgentSearch(event.target.value)} placeholder="Agent, role, or project" /></label>
            <label className="preview-field">Project<select value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}><option>All projects</option>{[...new Set(sampleAgents.map((agent) => agent.project))].map((project) => <option key={project}>{project}</option>)}</select></label>
            <label className="preview-field">Role<select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}><option>All roles</option>{[...new Set(sampleAgents.map((agent) => agent.role))].map((role) => <option key={role}>{role}</option>)}</select></label>
            <label className="preview-field">Presence<select value={presenceFilter} onChange={(event) => setPresenceFilter(event.target.value)}><option>All presence</option>{[...new Set(sampleAgents.map((agent) => agent.presence))].map((presence) => <option key={presence}>{presence}</option>)}</select></label>
            <span className="field-note" role="status">{visibleAgents.length} of {sampleAgents.length} sample agents · filters do not change work state</span>
          </div>
          <div className="office-agent-list">
            {visibleAgents.map((agent) => <button className={selectedAgent?.id === agent.id ? "office-agent-link is-selected" : "office-agent-link"} key={agent.id} type="button" aria-pressed={selectedAgent?.id === agent.id} onClick={() => selectAgent(agent)}><span className="agent-avatar" style={{ "--avatar-color": agent.color } as CSSProperties} aria-hidden="true">{agent.name.slice(0, 1)}</span><span className="office-agent-label"><strong>{agent.name}</strong><small>{agent.role}</small><small>{agent.room}</small></span><span className="office-agent-presence">{agent.presence}</span></button>)}
            {!visibleAgents.length ? <p className="empty-state">No sample agents match these filters.</p> : null}
          </div>
        </section>
        <section className="inspector-card rooms-card">
          <div className="inspector-heading"><div><p className="eyebrow">Inspect</p><h2>Objects</h2></div><span className="muted">Local props</span></div>
          <div className="room-list">
            {officeObjects.map((object) => <button className="room-link" type="button" key={object.title} onClick={() => selectObject(object.title, object.detail)}><span>{object.title}</span><span aria-hidden="true">↗</span></button>)}
          </div>
        </section>
        <div className="office-disabled-action"><DisabledWorkAction>Start agent</DisabledWorkAction><p>Work actions are disabled for sample records.</p></div>
      </aside>
    </section>
  );
}
