"use client";

import { useMemo, useState } from "react";
import { sampleArtifacts } from "../lib/ui-preview-data";
import type { SampleRun } from "../lib/sample-data";
import { StatusPill } from "./workspace-components";

export function RunEvidence({ run }: { run: SampleRun }) {
  const [eventKind, setEventKind] = useState("All event types");
  const events = useMemo(() => run.events.filter((event) => eventKind === "All event types" || event.type === eventKind), [eventKind, run.events]);
  const artifacts = sampleArtifacts.filter((artifact) => artifact.runId === run.id);
  return <><div className="timeline-heading"><h3>Sample event timeline</h3><label className="filter-field"><span className="visually-hidden">Filter sample event type</span><select value={eventKind} onChange={(event) => setEventKind(event.target.value)}><option>All event types</option>{[...new Set(run.events.map((event) => event.type))].map((type) => <option key={type}>{type}</option>)}</select></label></div><ol className="event-timeline">{events.length ? events.map((event, index) => <li key={`${event.time}-${event.type}`}><span className="timeline-marker" aria-hidden="true">{index + 1}</span><div><div className="event-meta"><strong>{event.type}</strong><time>{event.time}</time></div><p>{event.detail}</p><span className="sample-card-label">Sample event · not runtime evidence</span></div></li>) : <li className="empty-state">No sample events match.</li>}</ol><section className="run-evidence-section"><div className="section-heading"><h3>Artifacts</h3><StatusPill>Sample only</StatusPill></div>{artifacts.length ? <div className="artifact-list">{artifacts.map((artifact) => <article className="artifact-row" key={artifact.id}><div><strong>{artifact.name}</strong><span>{artifact.provenance} · {artifact.created}</span></div><span className="muted">Checksum: {artifact.checksum}</span></article>)}</div> : <p className="muted">No sample artifact attached.</p>}<p className="field-note">These filenames do not point to stored files and cannot be downloaded.</p></section></>;
}
