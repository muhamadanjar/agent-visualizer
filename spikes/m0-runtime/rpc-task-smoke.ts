const RESPONSE_TIMEOUT_MS = 60_000;
const PROMPT_ID = "m0-read-only-smoke";
const NEGOTIATION_ID = "m0-rpc-v2";

const child = Bun.spawn(
  ["rtk", "omp", "--mode", "rpc", "--no-session", "--no-ui", "--tools=read", "--max-time=60s"],
  { cwd: process.cwd(), stdin: "pipe", stdout: "pipe", stderr: "pipe" },
);
void child.stderr.pipeTo(new WritableStream({ write() {} })).catch(() => {});
const reader = child.stdout.getReader();
const decoder = new TextDecoder();
let buffer = "";
let succeeded = false;

function timeout<T>(promise: Promise<T>, label: string): Promise<T> {
  const { promise: timeoutPromise, reject } = Promise.withResolvers<never>();
  const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${label}`)), RESPONSE_TIMEOUT_MS);
  return Promise.race([promise, timeoutPromise]).finally(() => clearTimeout(timer));
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

    const result = await timeout(reader.read(), "an OMP RPC frame");
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

try {
  const ready = await nextFrame();
  if (!ready || ready.type !== "ready") throw new Error("OMP did not send its ready frame");
  if (!Array.isArray(ready.supportedProtocolVersions) || !ready.supportedProtocolVersions.includes(2)) {
    throw new Error("OMP does not advertise RPC protocol v2");
  }

  await send({ id: NEGOTIATION_ID, type: "negotiate_protocol", protocolVersion: 2 });
  const negotiated = await responseFor(NEGOTIATION_ID);
  if (!negotiated.success) throw new Error("OMP RPC v2 negotiation failed");

  await send({
    id: PROMPT_ID,
    type: "prompt",
    message: "Use the read tool to read PRD.md and return only its first Markdown heading. Do not modify files.",
  });

  const eventTypes = new Set<string>();
  const toolNames = new Set<string>();
  let toolExecutionCount = 0;
  let readToolEndCount = 0;
  let promptResult: Record<string, any> | null = null;
  let unexpectedToolStarts = 0;
  function recordToolFrame(frame: Record<string, any>): void {
    if (frame.type === "tool_execution_start") {
      toolExecutionCount += 1;
      if (typeof frame.toolName !== "string" || frame.toolName !== "read") unexpectedToolStarts += 1;
      if (typeof frame.toolName === "string") toolNames.add(frame.toolName);
    }
    if (frame.type === "tool_execution_end" && frame.toolName === "read") readToolEndCount += 1;
  }
  while (!promptResult) {
    const frame = await nextFrame();
    if (!frame) throw new Error("OMP exited before the prompt result");
    eventTypes.add(frame.type);
    recordToolFrame(frame);
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) promptResult = frame;
  }

  let sessionSettledEvent = false;
  if (promptResult.sessionSettled !== true) {
    while (!sessionSettledEvent) {
      const frame = await nextFrame();
      if (!frame) break;
      eventTypes.add(frame.type);
      recordToolFrame(frame);
      sessionSettledEvent = frame.type === "session_settled";
    }
  }

  if (promptResult.agentInvoked !== true || promptResult.status !== "completed") {
    throw new Error(`Unexpected task result: agentInvoked=${promptResult.agentInvoked}, status=${promptResult.status}`);
  }
  if (toolExecutionCount === 0 || readToolEndCount === 0 || unexpectedToolStarts > 0 || toolNames.size !== 1 || !toolNames.has("read")) {
    throw new Error("The read-only task used no read tool or invoked a tool outside the read-only allowlist");
  }
  if (promptResult.sessionSettled !== true && !sessionSettledEvent) {
    throw new Error("The completed task did not provide evidence that the session settled");
  }
  console.log(JSON.stringify({
    protocolVersions: ready.supportedProtocolVersions,
    protocolV2Negotiated: negotiated.success,
    promptId: PROMPT_ID,
    agentInvoked: promptResult.agentInvoked,
    status: promptResult.status,
    sessionSettled: promptResult.sessionSettled === true || sessionSettledEvent,
    sessionSettledVia: promptResult.sessionSettled === true ? "prompt_result" : sessionSettledEvent ? "session_settled" : "not observed",
    eventTypes: [...eventTypes].sort(),
    toolExecutionCount,
    toolNames: [...toolNames].sort(),
    unexpectedToolStarts,
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
