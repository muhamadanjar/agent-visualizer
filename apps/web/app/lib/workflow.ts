import { requestTaskData, type Task } from "./tasks";

export type WorkflowDelegationStart = {
  node_id: string;
  child_task_id: string;
  child_revision: number;
  digest: string;
  dependencies: string[];
  status: string;
};

export type WorkflowStartResponse = {
  parent_run_id: string;
  plan_digest: string;
  plan_revision: number;
  status: string;
  delegations: WorkflowDelegationStart[];
};

export type WorkflowChildTask = {
  title: string;
  goal: string;
  input_text: string;
  acceptance_criteria: string[];
  resource_refs: string[];
  output_schema: unknown;
};

export type WorkflowDelegation = WorkflowDelegationStart & {
  child_task: WorkflowChildTask | null;
  definition_id: string;
  definition_version: number;
  instance_id: string;
  project_id: string;
  workspace_path: string;
  input_payload: unknown;
  resource_refs: unknown;
  output_schema: unknown;
  model_scope: unknown;
  tool_scope: unknown;
  limits: unknown;
  approval_id: string | null;
  approval_digest: string | null;
  approval_expires_at: string | null;
  approval_decision: string | null;
  child_run_id: string | null;
  output: unknown;
};

export type WorkflowProjection = {
  parent_run_id: string;
  plan_digest: string;
  plan_revision: number;
  status: string;
  delegations: WorkflowDelegation[];
};

export type WorkflowStartInput = {
  request_id: string;
  goal: string;
  input_refs?: string[];
  max_child_runs?: number | null;
  max_duration_minutes?: number | null;
};

export function clampWorkflowLimits(
  maxChildRuns: number | null,
  maxDurationMinutes: number | null,
): Pick<WorkflowStartInput, "max_child_runs" | "max_duration_minutes"> {
  return {
    max_child_runs: maxChildRuns === null ? null : Math.min(maxChildRuns, 8),
    max_duration_minutes: maxDurationMinutes === null ? null : Math.min(maxDurationMinutes, 60),
  };
}

export function buildWorkflowStartInput(
  task: Pick<Task, "resource_refs" | "max_child_runs" | "max_duration_minutes">,
  goal: string,
  requestId: string,
): WorkflowStartInput {
  return {
    request_id: requestId,
    goal,
    input_refs: task.resource_refs,
    ...clampWorkflowLimits(task.max_child_runs, task.max_duration_minutes),
  };
}

export function requestWorkflowStart(
  taskId: string,
  input: WorkflowStartInput,
): Promise<WorkflowStartResponse> {
  return requestTaskData<WorkflowStartResponse>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/workflow`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export async function requestRunWorkflow(
  runId: string,
  signal?: AbortSignal,
): Promise<WorkflowProjection | null> {
  try {
    return await requestTaskData<WorkflowProjection>(
      `/api/v1/runs/${encodeURIComponent(runId)}/workflow`,
      signal ? { signal } : undefined,
    );
  } catch (reason) {
    if (reason instanceof Error && reason.message === "Workflow plan is not available") {
      return null;
    }
    throw reason;
  }
}
export async function decideWorkflowApproval(
  approvalId: string,
  actionDigest: string,
  decision: "approved" | "rejected",
): Promise<void> {
  await requestTaskData(`/api/v1/approvals/${encodeURIComponent(approvalId)}/decision`, {
    method: "POST",
    body: JSON.stringify({ action_digest: actionDigest, decision }),
  });
}
