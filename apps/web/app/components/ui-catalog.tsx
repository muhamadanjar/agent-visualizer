import Link from "next/link";
import {
  sampleArtifacts,
  sampleAuditEntries,
} from "../lib/ui-preview-data";
import { ProviderConnectionForm } from "./provider-connection-form";
import {
  DisabledWorkAction,
  PageHeader,
  SampleNotice,
  StatusPill,
} from "./workspace-components";

export function ConnectionsView() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Connections" title="Providers & runtimes" description="Provider selection is saved as non-secret configuration. Runtime execution remains unavailable." />
      <div className="settings-grid">
        <ProviderConnectionForm />
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Execution</p><h2>Runtime connection</h2></div><StatusPill tone="amber">Not connected</StatusPill></div>
          <p>The Runtime Adapter starts and observes agent work. It is separate from the model provider endpoint.</p>
          <div className="settings-row"><span>Runtime Adapter</span><strong>Not available</strong></div>
          <div className="settings-row"><span>Operating mode</span><strong>Unavailable until the Engine/Runner is implemented</strong></div>
          <div className="settings-row"><span>Managed capability</span><strong>Not approved</strong></div>
          <div className="settings-row"><span>Tool-call approval</span><strong>Fail-closed · runtime identity unavailable</strong></div>
          <div className="settings-row"><span>Credentials / egress</span><strong>Not configured</strong></div>
          <DisabledWorkAction reason="The Engine/Runner is not implemented; saving a provider endpoint does not enable execution.">Connect runtime</DisabledWorkAction>
        </section>
      </div>
      <section className="content-section">
        <div className="section-heading"><h2>Independent contracts</h2><StatusPill>Separate</StatusPill></div>
        <p className="muted">A configured model endpoint records a selection only. It does not prove provider availability, support a tool call, enable task execution, or authorize outbound network access.</p>
        <Link className="text-link" href="/usage">View usage and limits</Link>
      </section>
    </div>
  );
}

export function UsageView() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Capacity & budget" title="Usage and limits" description="Configured targets are not measurements. Runtime, token, cost, and live capacity data are unavailable in this sample preview." />
      <SampleNotice />
      <div className="metric-grid">
        <article className="content-section metric-card"><p className="eyebrow">Registered instances</p><strong>Not available</strong><span>PRD pilot target: up to 50</span></article>
        <article className="content-section metric-card"><p className="eyebrow">Active executions</p><strong>Not available</strong><span>PRD pilot target: up to 10, including child runs</span></article>
        <article className="content-section metric-card"><p className="eyebrow">Token usage</p><strong>Not available</strong><span>No provider/runtime telemetry connected</span></article>
        <article className="content-section metric-card"><p className="eyebrow">Cost</p><strong>Not available</strong><span>Not zero; no billable usage evidence</span></article>
      </div>
      <section className="content-section">
        <div className="section-heading"><h2>Execution limits</h2><StatusPill tone="amber">Not enforced here</StatusPill></div>
        <div className="settings-row"><span>Concurrency, duration, child count, delegation depth</span><strong>Not configured</strong></div>
        <div className="settings-row"><span>Hard spend limit</span><strong>Not verified by a provider/router</strong></div>
        <p className="muted">These controls require enforcement before dispatch. This page does not estimate usage, show zero as a substitute for missing telemetry, or claim a hard spend cap.</p>
        <DisabledWorkAction reason="Limits can only be described as enforced after runner and provider integration.">Configure limits</DisabledWorkAction>
      </section>
    </div>
  );
}

export function AuditView() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="History & retention" title="Audit and retention" description="A sample-only view of traceable changes and retention settings. These entries are not an audit log." />
      <SampleNotice />
      <section className="content-section">
        <div className="section-heading"><h2>Sample activity</h2><StatusPill>Local fixtures</StatusPill></div>
        <div className="responsive-table-wrap"><table className="preview-table"><caption>Illustrative history only; no persisted audit events are connected.</caption><thead><tr><th scope="col">When</th><th scope="col">Actor</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Source</th></tr></thead><tbody>{sampleAuditEntries.map((entry) => <tr key={`${entry.time}-${entry.target}`}><td>{entry.time}</td><td>{entry.actor}</td><td>{entry.action}</td><td>{entry.target}</td><td>{entry.source}</td></tr>)}</tbody></table></div>
      </section>
      <section className="content-section">
        <div className="section-heading"><h2>Retention</h2><StatusPill>Proposal</StatusPill></div>
        <div className="settings-row"><span>Detailed events and logs</span><strong>30 days · proposed default</strong></div>
        <div className="settings-row"><span>Definitions, summaries, approvals, artifacts</span><strong>90 days · proposed default</strong></div>
        <p className="muted">Deletion and export must exclude secret values. Retention is not configured or enforced by this preview.</p>
        <DisabledWorkAction reason="Retention and audit settings require persisted workspace policy.">Save retention policy</DisabledWorkAction>
      </section>
      <section className="content-section">
        <div className="section-heading"><h2>Sample artifacts</h2><StatusPill>Not runtime output</StatusPill></div>
        <div className="artifact-list">{sampleArtifacts.map((artifact) => <article className="artifact-row" key={artifact.id}><div><strong>{artifact.name}</strong><span>{artifact.provenance} · {artifact.created}</span></div><span className="muted">Checksum: {artifact.checksum}</span></article>)}</div>
      </section>
    </div>
  );
}

