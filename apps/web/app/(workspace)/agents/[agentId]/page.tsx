import { AgentInstanceEditor } from "../../../components/agent-instances-view";

export default async function AgentInstancePage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = await params;
  return <AgentInstanceEditor instanceId={agentId} />;
}
