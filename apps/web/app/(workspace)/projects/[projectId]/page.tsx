import { ProjectDetailView } from "../../../components/projects-view";

type ProjectPageProps = { params: Promise<{ projectId: string }> };

export default async function ProjectPage({ params }: ProjectPageProps) {
  const { projectId } = await params;
  return <ProjectDetailView projectId={projectId} />;
}
