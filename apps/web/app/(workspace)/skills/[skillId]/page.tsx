import { SkillDetailView } from "../../../components/skills-view";

type SkillPageProps = { params: Promise<{ skillId: string }> };

export default async function SkillPage({ params }: SkillPageProps) {
  const { skillId } = await params;
  return <SkillDetailView skillId={skillId} />;
}
