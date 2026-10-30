import { OfficeExperience } from "../../components/office-experience";
import { PageHeader } from "../../components/workspace-components";

export default function OfficePage() {
  return (
    <div className="page-stack office-page">
      <PageHeader eyebrow="Personal workspace" title="Office" description="See persisted Agent Instances move between individual workstations and a separate rest room based on verified Run availability. No demo agents or inferred activity." />
      <OfficeExperience />
    </div>
  );
}
