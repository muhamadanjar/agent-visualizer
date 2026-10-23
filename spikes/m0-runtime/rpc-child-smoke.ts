const RESPONSE_TIMEOUT_MS = 60_000;
const NEGOTIATION_ID = "m0-child-negotiate";
const SUBSCRIPTION_ID = "m0-child-subscription";
const PROMPT_ID = "m0-child-prompt";
const SUBAGENT_ID_PATTERN = /id|parent|depth|index|status|call/i;

const child = Bun.spawn(
  ["rtk", "omp", "--mode", "rpc", "--no-session", "--no-ui", "--tools=read,task", "--max-time=60s"],
  { cwd: process.cwd(), stdin: "pipe", stdout: "pipe", stderr: "pipe" },
);
void child.stderr.pipeTo(new WritableStream({ write() {} })).catch(() => {});
const reader = child.stdout.getReader();
const decoder = new TextDecoder();
let buffer = "";
let succeeded = false;

function withTimeout<T>(promise: Promise<T>, label: string): Promise<T> {
  const { promise: timeout, reject } = Promise.withResolvers<never>();
  const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), RESPONSE_TIMEOUT_MS);
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

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

async function responseFor(id: string): Promise<Record<string, any>> {
  while (true) {
    const frame = await nextFrame();
    if (!frame) throw new Error(`OMP exited before responding to ${id}`);
    if (frame.type === "response" && frame.id === id) return frame;
  }
}

function safeIds(frame: Record<string, any>): Record<string, string | number | boolean> {
  return Object.fromEntries(
    Object.entries(frame).filter(([key, value]) =>
      SUBAGENT_ID_PATTERN.test(key) && ["string", "number", "boolean"].includes(typeof value),
    ),
  );
}

try {
  const ready = await nextFrame();
  if (!ready || ready.type !== "ready") throw new Error("OMP did not send its ready frame");
  if (!Array.isArray(ready.supportedProtocolVersions) || !ready.supportedProtocolVersions.includes(2)) {
    throw new Error("OMP does not advertise RPC protocol v2");
  }

  await send({ id: NEGOTIATION_ID, type: "negotiate_protocol", protocolVersion: 2 });
  const negotiated = await responseFor(NEGOTIATION_ID);
  if (negotiated.success !== true) throw new Error("OMP RPC v2 negotiation failed");
  await send({ id: SUBSCRIPTION_ID, type: "set_subagent_subscription", level: "events" });
  const subscription = await responseFor(SUBSCRIPTION_ID);
  if (subscription.success !== true) throw new Error("OMP rejected the subagent subscription");

  await send({
    id: PROMPT_ID,
    type: "prompt",
    message: "Call the task tool exactly once with context stating this is a read-only child-scope test and one task item using agent `read-only-child`, task `Read PRD.md and report only its first Markdown heading`, and solutionSpace `fixed, single-file read-only task`. Do not create other children or run any non-read tool.",
  });

  const lifecycle: Array<Record<string, string | number | boolean>> = [];
  const childEventTypes = new Set<string>();
  const childToolNames = new Set<string>();
  const parentTaskCalls: Array<Record<string, string | number | boolean>> = [];
  let childToolStartCount = 0;
  let sessionSettled = false;
  let promptResult: Record<string, any> | null = null;
  while (!promptResult || (promptResult.sessionSettled !== true && !sessionSettled)) {
    const frame = await nextFrame();
    if (!frame) break;
    if (frame.type === "tool_execution_start" && frame.toolName === "task") {
      parentTaskCalls.push(safeIds(frame));
    }
    if (frame.type === "subagent_lifecycle") lifecycle.push(safeIds(frame.payload ?? {}));
    if (frame.type === "subagent_event") {
      const event = frame.payload?.event;
      childEventTypes.add(String(event?.type ?? frame.payload?.type ?? "unknown"));
      if (event?.type === "tool_execution_start") {
        childToolStartCount += 1;
        if (typeof event.toolName === "string") childToolNames.add(event.toolName);
      }
    }
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) promptResult = frame;
    if (frame.type === "session_settled") sessionSettled = true;
  }

  if (!promptResult || promptResult.status !== "completed" || !(promptResult.sessionSettled === true || sessionSettled)) {
    throw new Error(`Parent task did not complete and settle: status=${promptResult?.status ?? "missing"}`);
  }
  if (lifecycle.length === 0) throw new Error("No subagent lifecycle attribution was observed");
  const parentToolCallIds = new Set(
    parentTaskCalls.map((call) => call.toolCallId).filter((id): id is string => typeof id === "string"),
  );
  const childParentLinks = lifecycle
    .map((event) => event.parentToolCallId)
    .filter((id): id is string => typeof id === "string");
  const parentToolCallLinkObserved = childParentLinks.some((id) => parentToolCallIds.has(id));
  const childToolsOutsidePolicy = [...childToolNames].filter((name) => name !== "read");
  const childToolScopeVerified = childToolNames.size > 0 && childToolsOutsidePolicy.length === 0;
  if (!parentToolCallLinkObserved) throw new Error("Child lifecycle did not link to the parent's task tool call");
  if (!childToolScopeVerified) throw new Error(`Read-only child used unexpected tools: ${childToolsOutsidePolicy.join(",") || "none observed"}`);

  console.log(JSON.stringify({
    protocolV2Negotiated: true,
    toolsEnabled: ["read", "task"],
    parentPromptId: PROMPT_ID,
    parentStatus: promptResult.status,
    parentSettled: promptResult.sessionSettled === true || sessionSettled,
    childLifecycleFrames: lifecycle,
    childEventTypes: [...childEventTypes].sort(),
    childToolStartCount,
    childToolNames: [...childToolNames].sort(),
    parentToolCallLinkObserved,
    childToolScopeVerified,
    childToolsOutsideReadOnlyPolicy: childToolsOutsidePolicy,
  }, null, 2));
  succeeded = true;
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
