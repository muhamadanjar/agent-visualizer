import { RunList } from "../../components/run-list";
import { PageHeader } from "../../components/workspace-components";

export default function RunsPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Run / Attempt records" title="Runs" description="Inspect immutable creation records associated with persisted Tasks. Created records do not imply dispatch, execution, or a runtime session." />
      <RunList />
    </div>
  );
}
