import Link from "next/link";
import { AgentDirectory } from "../../components/agent-directory";
import { DisabledWorkAction, PageHeader, SampleNotice, SectionHeading } from "../../components/workspace-components";

export default function AgentsPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Workspace directory" title="Agents" description="Browse sample agent instances. An instance, its reusable definition, and its runtime session are separate concepts."><Link className="button button-secondary" href="/agents/definitions">Definition library</Link><DisabledWorkAction>Add instance</DisabledWorkAction></PageHeader>
      <SampleNotice />
      <section className="content-section" aria-labelledby="agent-directory-heading">
        <SectionHeading title="Agent directory" count={4} />
        <AgentDirectory />
      </section>
    </div>
  );
}
