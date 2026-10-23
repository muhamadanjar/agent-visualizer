/**
 * OMP v18.12.3 RPC approval probe (rpc-ui, protocol v2).
 *
 * Security posture: fail closed. The only command this probe ever approves is the exact
 * harmless string `true`. Every other approval request — missing evidence, malformed
 * evidence, a command outside the allowlist, a second request, an ambiguous pending call —
 * is denied.
 *
 * Evidence gathered (booleans and correlation results only, never raw command arguments,
 * tool output, prompts, or account data):
 *   - the approval request shape OMP v18.12.3 actually emits (`extension_ui_request`,
 *     method `select`, no `toolCallId` field in v18.12.3),
 *   - whether the request can be bound to a specific pending tool call by id,
 *   - whether the pending call's structured runtime evidence (`tool_execution_start.args`)
 *     and the prompt's own command line agree on the exact allowed command.
 *
 * When the protocol exposes no id binding, the smoke reports that narrower behavior as
 * unproven instead of inferring it from a successful allow/deny.
 */

const RESPONSE_TIMEOUT_MS = 65_000;
const VERSION_PROBE_TIMEOUT_MS = 10_000;
const NEGOTIATION_ID = "m0-approval-rpc-v2";
/** The single exact action this probe is willing to approve. */
const ALLOWED_EXACT_COMMAND = "true";
/** A second harmless command, used only to probe that a different command is denied. */
const DISTINCT_HARMLESS_COMMAND = "false";

type Decision = "allow" | "deny";
type DenyReason =
  | "additional_request"
  | "unexpected_ui_method"
  | "not_an_approval_prompt"
  | "prompt_missing_tool_line"
  | "prompt_tool_mismatch"
  | "prompt_missing_command_line"
  | "deny_scenario_policy"
  | "not_in_allowlist"
  | "no_structured_command_evidence"
  | "structured_command_mismatch"
  | "ambiguous_pending_calls"
  | "id_mismatch"
  | "missing_allow_label";

type ToolCallState = {
  toolCallId: string;
  toolName: string;
  /** Structured command from the runtime frame; null when the frame exposes none. */
  command: string | null;
  ended: boolean;
};

type ApprovalRequest = {
  approvalPrompt: boolean;
  /** True when the approval request itself carries a tool call id (not the case in v18.12.3). */
  requestCarriesToolCallId: boolean;
  toolCallIdMatched: boolean | null;
  /** The pending call's structured args exposed a command at all. */
  structuredCommandExposed: boolean;
  structuredCommandMatched: boolean;
  /** The prompt named the allowed command. */
  promptCommandMatched: boolean;
  /** The prompt named the command this scenario asked for (informational). */
  promptNamedScenarioCommand: boolean;
  /** Exactly one pending tool call existed when the request was decided. */
  uniquePendingCall: boolean;
  decision: Decision;
  reason: DenyReason | null;
  cancelledResponse: boolean;
};

type ApprovalRun = {
  promptId: string;
  scenario: "allow" | "deny";
  selectRequests: number;
  approvalPrompts: number;
  otherUiRequests: number;
  decisions: Decision[];
  requests: ApprovalRequest[];
  bashStarts: number;
  bashSuccesses: number;
  bashErrors: number;
  bashUnknownOutcomes: number;
  status: string;
  sessionSettled: boolean;
};

const child = Bun.spawn(
  [
    "rtk", "omp",
    "--mode", "rpc-ui",
    "--no-session",
    "--max-time=180s",
    "--approval-mode=always-ask",
    "--tools=bash",
  ],
  { cwd: process.cwd(), stdin: "pipe", stdout: "pipe", stderr: "pipe" },
);
void child.stderr.pipeTo(new WritableStream({ write() {} })).catch(() => {});
const reader = child.stdout.getReader();
const decoder = new TextDecoder();
let buffer = "";
let succeeded = false;
let currentStage = "startup";
const observedFrames: string[] = [];
const completedRuns: ApprovalRun[] = [];

