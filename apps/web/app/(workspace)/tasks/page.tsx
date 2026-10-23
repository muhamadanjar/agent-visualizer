import Link from "next/link";
import { TaskBoard } from "../../components/task-board";
import { PageHeader, SampleNotice } from "../../components/workspace-components";

export default function TasksPage() {
  return (
    <div className="page-stack">
      <PageHeader eyebrow="Work coordination" title="Tasks" description="A sample workflow board. Task stage and execution run status remain distinct concepts."><Link className="button button-secondary" href="/tasks/new">Draft task</Link></PageHeader>
      <SampleNotice />
      <TaskBoard />
    </div>
  );
}
