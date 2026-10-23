import { AgentDefinitionDetailView } from "../../../../components/agent-definitions-view";

type DefinitionPageProps = { params: Promise<{ definitionId: string }> };

export default async function DefinitionPage({ params }: DefinitionPageProps) {
  const { definitionId } = await params;
  return <AgentDefinitionDetailView definitionId={definitionId} />;
}
