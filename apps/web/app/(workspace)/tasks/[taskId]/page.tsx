import { TaskDetailView } from "../../../components/task-detail-view";

export default async function TaskDetailPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  return <TaskDetailView taskId={taskId} />;
}
