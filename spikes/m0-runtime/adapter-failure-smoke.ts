import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { delimiter, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { OmpRpcAdapter } from "./omp-rpc-adapter";

type Scenario = "malformed" | "chunked" | "timeout" | "exit";

const fixtureExecutable = `#!/bin/sh
printf '%s\\n' "$$" > "$OMP_FIXTURE_PID"
case "$OMP_FIXTURE_SCENARIO" in
  malformed)
    printf 'not-json\\n'
    IFS= read -r line
    ;;
  chunked)
    printf '{"type":"ready","supportedProtocolVersions":[2]}\\n'
    printf '{"type":"rpc_chunk"}\\n'
    IFS= read -r line
    ;;
  timeout)
    printf '{"type":"ready","supportedProtocolVersions":[2]}\\n'
    IFS= read -r line
    IFS= read -r line
    ;;
  exit)
    exit 23
    ;;
  duplicate_late)
    "$BUN" "$OMP_FIXTURE_SCRIPT"
    ;;
  cancel_race)
    exec "$BUN" "$OMP_FIXTURE_SCRIPT"
    ;;
  *)
    exit 24
    ;;
esac
`;
const eventFixtureSource = `#!/usr/bin/env bun
import { createInterface } from "node:readline";
import { writeFile } from "node:fs/promises";

const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
async function nextRequest() {
  const line = await lines.next();
  if (line.done) throw new Error("Adapter closed before sending the expected request");
  return JSON.parse(line.value);
}
function send(frame) {
  process.stdout.write(\`\${JSON.stringify(frame)}\\n\`);
}

send({ type: "ready", supportedProtocolVersions: [2] });
const negotiation = await nextRequest();
send({ type: "response", id: negotiation.id, success: true });
if (process.env.OMP_FIXTURE_SCENARIO === "cancel_race") {
  const health = await nextRequest();
  if (health.type !== "get_state") throw new Error("Expected an idle get_state request");
  send({ type: "response", id: health.id, success: true, data: { isSettled: true, hasPendingAsyncWork: false } });
  const prompt = await nextRequest();
  send({ type: "response", id: prompt.id, success: true });
  send({ type: "agent_start" });
  const abort = await nextRequest();
  if (abort.type !== "abort") throw new Error("Expected abort directly, not a blocking active get_state request");
  const abortCountPath = process.env.OMP_FIXTURE_ABORT_COUNT;
  if (!abortCountPath) throw new Error("Abort counter path is missing");
  await writeFile(abortCountPath, "1");
  process.stdout.write(\`\${JSON.stringify({ type: "response", id: abort.id, success: true })}\\n\${JSON.stringify({ type: "prompt_result", id: prompt.id, agentInvoked: true, status: "aborted", sessionSettled: true })}\\n\`);
  await lines.next();
} else {
  const prompt = await nextRequest();
  send({ type: "response", id: prompt.id, success: true });
  send({ type: "tool_execution_start", toolName: "read" });
  send({ type: "tool_execution_start", toolName: "read" });
  send({ type: "prompt_result", id: prompt.id, agentInvoked: true, status: "completed", sessionSettled: true });
  await Bun.sleep(150);
  send({ type: "tool_execution_end", toolName: "read", isError: false });
  await lines.next();
}
`;

async function cancelRaceFixtureChild(): Promise<void> {
  const abortCountPath = process.env.OMP_FIXTURE_ABORT_COUNT;
  if (!abortCountPath) throw new Error("Abort counter path is missing");
  const adapter = await OmpRpcAdapter.connect();
  let noActiveCancelRejected = false;
  let settledCancelRejected = false;
  let succeeded = false;
  let stage = "cancel_without_run";
  let unsubscribe: (() => void) | null = null;
  try {
    try {
      await adapter.cancelActiveTask();
    } catch {
      noActiveCancelRejected = true;
    }
    stage = "idle_health";
    const idleHealth = await adapter.getRuntimeState();
    const agentStarted = Promise.withResolvers<void>();
    unsubscribe = adapter.onActivity((event) => {
      if (event.type === "agent_start") agentStarted.resolve();
    });
    stage = "agent_start";
    const task = adapter.startReadOnlyTask("Synthetic cancellation race fixture");
    await Promise.race([
      agentStarted.promise,
      Bun.sleep(5_000).then(() => { throw new Error("Synthetic agent did not start"); }),
    ]);
    const activeStatus = adapter.getStatus();
    stage = "active_health";
    const activeHealth = await adapter.getRuntimeState();
    stage = "cancel";
    const firstCancel = adapter.cancelActiveTask();
    const secondCancel = adapter.cancelActiveTask();
    const [firstAck, secondAck, result] = await Promise.all([firstCancel, secondCancel, task]);
    try {
      await adapter.cancelActiveTask();
    } catch {
      settledCancelRejected = true;
    }
    const cancelState = adapter.getStatus().cancelState;
    stage = "close";
    await adapter.close();
    const abortCount = (await readFile(abortCountPath, "utf8")).trim();
    const passed =
      noActiveCancelRejected &&
      settledCancelRejected &&
      idleHealth.responsive &&
      activeStatus.phase === "active" &&
      activeHealth.reason === "active_task" &&
      firstAck.acknowledged &&
      secondAck.acknowledged &&
      firstAck.requestId === result.requestId &&
      secondAck.requestId === result.requestId &&
      result.status === "aborted" &&
      result.sessionSettled &&
      result.cancelAcknowledged &&
      cancelState === "acknowledged" &&
      abortCount === "1";
    console.log(JSON.stringify({
      noActiveCancelRejected,
      idleHealthResponsive: idleHealth.responsive,
      activePhase: activeStatus.phase,
      activeHealthReason: activeHealth.reason,
      abortAcknowledged: firstAck.acknowledged,
      terminalStatus: result.status,
      sessionSettled: result.sessionSettled,
      cancelAcknowledgedBeforeTerminal: result.cancelAcknowledged,
      idempotentAbortCount: abortCount,
      settledCancelRejected,
      passed,
    }));
    if (!passed) throw new Error("Adapter cancellation race contract failed");
    succeeded = true;
  } catch (error) {
    const message = error instanceof Error ? error.message.slice(0, 120) : "unknown_error";
    console.error(JSON.stringify({ scenario: "cancel_race", stage, message }));
    throw error;
  } finally {
    unsubscribe?.();
    await adapter.close();
    if (!succeeded) process.exitCode = 1;
  }
}

async function duplicateLateFixtureChild(): Promise<void> {
  const adapter = await OmpRpcAdapter.connect();
  const events: Array<{ type: string }> = [];
  adapter.onActivity((event) => events.push(event));
  const result = await adapter.startReadOnlyTask("Synthetic fixture task");
  await Bun.sleep(250);
  await adapter.close();
  const duplicateStartCount = events.filter((event) => event.type === "tool_execution_start").length;
  const lateEndCount = events.filter((event) => event.type === "tool_execution_end").length;
  const passed = duplicateStartCount === 2 && lateEndCount === 0;
  console.log(JSON.stringify({ resultStatus: result.status, duplicateStartCount, lateEndCount, passed }));
  process.exitCode = passed ? 0 : 1;
}
async function fixtureChild(): Promise<void> {
  if (process.env.OMP_FIXTURE_SCENARIO === "cancel_race") {
    await cancelRaceFixtureChild();
    return;
  }
  if (process.env.OMP_FIXTURE_SCENARIO === "duplicate_late") {
    await duplicateLateFixtureChild();
    return;
  }

  let rejected = false;
  try {
    const adapter = await OmpRpcAdapter.connect();
    await adapter.close();
  } catch {
    rejected = true;
  }
  console.log(JSON.stringify({ rejected }));
  process.exitCode = rejected ? 0 : 1;
}

async function runDuplicateLateScenario(
  fixtureDir: string,
  scriptPath: string,
): Promise<{ scenario: "duplicate_late"; duplicateStartsForwarded: 2; lateEventsDropped: true }> {
  const pidPath = join(fixtureDir, "duplicate_late.pid");
  const child = Bun.spawn([process.execPath, scriptPath, "--fixture-child"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PATH: `${fixtureDir}${delimiter}${process.env.PATH ?? ""}`,
      BUN: process.execPath,
      OMP_FIXTURE_SCENARIO: "duplicate_late",
      OMP_FIXTURE_PID: pidPath,
      OMP_FIXTURE_SCRIPT: join(fixtureDir, "event-fixture.mjs"),
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  const timer = setTimeout(() => child.kill(), 30_000);
  let exitCode: number;
  let output: string;
  try {
    [output, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      child.exited,
      new Response(child.stderr).text(),
    ]).then(([stdout, code]) => [stdout, code] as [string, number]);
  } finally {
    clearTimeout(timer);
  }
  if (exitCode !== 0) throw new Error("Duplicate/late event fixture failed");
  const result: unknown = JSON.parse(output);
  if (typeof result !== "object" || result === null || !("passed" in result) || result.passed !== true) {
    throw new Error("Adapter duplicate/late event behavior changed");
  }
  const pid = Number(await readFile(pidPath, "utf8"));
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error("Fixture PID was invalid");
  requireDeadProcess(pid);
  return { scenario: "duplicate_late", duplicateStartsForwarded: 2, lateEventsDropped: true };
}
async function runCancelRaceScenario(
  fixtureDir: string,
  scriptPath: string,
): Promise<{ scenario: "cancel_race"; abortAcknowledgedBeforeTerminal: true; idempotentAbortCount: 1 }> {
  const pidPath = join(fixtureDir, "cancel_race.pid");
  const abortCountPath = join(fixtureDir, "cancel_race.count");
  const child = Bun.spawn([process.execPath, scriptPath, "--fixture-child"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PATH: `${fixtureDir}${delimiter}${process.env.PATH ?? ""}`,
      BUN: process.execPath,
      OMP_FIXTURE_SCENARIO: "cancel_race",
      OMP_FIXTURE_PID: pidPath,
      OMP_FIXTURE_ABORT_COUNT: abortCountPath,
      OMP_FIXTURE_SCRIPT: join(fixtureDir, "event-fixture.mjs"),
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill();
  }, 15_000);
  let exitCode: number;
  let output: string;
  let errorOutput: string;
  try {
    [output, exitCode, errorOutput] = await Promise.all([
      new Response(child.stdout).text(),
      child.exited,
      new Response(child.stderr).text(),
    ]).then(([stdout, code, stderr]) => [stdout, code, stderr] as [string, number, string]);
  } finally {
    clearTimeout(timer);
  }
  if (timedOut) throw new Error(`Adapter cancellation fixture timed out: ${errorOutput.slice(0, 160)}`);
  if (exitCode !== 0) throw new Error(`Adapter cancellation fixture failed: ${errorOutput.slice(0, 160)}`);
  const result: unknown = JSON.parse(output);
  if (typeof result !== "object" || result === null || !("passed" in result) || result.passed !== true) {
    throw new Error("Adapter cancellation race contract changed");
  }
  const pid = Number(await readFile(pidPath, "utf8"));
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error("Cancellation fixture PID was invalid");
  requireDeadProcess(pid);
  return { scenario: "cancel_race", abortAcknowledgedBeforeTerminal: true, idempotentAbortCount: 1 };
}

function requireDeadProcess(pid: number): void {
  try {
    process.kill(pid, 0);
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "ESRCH"
    ) {
      return;
    }
    throw error;
  }
  throw new Error("Fixture OMP process remained alive after adapter rejection");
}

async function runScenario(
  scenario: Scenario,
  fixtureDir: string,
  scriptPath: string,
): Promise<{ scenario: Scenario; rejected: true; childTerminated: true }> {
  const pidPath = join(fixtureDir, `${scenario}.pid`);
  const child = Bun.spawn([process.execPath, scriptPath, "--fixture-child"], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PATH: `${fixtureDir}${delimiter}${process.env.PATH ?? ""}`,
      OMP_FIXTURE_SCENARIO: scenario,
      OMP_FIXTURE_PID: pidPath,
    },
    stdout: "pipe",
    stderr: "pipe",
  });
  const timer = setTimeout(() => child.kill(), 30_000);
  let exitCode: number;
  let output: string;
  try {
    [output, exitCode] = await Promise.all([
      new Response(child.stdout).text(),
      child.exited,
      new Response(child.stderr).text(),
    ]).then(([stdout, code]) => [stdout, code] as [string, number]);
  } finally {
    clearTimeout(timer);
  }

  if (exitCode !== 0) throw new Error(`Adapter fixture runner failed for ${scenario}`);
  const result: unknown = JSON.parse(output);
  if (
    typeof result !== "object" ||
    result === null ||
    !("rejected" in result) ||
    result.rejected !== true
  ) {
    throw new Error(`Adapter accepted the ${scenario} fixture`);
  }

  const pid = Number(await readFile(pidPath, "utf8"));
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error("Fixture PID was invalid");
  requireDeadProcess(pid);
  return { scenario, rejected: true, childTerminated: true };
}

