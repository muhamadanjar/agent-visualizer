export type Task = {
  id: string;
  revision: number;
  stage: "Backlog";
  project_id: string;
  project_name: string;
  agent_instance_id: string | null;
  agent_instance_name: string | null;
  title: string;
  goal: string;
  input_text: string;
  resource_refs: string[];
  acceptance_criteria: string[];
  blocked_by_task_ids: string[];
  max_duration_minutes: number | null;
  max_child_runs: number | null;
  created_at: string;
  updated_at: string;
  revision_created_at: string;
};

export type TaskWrite = {
  project_id: string;
  agent_instance_id: string | null;
  title: string;
  goal: string;
  input_text: string;
  resource_refs: string[];
  acceptance_criteria: string[];
  blocked_by_task_ids: string[];
  max_duration_minutes: number | null;
  max_child_runs: number | null;
};

export type TaskProject = {
  id: string;
  name: string;
  summary: string;
};

export type TaskAgentInstance = {
  id: string;
  display_name: string;
  definition_name: string;
  project_name: string | null;
};

export async function requestTaskData<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("accept", "application/json");
  if (init?.body) headers.set("content-type", "application/json");
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json() as { detail?: unknown }).detail;
    } catch {

      detail = undefined;
    }
    const message = typeof detail === "string"
      ? detail
      : response.status === 404
        ? "Task or referenced resource was not found or is not accessible."
        : `Could not load task data (HTTP ${response.status}).`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

const TASK_PAGE_SIZE = 200;

async function requestAllPages<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const records: T[] = [];
  for (let offset = 0; ; offset += TASK_PAGE_SIZE) {
    const query = new URLSearchParams({ limit: String(TASK_PAGE_SIZE), offset: String(offset) });
    const page = await requestTaskData<T[]>(
      `${path}?${query.toString()}`,
      signal ? { signal } : undefined,
    );
    records.push(...page);
    if (page.length < TASK_PAGE_SIZE) return records;
    if (offset === 1_000_000) {
      throw new Error("Too many records to load from this list.");
    }
  }
}

export function requestTasks(signal?: AbortSignal): Promise<Task[]> {
  return requestTaskData<Task[]>("/api/v1/tasks?limit=100", signal ? { signal } : undefined);
}

export function requestTask(taskId: string, signal?: AbortSignal): Promise<Task> {
  return requestTaskData<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, signal ? { signal } : undefined);
}

export function requestTaskRevisions(
  taskId: string,
  { limit = 100, offset = 0, signal }: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<Task[]> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return requestTaskData<Task[]>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/revisions?${query.toString()}`,
    signal ? { signal } : undefined,
  );
}

export function requestTaskProjects(signal?: AbortSignal): Promise<TaskProject[]> {
  return requestAllPages<TaskProject>("/api/v1/projects", signal);
}

export function requestTaskAgents(signal?: AbortSignal): Promise<TaskAgentInstance[]> {
  return requestAllPages<TaskAgentInstance>("/api/v1/agent-instances", signal);
}

export function saveTask(task: TaskWrite, taskId?: string): Promise<Task> {
  return requestTaskData<Task>(
    taskId ? `/api/v1/tasks/${encodeURIComponent(taskId)}` : "/api/v1/tasks",
    {
      method: taskId ? "PUT" : "POST",
      body: JSON.stringify(task),
    },
  );
}

export function requestAllTasks(signal?: AbortSignal): Promise<Task[]> {
  return requestAllPages<Task>("/api/v1/tasks", signal);
}
