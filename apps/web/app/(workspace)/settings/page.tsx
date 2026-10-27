import Link from "next/link";
import { PageHeader, SampleNotice, StatusPill } from "../../components/workspace-components";

export default function SettingsPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Workspace configuration" title="Settings" description="Provider endpoint selection is configurable. Runtime, policy, and product preferences remain unavailable or read-only." />
      <SampleNotice />
      <div className="settings-grid">
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Identity and scope</p><h2>Private workspace</h2></div><StatusPill>Owner session</StatusPill></div>
          <p>Owner authentication is checked through the session API. There is one owner; project-resource access is enforced server-side and is not represented by visual workspace membership.</p>
          <div className="settings-row"><span>Workspace mode</span><strong>Private pilot</strong></div>
          <div className="settings-row"><span>Persisted records</span><strong>Projects, definitions, Skills, instances, Tasks, Run creation</strong></div>
          <div className="settings-row"><span>Events and runtime activity</span><strong>Not persisted; previews are labeled Sample</strong></div>
          <Link className="text-link" href="/projects">Review project boundaries</Link>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Execution policy</p><h2>Runtime and approvals</h2></div><StatusPill tone="amber">Not enforced</StatusPill></div>
          <p>Execution and per-call approval require a verified runtime invocation identity. Unsupported or uncertain actions remain unavailable.</p>
          <div className="settings-row"><span>Runtime mode</span><strong>Not connected</strong></div>
          <div className="settings-row"><span>Approval scope</span><strong>Fail-closed</strong></div>
          <Link className="text-link" href="/connections">Review provider and runtime connections</Link>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Capacity and cost</p><h2>Usage limits</h2></div><StatusPill tone="amber">No live measurements</StatusPill></div>
          <p>Concurrency, duration, delegation depth, tokens, and cost require runner or provider evidence. Missing data is not shown as zero.</p>
          <Link className="text-link" href="/usage">View usage and limits</Link>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Data governance</p><h2>Audit and retention</h2></div><StatusPill>Proposal only</StatusPill></div>
          <p>Retention values are proposed defaults, not configured policies. Persisted audit events are not connected.</p>
          <Link className="text-link" href="/audit">View audit and retention</Link>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Presentation</p><h2>Office scene</h2></div><StatusPill>Local controls</StatusPill></div>
          <p>Camera and avatar movement are local presentation state. The scene does not grant access or alter task state.</p>
          <div className="settings-row"><span>Reduced motion</span><strong>Follows system preference</strong></div>
          <div className="settings-row"><span>Scene recovery</span><strong>Sample list fallback available</strong></div>
          <Link className="text-link" href="/office">Return to office</Link>
        </section>
        <section className="content-section settings-card">
          <div className="settings-card-heading"><div><p className="eyebrow">Provider credentials</p><h2>Secret handling</h2></div><StatusPill tone="amber">Runner not connected</StatusPill></div>
          <p>Provider endpoint settings store only adapter, model, and route. No key is accepted or returned; Runner-side credential injection and egress controls are not implemented.</p>
          <Link className="text-link" href="/connections">Open provider settings</Link>
        </section>
      </div>
    </div>
  );
}
