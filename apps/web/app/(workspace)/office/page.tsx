import { OfficeExperience } from "../../components/office-experience";
import { PageHeader } from "../../components/workspace-components";

export default function OfficePage() {
  return (
    <div className="page-stack office-page">
      <PageHeader eyebrow="Personal workspace" title="Office" description="Inspect persisted Agent Instances and their event-backed runtime projections. 3D agent positions remain unavailable because the event contract carries no location." />
      <OfficeExperience />
    </div>
  );
}
