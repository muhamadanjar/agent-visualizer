const RESPONSE_TIMEOUT_MS = 10_000;
const NEGOTIATION_ID = "m0-health-negotiate";
const STATE_ID = "m0-health-state";

const child = Bun.spawn(
  ["rtk", "omp", "--mode", "rpc", "--no-session", "--no-ui", "--tools=read", "--max-time=60s"],
  { cwd: process.cwd(), stdin: "pipe", stdout: "pipe", stderr: "pipe" },
);
void child.stderr.pipeTo(new WritableStream({ write() {} })).catch(() => {});
const reader = child.stdout.getReader();
const decoder = new TextDecoder();
let buffer = "";
let succeeded = false;

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

    const result = await Promise.race([
      reader.read(),
      Bun.sleep(RESPONSE_TIMEOUT_MS).then(() => { throw new Error("Timed out waiting for an OMP RPC frame"); }),
    ]);
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

  await send({ id: STATE_ID, type: "get_state" });
  const response = await responseFor(STATE_ID);
  const state = response.data;
  if (response.success !== true || !state || typeof state !== "object") {
    throw new Error("OMP get_state did not return a successful state payload");
  }
  if (typeof state.isSettled !== "boolean" || typeof state.hasPendingAsyncWork !== "boolean") {
    throw new Error("OMP get_state omitted the documented settlement fields");
  }

  const keys = Object.keys(state);
  console.log(JSON.stringify({
    protocolV2Negotiated: true,
    stateResponseReceived: true,
    isSettled: state.isSettled,
    hasPendingAsyncWork: state.hasPendingAsyncWork,
    heartbeatFieldPresent: keys.some((key) => /heartbeat|last.?seen|health/i.test(key)),
  }, null, 2));
  succeeded = true;
} finally {
  if (!succeeded) {
    child.kill();
  } else {
    child.stdin.end();
    const exitCode = await Promise.race([child.exited, Bun.sleep(5_000).then(() => null)]);
    if (exitCode === null) child.kill();
  }
}
