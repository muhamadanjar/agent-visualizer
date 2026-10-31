import { OfficeExperience } from "../../components/office-experience";
import { PageHeader } from "../../components/workspace-components";

export default function OfficePage() {
  return (
    <div className="page-stack office-page">
      <PageHeader eyebrow="Personal workspace" title="Office" description="See persisted Agent Instances in their workstations or rest room based on verified availability. Idle movement is decorative, not evidence of activity." />
      <OfficeExperience />
    </div>
  );
}
