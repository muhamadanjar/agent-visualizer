import Link from "next/link";
import { TaskBoard } from "../../components/task-board";
import { PageHeader } from "../../components/workspace-components";

export default function TasksPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Work coordination" title="Tasks" description="Persisted work requests. Tasks remain separate from their runtime Runs / Attempts.">
        <Link className="button button-secondary" href="/tasks/new">Create task</Link>
      </PageHeader>
      <TaskBoard />
    </div>
  );
}
