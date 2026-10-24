import Link from "next/link";
import { AgentInstancesView } from "../../components/agent-instances-view";
import { PageHeader } from "../../components/workspace-components";

export default function AgentsPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Workspace directory" title="Agents" description="Browse persistent instance identities linked to reusable definitions and optional project context. Runtime sessions and live work state remain separate.">
        <Link className="button button-secondary" href="/agents/definitions">Definition library</Link>
        <Link className="button button-primary" href="/agents/new">Add instance</Link>
      </PageHeader>
      <section className="content-section" aria-labelledby="agent-directory-heading">
        <div className="section-heading"><h2 id="agent-directory-heading">Agent directory</h2><Link className="text-link" href="/agents/definitions">Manage definitions</Link></div>
        <AgentInstancesView />
      </section>
    </div>
  );
}
