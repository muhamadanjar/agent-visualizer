"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

const OfficeCanvas = dynamic(() => import("./office-canvas"), {
  ssr: false,
  loading: () => <div className="scene-loading">Loading the local 3D scene…</div>,
});

export default function Home() {
  const [cameraMode, setCameraMode] = useState<"follow" | "overview">("follow");
  const [status, setStatus] = useState("Ready. Walk with WASD/arrow keys or click a walkable floor tile.");
  const [fps, setFps] = useState(0);
  const [motionMs, setMotionMs] = useState<number | null>(null);
  const [playerCell, setPlayerCell] = useState("—");
  const [loadTest, setLoadTest] = useState(false);
  const [syntheticEvents, setSyntheticEvents] = useState(0);
  const [renderScale, setRenderScale] = useState<number | null>(null);
  const [drawCalls, setDrawCalls] = useState<number | null>(null);

  useEffect(() => {
    if (!loadTest) return;
    const timer = window.setInterval(() => setSyntheticEvents((count) => count + 1), 100);
    return () => window.clearInterval(timer);
  }, [loadTest]);

  return (
    <main className="page-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">M0 FEASIBILITY SPIKE · NO AGENT RUNTIME</p>
          <h1>Explore the test office</h1>
        </div>
        <button
          className="mode-button"
          type="button"
          onClick={() => setCameraMode((mode) => (mode === "follow" ? "overview" : "follow"))}
          aria-label={`Switch camera to ${cameraMode === "follow" ? "overview" : "follow"} mode`}
        >
          {cameraMode === "follow" ? "Overview camera" : "Follow camera"}
        </button>
        <button
          className="mode-button"
          type="button"
          onClick={() => setLoadTest((enabled) => !enabled)}
          aria-pressed={loadTest}
        >
          {loadTest ? "Stop synthetic load" : "Run synthetic 50 / 10 load"}
        </button>
      </header>

      <section className="scene-frame" aria-label="Interactive three-dimensional office feasibility scene">
        <OfficeCanvas
          cameraMode={cameraMode}
          loadTest={loadTest}
          onStatus={setStatus}
          onFps={setFps}
          onMotionMs={setMotionMs}
          onCell={setPlayerCell}
          onScale={setRenderScale}
          onCalls={setDrawCalls}
        />
        <div className="scene-badge" aria-hidden="true">LOCAL SCENE · NO WORK STATE</div>
      </section>
      {loadTest && (
        <p className="load-test-label">
          Synthetic only: 50 avatar instances · 10 run streams · {syntheticEvents} event updates ({syntheticEvents % 10 + 1}/10 last updated)
        </p>
      )}

      <section className="readout" aria-live="polite">
        <div className="status-copy">
          <span className="readout-label">Scene status</span>
          <p>{status}</p>
        </div>
        <div className="metrics" aria-label="Diagnostic measurements">
          <div><span className="readout-label">FPS sample</span><strong>{fps || "—"}</strong></div>
          <div><span className="readout-label">Key to motion frame</span><strong>{motionMs === null ? "—" : `${motionMs} ms`}</strong></div>
          <div><span className="readout-label">Grid cell</span><strong>{playerCell}</strong></div>
          <div><span className="readout-label">Render scale (auto)</span><strong>{renderScale === null ? "—" : `${renderScale.toFixed(2)}×`}</strong></div>
          <div><span className="readout-label">Draw calls</span><strong>{drawCalls ?? "—"}</strong></div>
        </div>
      </section>

      <footer className="controls-hint">
        <span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> / arrows · move</span>
        <span>Click floor · route</span>
        <span><kbd>E</kbd> near console · interact</span>
        <span>Blocked cells and furniture stop the route</span>
      </footer>
    </main>
  );
}
