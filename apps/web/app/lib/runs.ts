import { requestTaskData } from "./tasks";

export type RunSnapshot = {
  task: {
    id: string;
    revision: number;
    project_id: string;
    project_name: string;
    title: string;
    goal: string;
    input_text: string;
    resource_refs: string[];
    acceptance_criteria: string[];
    blocked_by_task_ids: string[];
    max_duration_minutes: number | null;
    max_child_runs: number | null;
  };
  agent_instance: { id: string; display_name: string };
  agent_definition: {
    id: string;
    version: number;
    name: string;
    description: string;
    source_markdown: string;
    unmapped_metadata: Record<string, unknown>;
    configuration?: {
      role: string;
      model_endpoint_id: string;
      model_id: string;
      route: string | null;
      runtime_id: string;
      temperature: number | null;
      max_iterations: number;
      concurrency_limit: number;
      execution_timeout_seconds: number;
      allowed_tools: string[];
    } | null;
  };
  skills: Array<{
    id: string;
    version: number;
    name: string;
    description: string;
    instructions: string;
    dependencies: unknown;
    resources: unknown;
  }>;
  model_endpoint?: {
    id: string;
    adapter: string;
    endpoint_url: string;
    credential_env: string;
  } | null;
  /** Historical snapshots created before Model Endpoints replaced Provider configuration. */
  provider?: unknown | null;
  runtime?: unknown | null;
};

export type RunRecord = {
  id: string;
  task_id: string;
  task_revision: number;
  attempt_number: number;
  request_id: string;
  status: string;
  configuration_snapshot: RunSnapshot;
  created_at: string;
};

export type RunPage = { runs: RunRecord[]; hasMore: boolean };

async function requestRunData<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json() as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new Error(typeof detail === "string"
      ? detail
      : response.status === 404
        ? "Run record was not found or is not accessible."
        : `Could not load Run data (HTTP ${response.status}).`);
  }
  return response.json() as Promise<T>;
}

export async function requestTaskRunPage(
  taskId: string,
  { limit = 20, offset = 0, signal }: { limit?: number; offset?: number; signal?: AbortSignal } = {},
): Promise<RunPage> {
  const query = new URLSearchParams({ limit: String(limit + 1), offset: String(offset) });
  const records = await requestTaskData<RunRecord[]>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/runs?${query.toString()}`,
    signal ? { signal } : undefined,
  );
  return { runs: records.slice(0, limit), hasMore: records.length > limit };
}

export function requestRun(runId: string, signal?: AbortSignal): Promise<RunRecord> {
  return requestRunData<RunRecord>(
    `/api/v1/runs/${encodeURIComponent(runId)}`,
    signal,
  );
}

export type RunResultRecord = {
  run_id: string;
  text: string;
  byte_length: number;
  created_at: string;
};

export function requestRunResult(
  runId: string,
  signal?: AbortSignal,
): Promise<RunResultRecord> {
  return requestRunData<RunResultRecord>(
    `/api/v1/runs/${encodeURIComponent(runId)}/result`,
    signal,
  );
}

export async function requestRunCancellation(runId: string): Promise<void> {
  const response = await fetch(
    `/api/v1/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { accept: "application/json" },
    },
  );
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json() as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new Error(typeof detail === "string"
      ? detail
      : `Could not request cancellation (HTTP ${response.status}).`);
  }
  const body = await response.json() as { status?: unknown };
  if (response.status !== 202 || body.status !== "cancellation_pending") {
    throw new Error("API did not confirm a pending Run cancellation.");
  }
}
