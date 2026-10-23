import { notFound } from "next/navigation";
import { sampleSkills } from "../../../lib/ui-preview-data";
import { SkillDetailPreview } from "../../../components/catalog-detail-previews";

type SkillPageProps = { params: Promise<{ skillId: string }> };

export default async function SkillPage({ params }: SkillPageProps) {
  const { skillId } = await params;
  const skill = sampleSkills.find((item) => item.id === skillId);
  if (!skill) notFound();
  return <SkillDetailPreview skill={skill} />;
}