function timeout<T>(promise: Promise<T>, label: string): Promise<T> {
  const { promise: timeoutPromise, reject } = Promise.withResolvers<never>();
  const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), RESPONSE_TIMEOUT_MS);
  return Promise.race([promise, timeoutPromise]).finally(() => clearTimeout(timer));
}

/** Protocol-level names only; never frame payloads. */
function safeName(value: unknown): string {
  if (typeof value !== "string") return "unknown";
  return /^[A-Za-z0-9_.-]{1,64}$/.test(value) ? value : "other";
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function nextFrame(): Promise<Record<string, unknown> | null> {
  while (true) {
    const newline = buffer.indexOf("\n");
    if (newline >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (!line.trim()) continue;
      const frame: unknown = JSON.parse(line);
      if (!isRecord(frame) || typeof frame.type !== "string") {
        throw new Error("Malformed RPC frame");
      }
      const parsed = frame;
      const method = typeof parsed.method === "string" ? `:${safeName(parsed.method)}` : "";
      const tool = typeof parsed.toolName === "string" ? `:${safeName(parsed.toolName)}` : "";
      const error = typeof parsed.isError === "boolean" ? `:error=${parsed.isError}` : "";
      observedFrames.push(`${parsed.type}${method}${tool}${error}`);
      return parsed;
    }

    const result = await timeout(reader.read(), "OMP RPC frame");
    if (result.done) return null;
    buffer += decoder.decode(result.value, { stream: true });
  }
}

async function send(frame: Record<string, unknown>): Promise<void> {
  await child.stdin.write(`${JSON.stringify(frame)}\n`);
  await child.stdin.flush();
}

async function responseFor(id: string): Promise<Record<string, unknown>> {
  while (true) {
    const frame = await nextFrame();
    if (!frame) throw new Error("OMP exited before RPC response");
    if (frame.type === "response" && frame.id === id) return frame;
  }
}

/** The structured command a runtime frame exposes for a tool call. */
function structuredCommand(args: unknown): string | null {
  if (typeof args !== "object" || args === null || Array.isArray(args)) return null;
  const command = (args as { command?: unknown }).command;
  return typeof command === "string" ? command : null;
}

/**
 * The approval prompt's own rows. OMP v18.12.3 renders them into the select request's
 * `title` text; no structured field carries the command.
 */
function parseApprovalTitle(title: unknown): { toolNames: string[]; commands: string[] } {
  if (typeof title !== "string") return { toolNames: [], commands: [] };
  const lines = title.split("\n");
  return {
    toolNames: lines.filter((line) => line.startsWith("Allow tool: ")).map((line) => line.slice("Allow tool: ".length)),
    commands: lines.filter((line) => line.startsWith("Command: ")).map((line) => line.slice("Command: ".length)),
  };
}

/** The call-scoped allow label. Session-scoped and always-allow labels are never chosen. */
function callScopedAllowLabel(options: readonly string[]): string | null {
  if (options.includes("Approve")) return "Approve";
  if (options.includes("Approve Once")) return "Approve Once";
  return null;
}

async function probeOmpVersion(): Promise<string> {
  const proc = Bun.spawn(["rtk", "omp", "--version"], { stdout: "pipe", stderr: "pipe" });
  const timer = setTimeout(() => proc.kill(), VERSION_PROBE_TIMEOUT_MS);
  try {
    const code = await proc.exited;
    if (code !== 0) return "unknown";
    const text = await new Response(proc.stdout).text();
    const match = /omp v(\d+\.\d+\.\d+)/.exec(text) ?? /v?(\d+\.\d+\.\d+)/.exec(text);
    return match?.[1] ?? "unknown";
  } catch {
    return "unknown";
  } finally {
    clearTimeout(timer);
  }
}

async function runApproval(
  promptId: string,
  scenario: "allow" | "deny",
  requestedCommand: string,
): Promise<ApprovalRun> {
  currentStage = `approval:${scenario}`;
  await send({
    id: promptId,
    type: "prompt",
    message:
      `Use the bash tool exactly once to run the command \`${requestedCommand}\`. ` +
      `Do not run any other command, do not combine it with other commands, and do not use any other tool.`,
  });

  const calls = new Map<string, ToolCallState>();
  let additionalRequestSeen = false;

  const run: ApprovalRun = {
    promptId,
    scenario,
    selectRequests: 0,
    approvalPrompts: 0,
    otherUiRequests: 0,
    decisions: [],
    requests: [],
    bashStarts: 0,
    bashSuccesses: 0,
    bashErrors: 0,
    bashUnknownOutcomes: 0,
    status: "unknown",
    sessionSettled: false,
  };

  const pendingCalls = (): ToolCallState[] => [...calls.values()].filter((call) => !call.ended);

  const recordToolStart = (frame: Record<string, unknown>): void => {
    const toolCallId = frame.toolCallId;
    if (typeof toolCallId !== "string") return;
    const toolName = safeName(frame.toolName);
    calls.set(toolCallId, {
      toolCallId,
      toolName,
      command: structuredCommand(frame.args),
      ended: false,
    });
    if (toolName === "bash") run.bashStarts += 1;
  };

  const recordToolEnd = (frame: Record<string, unknown>): void => {
    const toolCallId = frame.toolCallId;
    if (typeof toolCallId !== "string") return;
    const toolName = safeName(frame.toolName);
    const isError = typeof frame.isError === "boolean" ? frame.isError : null;
    const call = calls.get(toolCallId);
    if (call) call.ended = true;
    if (toolName === "bash") {
      if (isError === true) run.bashErrors += 1;
      else if (isError === false) run.bashSuccesses += 1;
      else run.bashUnknownOutcomes += 1;
    }
  };

  const respondValue = async (id: string, value: string): Promise<void> => {
    await send({ type: "extension_ui_response", id, value });
  };
  const respondCancelled = async (id: string): Promise<void> => {
    await send({ type: "extension_ui_response", id, cancelled: true });
  };

  const denyRequest = async (
    request: ApprovalRequest,
    id: string,
    reason: DenyReason,
    options: readonly string[],
  ): Promise<void> => {
    request.decision = "deny";
    request.reason = reason;
    run.decisions.push("deny");
    if (options.includes("Deny")) await respondValue(id, "Deny");
    else {
      request.cancelledResponse = true;
      await respondCancelled(id);
    }
  };

  /** A request that arrives while another one is still unresolved: deny it immediately. */
  const denyNestedRequest = async (frame: Record<string, unknown>): Promise<void> => {
    const id = frame.id;
    if (typeof id !== "string") return;
    run.selectRequests += 1;
    const options = Array.isArray(frame.options)
      ? frame.options.filter((option): option is string => typeof option === "string")
      : [];
    const request: ApprovalRequest = {
      approvalPrompt: true,
      requestCarriesToolCallId: typeof frame.toolCallId === "string",
      toolCallIdMatched: null,
      structuredCommandExposed: false,
      structuredCommandMatched: false,
      promptCommandMatched: false,
      promptNamedScenarioCommand: false,
      uniquePendingCall: false,
      decision: "deny",
      reason: "additional_request",
      cancelledResponse: false,
    };
    run.requests.push(request);
    await denyRequest(request, id, "additional_request", options);
  };

  /**
   * Reads frames until the pending tool call is announced. The runtime pushes
   * `tool_execution_start` before the call reaches the approval prompt, but the frame may
   * still be in flight when the prompt arrives.
   */
  const awaitToolCallFrame = async (): Promise<boolean> => {
    for (;;) {
      const frame = await nextFrame();
      if (!frame) throw new Error("OMP exited before the pending tool call was announced");
      if (frame.type === "extension_ui_request") {
        additionalRequestSeen = true;
        if (frame.method === "select") {
          await denyNestedRequest(frame);
        } else {
          run.otherUiRequests += 1;
          const id = frame.id;
          if (typeof id === "string") await respondCancelled(id);
        }
        continue;
      }
      if (frame.type === "tool_execution_start") {
        recordToolStart(frame);
        continue;
      }
      if (frame.type === "tool_execution_end") {
        recordToolEnd(frame);
        continue;
      }
      if (frame.type === "prompt_result" && frame.id === promptId) {
        run.status = typeof frame.status === "string" ? frame.status : "unknown";
        run.sessionSettled = frame.sessionSettled === true;
        return false;
      }
    }
  };

  const handleSelect = async (frame: Record<string, unknown>): Promise<void> => {
    const id = frame.id;
    if (typeof id !== "string") throw new Error("Approval UI request had no ID");
    run.selectRequests += 1;
    const options = Array.isArray(frame.options)
      ? frame.options.filter((option): option is string => typeof option === "string")
      : [];
    const { toolNames, commands } = parseApprovalTitle(frame.title);
    const isApprovalPrompt = toolNames.length > 0;
    if (isApprovalPrompt) run.approvalPrompts += 1;

    const request: ApprovalRequest = {
      approvalPrompt: isApprovalPrompt,
      requestCarriesToolCallId: typeof frame.toolCallId === "string",
      toolCallIdMatched: null,
      structuredCommandExposed: false,
      structuredCommandMatched: false,
      promptCommandMatched: false,
      promptNamedScenarioCommand: commands.length === 1 && commands[0] === requestedCommand,
      uniquePendingCall: false,
      decision: "deny",
      reason: null,
      cancelledResponse: false,
    };
    run.requests.push(request);

    observedFrames.push(
      `select:approval=${isApprovalPrompt}:options=${options.length}:allow=${callScopedAllowLabel(options) !== null}:deny=${options.includes("Deny")}:toolCallId=${request.requestCarriesToolCallId}`,
    );

    // 1. Only an approval prompt is ever eligible for approval.
    if (!isApprovalPrompt) {
      await denyRequest(request, id, "not_an_approval_prompt", options);
      return;
    }
    // 2. One approval per run: every additional request is denied.
    if (run.approvalPrompts > 1 || additionalRequestSeen) {
      await denyRequest(request, id, "additional_request", options);
      return;
    }
    // 3. The prompt must name exactly one tool, and it must be the allowed one.
    if (toolNames.length !== 1) {
      await denyRequest(request, id, "prompt_missing_tool_line", options);
      return;
    }
    if (toolNames[0] !== "bash") {
      await denyRequest(request, id, "prompt_tool_mismatch", options);
      return;
    }
    // 4. The prompt must name exactly one command.
    if (commands.length !== 1) {
      await denyRequest(request, id, "prompt_missing_command_line", options);
      return;
    }
    // Deny every request in the negative-control scenario, including an unexpected
    // `true`, so a model mismatch can never turn this scenario into an approved action.
    if (run.scenario === "deny") {
      await denyRequest(request, id, "deny_scenario_policy", options);
      return;
    }
    // 5. The command must be the single allowlisted exact action.
    if (commands[0] !== ALLOWED_EXACT_COMMAND) {
      await denyRequest(request, id, "not_in_allowlist", options);
      return;
    }
    request.promptCommandMatched = true;

    // 6. Structured runtime evidence for the pending call.
    if (calls.size === 0) {
      const announced = await awaitToolCallFrame();
      if (!announced || additionalRequestSeen) {
        await denyRequest(request, id, additionalRequestSeen ? "additional_request" : "no_structured_command_evidence", options);
        return;
      }
    }
    if (calls.size > 1) {
      await denyRequest(request, id, "ambiguous_pending_calls", options);
      return;
    }
    const candidates = pendingCalls();
    if (candidates.length !== 1) {
      await denyRequest(request, id, "ambiguous_pending_calls", options);
      return;
    }
    const candidate = candidates[0]!;
    request.uniquePendingCall = true;
    if (candidate.toolName !== "bash" || candidate.command === null) {
      await denyRequest(request, id, "no_structured_command_evidence", options);
      return;
    }
    request.structuredCommandExposed = true;
    if (candidate.command !== ALLOWED_EXACT_COMMAND) {
      await denyRequest(request, id, "structured_command_mismatch", options);
      return;
    }
    request.structuredCommandMatched = true;

    // 7. When the protocol carries a tool call id, it must match the pending call.
    if (request.requestCarriesToolCallId) {
      request.toolCallIdMatched = frame.toolCallId === candidate.toolCallId;
      if (!request.toolCallIdMatched) {
        await denyRequest(request, id, "id_mismatch", options);
        return;
      }
    }

    // 8. Only a call-scoped allow label may be used.
    const allowLabel = callScopedAllowLabel(options);
    if (!allowLabel) {
      await denyRequest(request, id, "missing_allow_label", options);
      return;
    }

    request.decision = "allow";
    request.reason = null;
    run.decisions.push("allow");
    await respondValue(id, allowLabel);
  };

  while (run.status === "unknown") {
    const frame = await nextFrame();
    if (!frame) throw new Error("OMP exited before the approval task settled");

    if (frame.type === "extension_ui_request") {
      if (frame.method === "select") {
        await handleSelect(frame);
        continue;
      }
      run.otherUiRequests += 1;
      const id = frame.id;
      if (typeof id === "string") await respondCancelled(id);
      continue;
    }
    if (frame.type === "tool_execution_start") {
      recordToolStart(frame);
      continue;
    }
    if (frame.type === "tool_execution_end") {
      recordToolEnd(frame);
      continue;
    }
    if (frame.type === "prompt_result" && frame.id === promptId) {
      run.status = typeof frame.status === "string" ? frame.status : "unknown";
      run.sessionSettled = frame.sessionSettled === true;
    }
  }

  if (!run.sessionSettled) {
    while (true) {
      const frame = await nextFrame();
      if (!frame) break;
      if (frame.type === "session_settled") {
        run.sessionSettled = true;
        break;
      }
    }
  }
  return run;
}

type ScenarioVerification = { verified: boolean; failures: string[] };

function verifyAllowScenario(run: ApprovalRun): ScenarioVerification {
  const failures: string[] = [];
  const approved = run.requests.filter((request) => request.decision === "allow");
  if (run.approvalPrompts < 1) failures.push("no approval prompt was observed");
  if (approved.length !== 1) failures.push(`expected exactly one approved request, observed ${approved.length}`);
  const request = approved[0];
  if (request) {
    if (!request.structuredCommandExposed) failures.push("no structured command evidence on the approved request");
    if (!request.structuredCommandMatched) failures.push("structured command evidence did not match the allowed command");
    if (!request.promptCommandMatched) failures.push("approval prompt did not name the allowed command");
    if (!request.uniquePendingCall) failures.push("approval request was not bound to a single pending tool call");
    if (request.requestCarriesToolCallId && request.toolCallIdMatched !== true) {
      failures.push("approval request carried a tool call id that did not match the pending call");
    }
  }
  if (run.bashSuccesses < 1) failures.push("the approved call produced no successful tool outcome");
  if (!run.sessionSettled) failures.push("session did not settle");
  return { verified: failures.length === 0, failures };
}

function verifyDenyScenario(run: ApprovalRun): ScenarioVerification {
  const failures: string[] = [];
  if (run.approvalPrompts < 1) failures.push("no approval prompt was observed");
  if (run.decisions.includes("allow")) failures.push("a request was approved inside the deny scenario");
  const intendedCommandDenied = run.requests.some(
    (request) => request.promptNamedScenarioCommand && request.decision === "deny",
  );
  if (!intendedCommandDenied) failures.push("the requested negative-control command was not observed and denied");
  if (run.bashSuccesses > 0) failures.push("a command executed despite the denial");
  if (!run.sessionSettled) failures.push("session did not settle");
  return { verified: failures.length === 0, failures };
}

function runSummary(run: ApprovalRun) {
  return {
    scenario: run.scenario,
    selectRequests: run.selectRequests,
    approvalPrompts: run.approvalPrompts,
    otherUiRequests: run.otherUiRequests,
    decisions: run.decisions,
    requestEvidence: run.requests.map((request) => ({
      approvalPrompt: request.approvalPrompt,
      requestCarriesToolCallId: request.requestCarriesToolCallId,
      toolCallIdMatched: request.toolCallIdMatched,
      structuredCommandExposed: request.structuredCommandExposed,
      structuredCommandMatched: request.structuredCommandMatched,
      promptCommandMatched: request.promptCommandMatched,
      promptNamedScenarioCommand: request.promptNamedScenarioCommand,
      uniquePendingCall: request.uniquePendingCall,
      decision: request.decision,
      reason: request.reason,
      cancelledResponse: request.cancelledResponse,
    })),
    bashStarts: run.bashStarts,
    bashSuccesses: run.bashSuccesses,
    bashErrors: run.bashErrors,
    bashUnknownOutcomes: run.bashUnknownOutcomes,
    status: run.status,
    sessionSettled: run.sessionSettled,
  };
}

try {
  currentStage = "ready";
  const ready = await nextFrame();
  if (!ready || ready.type !== "ready") throw new Error("OMP did not send its ready frame");
  if (!Array.isArray(ready.supportedProtocolVersions) || !ready.supportedProtocolVersions.includes(2)) {
    throw new Error("OMP does not advertise RPC protocol v2");
  }
  currentStage = "negotiate";
  await send({ id: NEGOTIATION_ID, type: "negotiate_protocol", protocolVersion: 2 });
  const negotiated = await responseFor(NEGOTIATION_ID);
  if (negotiated.success !== true) throw new Error("OMP RPC v2 negotiation failed");

  currentStage = "version";
  const ompVersion = await probeOmpVersion();

  currentStage = "approval:allow";
  const allowed = await runApproval("m0-approval-allow", "allow", ALLOWED_EXACT_COMMAND);
  completedRuns.push(allowed);
  currentStage = "approval:deny";
  const denied = await runApproval("m0-approval-deny", "deny", DISTINCT_HARMLESS_COMMAND);
  completedRuns.push(denied);

  const allowVerification = verifyAllowScenario(allowed);
  const denyVerification = verifyDenyScenario(denied);
  const approved = allowed.requests.filter((request) => request.decision === "allow");
  const approvedRequest = approved[0];
  const idBindingProven =
    approvedRequest?.requestCarriesToolCallId === true && approvedRequest.toolCallIdMatched === true;

  console.log(JSON.stringify({
    approvalSmoke: allowVerification.verified && denyVerification.verified ? "passed" : "failed",
    ompVersion,
    protocolV2Negotiated: true,
    mode: "rpc-ui",
    noUi: false,
    approvalMode: "always-ask",
    toolAllowlist: ["bash"],
    allowedExactCommand: ALLOWED_EXACT_COMMAND,
    approvalRequestShape: {
      method: "select",
      carriesToolCallId: approvedRequest?.requestCarriesToolCallId ?? false,
      carriesStructuredCommandField: false,
    },
    binding: {
      // The approval request carries no tool call id in OMP v18.12.3, so an id-level
      // binding cannot be proven; the smoke asserts that gap instead of inferring it.
      toolCallIdBindingProven: idBindingProven,
      promptAndToolCommandEvidenceAgree:
        approvedRequest?.structuredCommandMatched === true &&
        approvedRequest?.promptCommandMatched === true &&
        approvedRequest?.uniquePendingCall === true,
    },
    scenarios: {
      allow: { ...runSummary(allowed), verified: allowVerification.verified, failures: allowVerification.failures },
      deny: { ...runSummary(denied), verified: denyVerification.verified, failures: denyVerification.failures },
    },
    limitations: [
      "Approval requests carry no tool call id, so approval-to-call binding is positional plus command-content matched, never id-bound.",
      "Only the exact command `true` is ever approved; every other request is denied.",
      "Evidence covers one rpc-ui session with the bash tool only, and does not generalize to other tools, modes, or hosts.",
    ],
  }, null, 2));

  if (!allowVerification.verified || !denyVerification.verified) {
    throw new Error("Approval binding evidence was not demonstrated");
  }
  succeeded = true;
} catch {
  console.error(JSON.stringify({
    approvalSmoke: "failed",
    stage: currentStage,
    frames: observedFrames.slice(-40),
    runs: completedRuns.map(runSummary),
  }));
  process.exitCode = 1;
} finally {
  if (!succeeded) {
    child.kill();
  } else {
    child.stdin.end();
    const shutdown = Promise.withResolvers<void>();
    const timer = setTimeout(shutdown.resolve, 5_000);
    await Promise.race([child.exited, shutdown.promise]);
    clearTimeout(timer);
    if (child.exitCode === null) child.kill();
  }
}
