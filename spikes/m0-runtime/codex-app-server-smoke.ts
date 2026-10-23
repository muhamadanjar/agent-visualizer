import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { isRecord } from "./runtime-types";

const INITIALIZE_REQUEST_ID = 1;
const STARTUP_TIMEOUT_MS = 15_000;
const SHUTDOWN_TIMEOUT_MS = 2_000;
const MAX_FRAME_BYTES = 1_048_576;
const encoder = new TextEncoder();

function withTimeout<T>(promise: Promise<T>, label: string, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      timer = setTimeout(() => reject(new Error(label)), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

class JsonlReader {
  private readonly reader: ReadableStreamDefaultReader<Uint8Array>;
  private readonly decoder = new TextDecoder();
  private buffer = "";

  constructor(child: ReturnType<typeof Bun.spawn>) {
    this.reader = child.stdout.getReader();
  }

  async nextMessage(): Promise<Record<string, unknown>> {
    while (true) {
      const newline = this.buffer.indexOf("\n");
      if (newline >= 0) {
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        if (encoder.encode(line).byteLength > MAX_FRAME_BYTES) {
          throw new Error("codex_frame_too_large");
        }
        let value: unknown;
        try {
          value = JSON.parse(line);
        } catch {
          throw new Error("codex_invalid_jsonrpc_frame");
        }
        if (!isRecord(value)) throw new Error("codex_invalid_jsonrpc_message");
        return value;
      }
      if (encoder.encode(this.buffer).byteLength > MAX_FRAME_BYTES) {
        throw new Error("codex_frame_too_large");
      }
      const { done, value } = await this.reader.read();
      if (done) throw new Error("codex_server_closed_stdout");
      this.buffer += this.decoder.decode(value, { stream: true });
    }
  }

  async drain(): Promise<void> {
    try {
      while (!(await this.reader.read()).done) {
        // Drain and discard post-initialize frames; never persist server payloads.
      }
    } finally {
      this.reader.releaseLock();
    }
  }
}

async function nextInitializeResponse(
  reader: JsonlReader,
): Promise<Record<string, unknown>> {
  while (true) {
    const message = await reader.nextMessage();
    if (message.id === INITIALIZE_REQUEST_ID) return message;
  }
}

async function stopChild(child: ReturnType<typeof Bun.spawn>): Promise<void> {
  child.stdin.end();
  try {
    await withTimeout(child.exited, "codex_graceful_shutdown_timeout", SHUTDOWN_TIMEOUT_MS);
  } catch {
    if (child.exitCode === null) child.kill("SIGTERM");
    try {
      await withTimeout(child.exited, "codex_terminate_timeout", SHUTDOWN_TIMEOUT_MS);
    } catch {
      if (child.exitCode === null) child.kill("SIGKILL");
      await child.exited;
    }
  }
}

let stage = "preflight";
let failureStage = "preflight";
let failureReason = "unknown";

async function main(): Promise<void> {
  const codex = Bun.which("codex");
  if (!codex) throw new Error("codex_cli_unavailable");

  const tempRoot = await mkdtemp("/tmp/m0-codex-app-server-");
  const isolatedHome = join(tempRoot, "home");
  const codexHome = join(tempRoot, "codex-home");
  const cacheHome = join(tempRoot, "cache");
  const configHome = join(tempRoot, "config");
  const dataHome = join(tempRoot, "data");
  const tempDir = join(tempRoot, "tmp");
  let child: ReturnType<typeof Bun.spawn> | null = null;
  let stderrBytes = 0;
  let initialized = false;
  let childExitCode: number | null = null;
  let stderrDrain: Promise<void> | null = null;
  let stdoutDrain: Promise<void> | null = null;
  try {
    stage = "prepare_isolated_home";
    await Promise.all([
      mkdir(isolatedHome, { mode: 0o700 }),
      mkdir(codexHome, { mode: 0o700 }),
      mkdir(cacheHome, { mode: 0o700 }),
      mkdir(configHome, { mode: 0o700 }),
      mkdir(dataHome, { mode: 0o700 }),
      mkdir(tempDir, { mode: 0o700 }),
    ]);

    stage = "launch_app_server";
    child = Bun.spawn([codex, "app-server", "--stdio"], {
      cwd: tempRoot,
      env: {
        PATH: process.env.PATH ?? "/usr/bin:/bin",
        HOME: isolatedHome,
        CODEX_HOME: codexHome,
        XDG_CACHE_HOME: cacheHome,
        XDG_CONFIG_HOME: configHome,
        XDG_DATA_HOME: dataHome,
        TMPDIR: tempDir,
        LANG: "C.UTF-8",
      },
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    });
    const reader = new JsonlReader(child);
    stderrDrain = child.stderr.pipeTo(new WritableStream({
      write(chunk) {
        stderrBytes += chunk.byteLength;
      },
    })).catch(() => undefined);

    stage = "initialize_protocol";
    await child.stdin.write(`${JSON.stringify({
      id: INITIALIZE_REQUEST_ID,
      method: "initialize",
      params: {
        clientInfo: { name: "agent-office-m0-smoke", version: "0.1.0" },
        capabilities: { experimentalApi: false },
      },
    })}\n`);
    const response = await withTimeout(
      nextInitializeResponse(reader),
      "codex_initialize_timeout",
      STARTUP_TIMEOUT_MS,
    );
    if (response.error !== undefined || !isRecord(response.result)) {
      throw new Error("codex_initialize_rejected");
    }

    const result = response.result;
    if (
      typeof result.codexHome !== "string" ||
      typeof result.platformFamily !== "string" ||
      typeof result.platformOs !== "string" ||
      typeof result.userAgent !== "string" ||
      result.codexHome !== codexHome
    ) {
      throw new Error("codex_initialize_response_invalid");
    }

    stage = "send_initialized_notification";
    await child.stdin.write(`${JSON.stringify({ method: "initialized" })}\n`);
    initialized = true;
    stdoutDrain = reader.drain().catch(() => undefined);
  } catch (error) {
    failureStage = stage;
    failureReason = error instanceof Error && /^[a-z0-9_:-]+$/.test(error.message)
      ? error.message
      : "unexpected_error";
    throw error;
  } finally {
    if (child) {
      try {
        if (initialized) stage = "shutdown_app_server";
        await stopChild(child);
        childExitCode = child.exitCode;
        await Promise.all([stderrDrain, stdoutDrain]);
      } finally {
        await rm(tempRoot, { recursive: true, force: true });
      }
    } else {
      await rm(tempRoot, { recursive: true, force: true });
    }
  }

  console.log(JSON.stringify({
    candidate: "codex_app_server",
    initialized,
    isolatedCodexHome: true,
    taskRequestsSent: 0,
    modelTaskSubmitted: false,
    stderrBytes: Math.min(stderrBytes, Number.MAX_SAFE_INTEGER),
    childExitCode,
  }));
}

main().catch(() => {
  console.log(JSON.stringify({
    candidate: "codex_app_server",
    initialized: false,
    taskRequestsSent: 0,
    modelTaskSubmitted: false,
    stage: failureStage,
    result: failureReason,
  }));
  process.exitCode = 1;
});

