const RESPONSE_TIMEOUT_MS = 20_000;
const NEGOTIATION_ID = "m0-abort-negotiate";
const PROMPT_ID = "m0-abort-prompt";
const ABORT_ID = "m0-abort-request";

const child = Bun.spawn(
  ["rtk", "omp", "--mode", "rpc", "--no-session", "--no-ui", "--tools=read", "--max-time=60s"],
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

try {
  const ready = await nextFrame();
  if (!ready || ready.type !== "ready") throw new Error("OMP did not send its ready frame");
  if (!Array.isArray(ready.supportedProtocolVersions) || !ready.supportedProtocolVersions.includes(2)) {
    throw new Error("OMP does not advertise RPC protocol v2");
  }

  await send({ id: NEGOTIATION_ID, type: "negotiate_protocol", protocolVersion: 2 });
  const negotiated = await responseFor(NEGOTIATION_ID);
  if (negotiated.success !== true) throw new Error("OMP RPC v2 negotiation failed");

  await send({
    id: PROMPT_ID,
    type: "prompt",
    message: "Read PRD.md, then provide a detailed summary of the functional requirements. Do not use tools other than read and do not modify anything.",
  });

  let agentStarted = false;
  let promptResult: Record<string, any> | null = null;
  while (!agentStarted && !promptResult) {
    const frame = await nextFrame();
    if (!frame) throw new Error("OMP exited before the prompt started");
    if (frame.type === "agent_start") agentStarted = true;
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) promptResult = frame;
  }
  if (!agentStarted || promptResult) throw new Error("Read-only task settled before cancellation could be requested");

  await send({ id: ABORT_ID, type: "abort" });
  let abortResponse: Record<string, any> | null = null;
  let settledEvent = false;
  while (!abortResponse || !promptResult || (promptResult.sessionSettled !== true && !settledEvent)) {
    const frame = await nextFrame();
    if (!frame) break;
    if (frame.type === "response" && frame.id === ABORT_ID) abortResponse = frame;
    if (frame.type === "prompt_result" && frame.id === PROMPT_ID) promptResult = frame;
    if (frame.type === "session_settled") settledEvent = true;
  }

  if (abortResponse?.success !== true) throw new Error("OMP did not acknowledge the abort request successfully");
  if (promptResult?.status !== "aborted") throw new Error(`Expected aborted prompt result, received ${promptResult?.status ?? "none"}`);
  const sessionSettled = promptResult.sessionSettled === true || settledEvent;
  if (!sessionSettled) throw new Error("Session settlement was not observed after abort");

  console.log(JSON.stringify({
    protocolV2Negotiated: true,
    toolsEnabled: ["read"],
    agentStarted,
    abortAcknowledged: abortResponse.success,
    promptStatus: promptResult.status,
    sessionSettled,
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
