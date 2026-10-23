import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

type RpcChild = ReturnType<typeof Bun.spawn>;
const FRAME_TIMEOUT_MS = 60_000;
const SESSION_DIR = await mkdtemp(join(tmpdir(), "omp-m0-recovery-"));
const PROMPT_ID = "m0-recovery-prompt";
let firstChild: RpcChild | null = null;
let restoredChild: RpcChild | null = null;
let initialSessionFile = "";

function withTimeout<T>(promise: Promise<T>, label: string): Promise<T> {
  const { promise: timeout, reject } = Promise.withResolvers<never>();
  const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), FRAME_TIMEOUT_MS);
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function spawn(args: string[]): {
  child: RpcChild;
  nextFrame: () => Promise<Record<string, any> | null>;
  send: (frame: Record<string, unknown>) => Promise<void>;
  stderrText: () => string;
  stderrDone: Promise<void>;
} {
  const child = Bun.spawn(args, { cwd: process.cwd(), stdin: "pipe", stdout: "pipe", stderr: "pipe" });
  let stderrContent = "";
  const stderrDone = child.stderr.pipeTo(new WritableStream({
    write(chunk) {
      stderrContent = (stderrContent + new TextDecoder().decode(chunk)).slice(-4_096);
    },
  })).catch(() => {});
  const stderrText = () => stderrContent;
  const reader = child.stdout.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  async function nextFrame(): Promise<Record<string, any> | null> {
    while (true) {
      const newline = buffer.indexOf("\n");
      if (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (!line.trim()) continue;
        const frame = JSON.parse(line);
        if (!frame || typeof frame !== "object" || typeof frame.type !== "string") {
          throw new Error("Received a malformed RPC frame");
        }
        return frame;
      }
      const result = await withTimeout(reader.read(), "an OMP RPC frame");
      if (result.done) return null;
      buffer += decoder.decode(result.value, { stream: true });
    }
  }

  async function send(frame: Record<string, unknown>): Promise<void> {
    await child.stdin.write(`${JSON.stringify(frame)}\n`);
    await child.stdin.flush();
  }

  return { child, nextFrame, send, stderrText, stderrDone };
}

async function ready(
  client: ReturnType<typeof spawn>,
  negotiationId: string,
  label: string,
): Promise<Record<string, any>[]> {
  const startupFrames: Record<string, any>[] = [];
  let frame: Record<string, any> | null = null;
  while (frame?.type !== "ready") {
    frame = await client.nextFrame();
    if (!frame) throw new Error(`${label} exited before RPC ready`);
    if (frame.type !== "ready") startupFrames.push(frame);
  }
  if (!frame.supportedProtocolVersions?.includes(2)) {
    throw new Error(`${label} did not advertise RPC protocol v2`);
  }
  await client.send({ id: negotiationId, type: "negotiate_protocol", protocolVersion: 2 });
  while (true) {
    const reply = await client.nextFrame();
    if (!reply) throw new Error(`${label} exited during protocol negotiation`);
    if (reply.type === "response" && reply.id === negotiationId) {
      if (reply.success !== true) throw new Error(`${label} RPC v2 negotiation failed`);
      return startupFrames;
    }
    startupFrames.push(reply);
  }
}
function sanitizeDiagnostic(value: string): string | null {
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const selected = lines.filter((line) => /error|restore|session|failed/i.test(line)).slice(-2);
  if (selected.length === 0) return null;
  return selected.join(" ").replace(/(api[_-]?key|token|authorization|secret)\s*[:=]\s*\S+/gi, "$1=<redacted>")
    .replace(/\/(?:home|tmp|root)\/[^\s:]+/g, "<path>")
    .slice(0, 500);
}

async function stop(child: RpcChild | null): Promise<void> {
  if (!child) return;
  if (child.exitCode === null) child.kill("SIGKILL");
  await child.exited;
}