async function run(): Promise<void> {
  const scriptPath = process.argv[1];
  if (!scriptPath) throw new Error("Could not resolve the failure smoke script path");
  const fixtureDir = await mkdtemp(join(tmpdir(), "omp-rpc-adapter-"));
  try {
    await writeFile(join(fixtureDir, "omp"), fixtureExecutable, { mode: 0o700 });
    await writeFile(join(fixtureDir, "event-fixture.mjs"), eventFixtureSource, { mode: 0o700 });
    await chmod(join(fixtureDir, "omp"), 0o700);
    const cases: Scenario[] = ["malformed", "chunked", "timeout", "exit"];
    const results = [];
    for (const scenario of cases) {
      results.push(await runScenario(scenario, fixtureDir, resolve(scriptPath)));
    }
    results.push(await runCancelRaceScenario(fixtureDir, resolve(scriptPath)));
    console.log(JSON.stringify({ adapter: "m0-omp-rpc", cases: results }, null, 2));
  } finally {
    await rm(fixtureDir, { recursive: true, force: true });
  }
}

if (process.argv[2] === "--fixture-child") {
  await fixtureChild();
} else {
  try {
    await run();
  } catch (error) {
    const reason = error instanceof Error ? error.message.slice(0, 220) : "unknown_error";
    console.error(JSON.stringify({ adapter: "m0-omp-rpc", result: "failure_smoke_failed", reason }));
    process.exitCode = 1;
  }
}
