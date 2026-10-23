import type { CSSProperties } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader, SampleNotice, StatusPill } from "../../../components/workspace-components";
import { getSampleAgent, getSampleRun, getSampleTask } from "../../../lib/sample-data";
import { RunEvidence } from "../../../components/run-evidence";
import { sampleDefinitions } from "../../../lib/ui-preview-data";

export default async function RunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const run = getSampleRun(runId);
  if (!run) notFound();
  const task = getSampleTask(run.taskId);
  const agent = getSampleAgent(run.agentId);
  if (!task || !agent) notFound();
  const definition = sampleDefinitions.find((item) => item.name === agent.role);

  return (
    <div className="page-stack">
      <PageHeader eyebrow="Run / attempt" title="Run detail" description="Inspect one sample attempt and its presentation timeline. This is not evidence of a real runtime session." />
      <SampleNotice />
      <div className="run-layout">
        <section className="content-section run-main" aria-labelledby="run-overview-heading">
          <div className="run-title-row"><div><p className="eyebrow">Sample attempt · {run.id}</p><h2 id="run-overview-heading">{task.title}</h2></div><StatusPill tone={run.status === "Succeeded" ? "green" : run.status === "Running" ? "amber" : "neutral"}>{run.status}</StatusPill></div>
          <p className="run-description">The timeline and runtime label below are deterministic local sample records. No provider or execution adapter was called.</p>
          <div className="run-facts"><div><span>Agent instance</span><strong>{agent.name} · {agent.role}</strong></div><div><span>Task</span><strong>{task.title}</strong></div><div><span>Started</span><strong>{run.started}</strong></div><div><span>Runtime session</span><strong>{run.runtime}</strong></div></div>
          <RunEvidence run={run} />
          <section className="run-evidence-section"><div className="section-heading"><h3>Configuration snapshot</h3><StatusPill>Sample · not persisted</StatusPill></div><div className="settings-row"><span>Agent definition</span><strong>{definition?.name ?? "Not available"} · version unavailable</strong></div><div className="settings-row"><span>Skills and effective instructions</span><strong>Not available</strong></div><div className="settings-row"><span>Provider adapter / model route</span><strong>Not available</strong></div><div className="settings-row"><span>Permissions</span><strong>Not verified</strong></div></section>
          <section className="run-evidence-section"><div className="section-heading"><h3>Execution and acceptance</h3><StatusPill tone="amber">Evidence unavailable</StatusPill></div><div className="settings-row"><span>Runtime connection health</span><strong>Not connected</strong></div><div className="settings-row"><span>Last heartbeat / activity freshness</span><strong>Not available</strong></div><div className="settings-row"><span>Task review state</span><strong>{task.stage} · separate from run status</strong></div><div className="settings-row"><span>Test commands and exit status</span><strong>Not available</strong></div><div className="settings-row"><span>Parent / child run relation</span><strong>No verified delegation relation</strong></div><div className="settings-row"><span>Tool-call details</span><strong>Not available</strong></div></section>
          <div className="run-actions"><button className="button button-secondary" type="button" disabled title="Tersedia setelah runtime nyata terhubung.">Cancel run</button><span className="muted">Work actions are disabled in sample mode.</span></div>
        </section>
        <aside className="run-side-panel">
          <section className="content-section"><h2>Task context</h2><StatusPill>Task stage · {task.stage}</StatusPill><p>{task.project}</p><Link className="text-link" href="/tasks">Open task board</Link></section>
          <section className="content-section"><h2>Agent context</h2><div className="mini-agent"><span className="agent-avatar" style={{ "--avatar-color": agent.color } as CSSProperties} aria-hidden="true">{agent.name.slice(0, 1)}</span><div><strong>{agent.name}</strong><span>{agent.role}</span></div></div><p>{agent.summary}</p><Link className="text-link" href="/agents">Open agent directory</Link></section>
        </aside>
      </div>
    </div>
  );
}