export function DelegationsView() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Handoffs" title="Delegations" description="A parent/child run relationship must be explicit and backed by runtime evidence; it is never inferred from agent names or movement." />
      <SampleNotice />
      <section className="content-section">
        <div className="section-heading"><h2>Verified delegation tree</h2><StatusPill tone="amber">No verified links</StatusPill></div>
        <div className="empty-state"><strong>No parent/child relationship is available.</strong><span>Sample agents and tasks are independent display records. No child run, parent input, or handoff result is being simulated.</span></div>
      </section>
      <div className="settings-grid">
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Delegation limits</p><h2>Concurrency and depth</h2></div><StatusPill tone="amber">Not configured</StatusPill></div>
          <div className="settings-row"><span>Proposed maximum active children</span><strong>3 · not enforced</strong></div>
          <div className="settings-row"><span>Proposed maximum depth</span><strong>2 edges · not enforced</strong></div>
          <div className="settings-row"><span>Project scope and budget</span><strong>Not available</strong></div>
          <p className="field-note">PRD proposals are shown as proposals only. Child permissions must be the intersection of parent, definition, and project scope.</p>
          <DisabledWorkAction reason="Delegation, task persistence, isolation, and runtime parentage are not connected.">Create child task</DisabledWorkAction>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Recovery</p><h2>Uncertain child runs</h2></div><StatusPill>Unknown</StatusPill></div>
          <p>No runner lease, event spool, replay cursor, or recovery state is available in this preview. An uncertain attempt must not be retried as if it had no side effects.</p>
          <Link className="text-link" href="/inbox">Open sample attention inbox</Link>
        </section>
      </div>
    </div>
  );
}

const readinessItems = [
  { area: "Office exploration and accessible alternatives", evidence: "Presentation route exists; complete AC-23–31 and failure scenarios are not verified.", href: "/office" },
  { area: "Agent definitions, imports, and skills", evidence: "Definitions, Skill versions/pins, and Agent Instances use owner-scoped APIs. Task assignment and workspace execution policy are API-backed; runtime/provider compatibility remains unqualified.", href: "/agents/definitions" },
  { area: "Tasks, assignment, and runtime controls", evidence: "Workspace policy, fixed file-tool allowlist, bounded Run dispatch, and Runner readiness checks are implemented. End-to-end configured-provider execution is not verified.", href: "/tasks" },
  { area: "Run evidence, artifacts, and acceptance", evidence: "Run snapshots, authenticated lifecycle events, acknowledgements, and fixed action summaries have component evidence; final-answer/artifact persistence and live 3D acceptance remain open.", href: "/runs" },
  { area: "Providers, runtime safety, and approvals", evidence: "Runner uses a bounded OpenAI-compatible tool loop; live configured-provider execution and OS-enforced provider-only egress are unverified. Destructive tools are not exposed.", href: "/connections" },
  { area: "Delegation, isolation, and recovery", evidence: "Runs sharing a configured workspace path are serialized and event outbox restart is unit-tested. Per-Run worktrees, cancel acknowledgement, and full service restart recovery remain open.", href: "/delegations" },
  { area: "Private pilot durability and audit", evidence: "This UI does not verify API scope, persisted events, or audit retention.", href: "/audit" },
  { area: "Capacity, costs, and target profile", evidence: "Live measurements are unavailable; proposed targets are not acceptance results.", href: "/usage" },
] as const;

export function ReadinessView() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="MVP acceptance" title="Readiness" description="This checklist reports open evidence gates. A polished UI or sample record is not proof of operational acceptance." />
      <SampleNotice />
      <section className="content-section">
        <div className="section-heading"><h2>P0 evidence gates</h2><StatusPill tone="amber">Open</StatusPill></div>
        <div className="readiness-list">{readinessItems.map((item) => <article className="readiness-row" key={item.area}><div><h3>{item.area}</h3><p>{item.evidence}</p></div><div className="readiness-actions"><StatusPill tone="amber">Not verified</StatusPill><Link className="text-link" href={item.href}>Inspect UI</Link></div></article>)}</div>
      </section>
      <section className="content-section"><div className="section-heading"><h2>Release decision</h2><StatusPill tone="amber">Not ready to pilot</StatusPill></div><p className="muted">Phase 1/M0 remains open, Phase 3 is in progress, and Phase 4 event ingestion/reconciliation is not implemented. Phase 8 requires evidence from a real task, runtime, artifact, approval/failure path, recovery scenario, and applicable NFR checks; none is established by this UI preview.</p><Link className="text-link" href="/settings">Review workspace configuration</Link></section>
    </div>
  );
}
