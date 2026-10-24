import { TaskRequestForm } from "../../../../components/task-request-form";

export default async function EditTaskPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  return <TaskRequestForm taskId={taskId} />;
}
