"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import {
  requestTaskAgents,
  requestTaskData,
  requestTaskProjects,
  requestAllTasks,
  saveTask,
  type Task,
  type TaskAgentInstance,
  type TaskProject,
  type TaskWrite,
} from "../lib/tasks";
import { PageHeader, StatusPill } from "./workspace-components";

function lines(value: string): string[] {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
}

export function TaskRequestForm({ taskId }: { taskId?: string }) {
  const router = useRouter();
  const [projects, setProjects] = useState<TaskProject[]>([]);
  const [agents, setAgents] = useState<TaskAgentInstance[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("");
  const [agentInstanceId, setAgentInstanceId] = useState("");
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [inputText, setInputText] = useState("");
  const [resourceRefs, setResourceRefs] = useState("");
  const [acceptanceCriteria, setAcceptanceCriteria] = useState("");
  const [blockedByTaskIds, setBlockedByTaskIds] = useState<string[]>([]);
  const [maxDurationMinutes, setMaxDurationMinutes] = useState("");
  const [maxChildRuns, setMaxChildRuns] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void Promise.all([
      requestTaskProjects(controller.signal),
      requestTaskAgents(controller.signal),
      requestAllTasks(controller.signal),
      taskId ? requestTaskData<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { signal: controller.signal }) : Promise.resolve(null),
    ]).then(([loadedProjects, loadedAgents, loadedTasks, currentTask]) => {
      if (controller.signal.aborted) return;
      setProjects(loadedProjects);
      setAgents(loadedAgents);
      setTasks(loadedTasks);
      setTask(currentTask);
      if (currentTask) {
        setProjectId(currentTask.project_id);
        setAgentInstanceId(currentTask.agent_instance_id ?? "");
        setTitle(currentTask.title);
        setGoal(currentTask.goal);
        setInputText(currentTask.input_text);
        setResourceRefs(currentTask.resource_refs.join("\n"));
        setAcceptanceCriteria(currentTask.acceptance_criteria.join("\n"));
        setBlockedByTaskIds(currentTask.blocked_by_task_ids);
        setMaxDurationMinutes(currentTask.max_duration_minutes?.toString() ?? "");
        setMaxChildRuns(currentTask.max_child_runs?.toString() ?? "");
      }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Could not load task form data.");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [taskId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !projectId) return;
    setSaving(true);
    setError(null);
    const payload: TaskWrite = {
      project_id: projectId,
      agent_instance_id: agentInstanceId || null,
      title,
      goal,
      input_text: inputText,
      resource_refs: lines(resourceRefs),
      acceptance_criteria: lines(acceptanceCriteria),
      blocked_by_task_ids: blockedByTaskIds,
      max_duration_minutes: maxDurationMinutes ? Number(maxDurationMinutes) : null,
      max_child_runs: maxChildRuns ? Number(maxChildRuns) : null,
    };
    try {
      const saved = await saveTask(payload, taskId);
      router.push(`/tasks/${saved.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save task.");
    } finally {
      setSaving(false);
    }
  }

  function toggleDependency(dependencyId: string) {
    setBlockedByTaskIds((current) => current.includes(dependencyId)
      ? current.filter((id) => id !== dependencyId)
      : [...current, dependencyId]);
  }

  const eligibleDependencies = tasks.filter((candidate) => candidate.id !== taskId);

  if (loading) {
    return <main className="workspace-gate" role="status">Loading task form…</main>;
  }
  if (error && !projects.length) {
    return <div className="page-stack"><PageHeader eyebrow="Work coordination" title="Task form unavailable" description={error} /><Link className="text-link" href="/tasks">Back to tasks</Link></div>;
  }
  if (taskId && !task) {
    return <div className="page-stack"><PageHeader eyebrow="Work coordination" title="Task unavailable" description="The task could not be loaded or is not accessible." /><Link className="text-link" href="/tasks">Back to tasks</Link></div>;
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Work coordination"
        title={taskId ? "Edit task" : "New task"}
        description={taskId ? "Save a changed specification as a new immutable task revision." : "Create a persisted work request in Backlog. No runtime is started."}
      >
        <Link className="text-link" href={taskId ? `/tasks/${taskId}` : "/tasks"}>Cancel</Link>
      </PageHeader>
      <form className="content-section draft-form" onSubmit={handleSubmit}>
        <div className="section-heading"><h2>Task specification</h2><StatusPill>{taskId ? `Current revision ${task?.revision}` : "Backlog"}</StatusPill></div>
        <label className="preview-field">Title<input name="title" value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} autoComplete="off" /></label>
        <label className="preview-field">Goal<textarea name="goal" rows={4} value={goal} onChange={(event) => setGoal(event.target.value)} required maxLength={10000} /></label>
        <label className="preview-field">Project
          <select name="project_id" value={projectId} onChange={(event) => setProjectId(event.target.value)} required>
            <option value="" disabled>Select a project</option>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
        </label>
        <label className="preview-field">Agent instance
          <select name="agent_instance_id" value={agentInstanceId} onChange={(event) => setAgentInstanceId(event.target.value)}>
            <option value="">Unassigned · choose explicitly</option>
            {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.display_name} · {agent.definition_name}</option>)}
          </select>
        </label>
        <label className="preview-field">Inputs<textarea name="input_text" rows={3} value={inputText} onChange={(event) => setInputText(event.target.value)} maxLength={10000} placeholder="Text supplied as task context" /></label>
        <label className="preview-field">Resource references<textarea name="resource_refs" rows={3} value={resourceRefs} onChange={(event) => setResourceRefs(event.target.value)} placeholder="One inert reference per line; references do not grant resource access" /></label>
        <p className="field-note">References are stored as text only. Repository access and resource resolution are not enabled.</p>
        <label className="preview-field">Acceptance criteria<textarea name="acceptance_criteria" rows={4} value={acceptanceCriteria} onChange={(event) => setAcceptanceCriteria(event.target.value)} required placeholder="One verifiable outcome per line" /></label>
        <fieldset className="draft-fieldset">
          <legend>Blocked by</legend>
          {eligibleDependencies.length
            ? eligibleDependencies.map((candidate) => (
              <label className="check-row" key={candidate.id}>
                <input
                  type="checkbox"
                  checked={blockedByTaskIds.includes(candidate.id)}
                  onChange={() => toggleDependency(candidate.id)}
                />
                <span>{candidate.title} · {candidate.project_name}</span>
              </label>
            ))
            : <p className="field-note">No other tasks are available as dependencies.</p>}
          <p className="field-note">Dependencies are checked for owner scope and cycles before saving.</p>
        </fieldset>
        <fieldset className="draft-fieldset">
          <legend>Execution bounds for future runs</legend>
          <label className="preview-field">Maximum duration (minutes)
            <input type="number" min="1" max="1440" value={maxDurationMinutes} onChange={(event) => setMaxDurationMinutes(event.target.value)} />
          </label>
          <label className="preview-field">Maximum child runs
            <input type="number" min="0" max="100" value={maxChildRuns} onChange={(event) => setMaxChildRuns(event.target.value)} />
          </label>
          <p className="field-note">Stored as task specification only; no runtime currently enforces these bounds.</p>
        </fieldset>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        {projects.length === 0 ? <p className="inline-error" role="alert">Create a project before saving a task.</p> : null}
        <div className="form-actions">
          <button className="button" type="submit" disabled={saving || projects.length === 0}>
            {saving ? "Saving…" : taskId ? "Save new revision" : "Create task"}
          </button>
          <span className="muted">Saving creates a task or a new revision. It does not queue or execute work.</span>
        </div>
      </form>
    </div>
  );
}
