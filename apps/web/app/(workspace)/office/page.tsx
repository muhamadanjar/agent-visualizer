import { OfficeExperience } from "../../components/office-experience";
import { PageHeader, SampleNotice } from "../../components/workspace-components";

export default function OfficePage() {
  return (
    <div className="page-stack office-page">
      <PageHeader eyebrow="Personal workspace" title="Office" description="Explore the sample office, inspect its presentation records, and move your local avatar." />
      <SampleNotice />
      <OfficeExperience />
    </div>
  );
}