let stage = "initial session startup";
try {
  const initial = spawn([
    "rtk", "omp", "--mode", "rpc", `--session-dir=${SESSION_DIR}`,
    "--tools=read", "--max-time=60s",
  ]);
  firstChild = initial.child;
  await ready(initial, "m0-recovery-negotiate-1", "Initial session");
  stage = "initial state query";
  await initial.send({ id: "m0-recovery-state", type: "get_state" });
  let state: Record<string, any> | null = null;
  while (!state) {
    const frame = await initial.nextFrame();
    if (!frame) throw new Error("OMP exited before get_state");
    if (frame.type === "response" && frame.id === "m0-recovery-state") state = frame.data;
  }
  if (typeof state.sessionFile !== "string" || state.sessionFile.length === 0) {
    throw new Error("OMP did not report a session file for recovery");
  }
  initialSessionFile = state.sessionFile;
  stage = "starting read-only task";

  await initial.send({
    id: PROMPT_ID,
    type: "prompt",
    message: "Read PRD.md and write a comprehensive summary of its functional requirements. Do not modify files.",
  });
async function persistedBytes(path: string): Promise<number> {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    try {
      return (await stat(path)).size;
    } catch {
      await Bun.sleep(100);
    }
  }
  throw new Error("Session file was not persisted before crash injection");
}
  let activeObserved = false;
  let preCrashReadStarts = 0;
  let readEndObserved = false;
  stage = "waiting for read-only tool completion";
  while (!readEndObserved) {
    const frame = await initial.nextFrame();
    if (!frame) throw new Error("OMP exited before a read-only tool completed");
    if (frame.type === "agent_start") activeObserved = true;
    if (frame.type === "tool_execution_start" && frame.toolName === "read") preCrashReadStarts += 1;
    if (frame.type === "tool_execution_end" && frame.toolName === "read") readEndObserved = true;
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) {
      throw new Error(`Task completed before crash injection: ${frame.status}`);
    }
  }

  const sessionBytesBeforeCrash = await persistedBytes(initialSessionFile);
  stage = "crash injection";

  firstChild.kill("SIGKILL");
  const crashExitCode = await firstChild.exited;
  const sessionBytesAfterCrash = (await stat(initialSessionFile)).size;

  stage = "restart with --continue";
  const restored = spawn([
    "rtk", "omp", "--mode", "rpc", `--session-dir=${SESSION_DIR}`,
    "--continue", "--tools=read", "--max-time=60s",
  ]);
  restoredChild = restored.child;
  let restoreStartupFrames: Record<string, any>[] = [];
  let restoreExitCode: number | null = null;
  let restoreReady = false;
  let restoreFailure: string | null = null;
  let restoreStderrSummary: string | null = null;
  try {
    restoreStartupFrames = await ready(restored, "m0-recovery-negotiate-2", "Restored session");
    restoreReady = true;
  } catch (error) {
    restoreFailure = error instanceof Error ? error.message : "Restored session startup failed";
    if (restoredChild.exitCode === null) restoredChild.kill("SIGKILL");
    restoreExitCode = await restoredChild.exited;
    await restored.stderrDone;
    restoreStderrSummary = sanitizeDiagnostic(restored.stderrText());
  }

  let continuedState: Record<string, any> | null = null;
  let restoreAgentStarts = 0;
  let restoreReadStarts = 0;
  let recoveredPromptResults = 0;
  const restoreFrameTypes = new Set<string>();
  for (const frame of restoreStartupFrames) {
    restoreFrameTypes.add(frame.type);
    if (frame.type === "agent_start") restoreAgentStarts += 1;
    if (frame.type === "tool_execution_start" && frame.toolName === "read") restoreReadStarts += 1;
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) recoveredPromptResults += 1;
  }
  if (restoreReady) {
    stage = "querying continued session state";
    await restored.send({ id: "m0-recovery-state-2", type: "get_state" });
    while (!continuedState) {
      const frame = await restored.nextFrame();
      if (!frame) break;
      restoreFrameTypes.add(frame.type);
      if (frame.type === "agent_start") restoreAgentStarts += 1;
      if (frame.type === "tool_execution_start" && frame.toolName === "read") restoreReadStarts += 1;
      if (frame.type === "prompt_result" && frame.id === PROMPT_ID) recoveredPromptResults += 1;
      if (frame.type === "response" && frame.id === "m0-recovery-state-2") continuedState = frame.data;
    }
    if (!continuedState) restoreFailure = "Continued session returned no get_state response";
  }
  const recoverySettled = continuedState?.isSettled === true;
  let postRecoveryTaskStatus: string | null = null;
  let postRecoveryReadStarts = 0;
  let postRecoveryReadEnds = 0;
  if (restoreReady && recoverySettled && continuedState?.sessionId === state.sessionId) {
    stage = "verifying a fresh task after continuation";
    await restored.send({
      id: "m0-recovery-follow-up",
      type: "prompt",
      message: "Read docs/progress.md and return only its first Markdown heading. Do not modify files.",
    });
    while (postRecoveryTaskStatus === null) {
      const frame = await restored.nextFrame();
      if (!frame) throw new Error("OMP exited before the post-recovery task completed");
      if (frame.type === "tool_execution_start" && frame.toolName === "read") postRecoveryReadStarts += 1;
      if (frame.type === "tool_execution_end" && frame.toolName === "read") postRecoveryReadEnds += 1;
      if (frame.type === "prompt_result" && frame.id === "m0-recovery-follow-up") {
        postRecoveryTaskStatus = frame.status;
      }
    }
  }
  console.log(JSON.stringify({
    protocolV2NegotiatedBeforeCrash: true,
    restoreRpcReady: restoreReady,
    restoreFailure,
    restoreStderrSummary,
    restoreExitCode,
    sessionFileCaptured: true,
    sessionBytesBeforeCrash,
    sessionBytesAfterCrash,
    crashExitCode,
    activeObserved,
    preCrashReadStarts,
    recoverySettled,
    continuedSessionIdMatchesOriginal: continuedState?.sessionId === state.sessionId,
    continuedStateKeys: continuedState ? Object.keys(continuedState).sort() : [],
    restoreAgentStarts,
    restoreReadStarts,
    recoveredPromptResults,
    restoreFrameTypes: [...restoreFrameTypes].sort(),
    postRecoveryTaskStatus,
    postRecoveryReadStarts,
    postRecoveryReadEnds,
  }, null, 2));
} catch (error) {
  console.log(JSON.stringify({
    probeFailedAt: stage,
    failure: error instanceof Error ? error.message : "Unexpected recovery probe failure",
  }, null, 2));
  process.exitCode = 1;
} finally {
  await stop(firstChild);
  await stop(restoredChild);
  await rm(SESSION_DIR, { recursive: true, force: true });
}
