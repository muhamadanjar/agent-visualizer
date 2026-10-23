import { OmpRpcAdapter } from "./omp-rpc-adapter";
import type { SafeRuntimeEvent } from "./omp-rpc-adapter";

const prompt = "Use the read tool to read PRD.md and return only its first Markdown heading. Do not modify files.";
const activity: SafeRuntimeEvent[] = [];
let adapter: OmpRpcAdapter | null = null;
let unsubscribe: (() => void) | null = null;
let succeeded = false;

try {
  adapter = await OmpRpcAdapter.connect({ cwd: process.cwd() });
  unsubscribe = adapter.onActivity((event) => activity.push(event));
  const idleHealth = await adapter.getRuntimeState();
  if (!idleHealth.responsive || idleHealth.isSettled !== true || idleHealth.hasPendingAsyncWork !== false) {
    throw new Error("The idle runtime-state poll did not report a responsive settled session");
  }
  let invalidPromptsRejected = 0;
  for (const invalidPrompt of ["  ", "x".repeat(2_001)]) {
    try {
      await adapter.startReadOnlyTask(invalidPrompt);
    } catch {
      invalidPromptsRejected += 1;
    }
  }
  if (invalidPromptsRejected !== 2) throw new Error("Invalid prompts were not rejected before starting the task");

  const task = adapter.startReadOnlyTask(prompt);
  const activeStatus = adapter.getStatus();
  const activeHealth = await adapter.getRuntimeState();
  if (activeStatus.phase !== "active" || activeHealth.reason !== "active_task" || activeHealth.responsive) {
    throw new Error("An active task was not represented without issuing a blocking health request");
  }
  let concurrentStartRejected = false;
  try {
    await adapter.startReadOnlyTask(prompt);
  } catch {
    concurrentStartRejected = true;
  }
  if (!concurrentStartRejected) throw new Error("Concurrent task start was not rejected");
  const result = await task;

  const correlatedReadStart = activity.some(
    (event) => event.requestId === result.requestId && event.attribution === "single-active-task-window" && event.type === "tool_execution_start" && event.toolName === "read",
  );
  const correlatedReadEnd = activity.some(
    (event) => event.requestId === result.requestId && event.attribution === "single-active-task-window" && event.type === "tool_execution_end" && event.toolName === "read",
  );
  const unexpectedTool = activity.some(
    (event) => event.requestId === result.requestId && event.toolName === "other",
  );

  if (
    result.agentInvoked !== true ||
    result.status !== "completed" ||
    result.sessionSettled !== true ||
    result.cancelAcknowledged !== false
  ) {
    throw new Error("The real read-only task did not complete with the expected settlement state");
  }
  if (!correlatedReadStart || !correlatedReadEnd || unexpectedTool) {
    throw new Error("Read-tool activity was missing, unattributed, or outside the read-only boundary");
  }

  console.log(JSON.stringify({
    adapter: "m0-omp-rpc",
    protocolVersion: 2,
    idleHealth: {
      responsive: idleHealth.responsive,
      isSettled: idleHealth.isSettled,
      hasPendingAsyncWork: idleHealth.hasPendingAsyncWork,
      heartbeat: idleHealth.heartbeat,
    },
    activePhase: activeStatus.phase,
    activeHealthReason: activeHealth.reason,
    requestId: result.requestId,
    invalidPromptsRejected,
    concurrentStartRejected,
    agentInvoked: result.agentInvoked,
    status: result.status,
    sessionSettled: result.sessionSettled,
    activity: activity.map((event) => ({
      requestId: event.requestId,
      attribution: event.attribution,
      type: event.type,
      ...(event.toolName ? { toolName: event.toolName } : {}),
      ...(typeof event.isError === "boolean" ? { isError: event.isError } : {}),
    })),
  }, null, 2));
  succeeded = true;
} catch {
  console.error(JSON.stringify({ adapter: "m0-omp-rpc", result: "failed", error: "bounded_read_only_smoke_failed" }));
  process.exitCode = 1;
} finally {
  unsubscribe?.();
  if (adapter) {
    try {
      await adapter.close();
    } catch {
      if (succeeded) {
        console.error(JSON.stringify({ adapter: "m0-omp-rpc", result: "shutdown_failed" }));
        process.exitCode = 1;
      }
    }
  }
}
