import { isRecord } from "./runtime-types";
type RpcFrame = Record<string, unknown> & { type: string };
type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
};

type AdapterState = "starting" | "ready" | "failed" | "closing" | "closed";

type SafeEventType =
  | "agent_start"
  | "agent_end"
  | "tool_execution_start"
  | "tool_execution_end"
  | "session_settled";

export type SafeRuntimeEvent = {
  requestId: string;
  attribution: "single-active-task-window";
  type: SafeEventType;
  toolName?: "read" | "other";
  isError?: boolean;
};

export type RunResult = {
  requestId: string;
  agentInvoked: boolean;
  status: string;
  sessionSettled: boolean;
  // True only when OMP acknowledged the abort before its terminal prompt result.
  // The abort response itself does not mean that the task has settled.
  cancelAcknowledged: boolean;
};

export type AdapterPhase = "starting" | "ready" | "active" | "failed" | "closing" | "closed";

export type CancelState = "none" | "requested" | "acknowledged" | "failed";

export type AdapterStatus = {
  phase: AdapterPhase;
  activeRequestId: string | null;
  cancelState: CancelState;
};

export type HealthReason =
  | "ok"
  | "adapter_not_ready"
  | "active_task"
  | "request_failed"
  | "request_timeout"
  | "invalid_state_payload";

// Point-in-time poll of OMP quiescence. This is not a heartbeat: it says nothing
// about liveness between polls, during a hung run, or after the reply was produced.
export type PointInTimeHealth = {
  checkedAt: string;
  responsive: boolean;
  heartbeat: false;
  isSettled: boolean | null;
  hasPendingAsyncWork: boolean | null;
  activeRequestId: string | null;
  reason: HealthReason;
};

// Acknowledgement that OMP accepted the abort command. It is not a terminal
// result: the task settles separately through the startReadOnlyTask promise.
export type CancelAcknowledgement = {
  requestId: string;
  acknowledged: true;
  acknowledgedAt: string;
};

type ActivityHandler = (event: SafeRuntimeEvent) => void;

type ActiveRun = {
  requestId: string;
  result: Deferred<RunResult>;
  promptResult?: Omit<RunResult, "requestId" | "sessionSettled" | "cancelAcknowledged"> & {
    sessionSettled: boolean;
  };
  sessionSettledSeen: boolean;
  cancelPromise?: Promise<CancelAcknowledgement>;
  cancelAcknowledged: boolean;
};

const READY_TIMEOUT_MS = 10_000;
const COMMAND_TIMEOUT_MS = 10_000;
const HEALTH_TIMEOUT_MS = 10_000;
const CANCEL_TIMEOUT_MS = 20_000;
const TASK_TIMEOUT_MS = 70_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const MAX_FRAME_BYTES = 1_048_576;
const encoder = new TextEncoder();


function safeStatus(value: unknown): string {
  if (typeof value !== "string" || value.length > 32 || !/^[a-zA-Z0-9_-]+$/.test(value)) {
    return "unknown";
  }
  return value;
}

function withTimeout<T>(promise: Promise<T>, label: string, timeoutMs: number): Promise<T> {
  const timeout = Promise.withResolvers<never>();
  const timer = setTimeout(() => timeout.reject(new Error(`Timed out waiting for ${label}`)), timeoutMs);
  return Promise.race([promise, timeout.promise]).finally(() => clearTimeout(timer));
}

export class OmpRpcAdapter {
  private readonly child: ReturnType<typeof Bun.spawn>;
  private readonly reader: ReadableStreamDefaultReader<Uint8Array>;
  private readonly decoder = new TextDecoder();
  private readonly readyFrame: Deferred<RpcFrame> = Promise.withResolvers<RpcFrame>();
  private readonly pendingResponses = new Map<string, Deferred<RpcFrame>>();
  private readonly pendingCancellationRequests = new Map<string, ActiveRun>();
  private readonly activityHandlers = new Set<ActivityHandler>();
  private buffer = "";
  private state: AdapterState = "starting";
  private failure: Error | null = null;
  private activeRun: ActiveRun | null = null;
  private cancelState: CancelState = "none";
  private startedTask = false;
  private readLoop: Promise<void>;

  private constructor(
    child: ReturnType<typeof Bun.spawn>,
    onStderr?: (chunk: Uint8Array) => void,
  ) {
    this.child = child;
    this.reader = child.stdout.getReader();
    void child.stderr.pipeTo(new WritableStream({
      write(chunk) {
        onStderr?.(chunk);
      },
    })).catch(() => {});
    this.readLoop = this.readOutput();
    void child.exited.then((code) => {
      if (this.state !== "closing" && this.state !== "closed" && this.state !== "failed") {
        this.fail(new Error(`OMP exited unexpectedly (${code})`));
      }
    });
  }

  static async connect(options: {
    cwd?: string;
    launchCommand?: string[];
    launchEnv?: Record<string, string>;
    launchStderr?: (chunk: Uint8Array) => void;
  } = {}): Promise<OmpRpcAdapter> {
    const executable = Bun.which("omp");
    const launchCommand = options.launchCommand ?? (executable
      ? [
          executable,
          "--mode", "rpc",
          "--no-session",
          "--no-ui",
          "--tools=read",
          "--max-time=60s",
        ]
      : null);
    if (!launchCommand?.length) throw new Error("OMP executable was not found on PATH");

    let adapter: OmpRpcAdapter | null = null;
    try {
      const child = Bun.spawn(launchCommand, {
        cwd: options.cwd ?? process.cwd(),
        ...(options.launchEnv ? { env: options.launchEnv } : {}),
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
      });
      adapter = new OmpRpcAdapter(child, options.launchStderr);
      const ready = await withTimeout(adapter.readyFrame.promise, "OMP ready frame", READY_TIMEOUT_MS);
      const versions = ready.supportedProtocolVersions;
      if (!Array.isArray(versions) || !versions.includes(2)) {
        throw new Error("OMP does not advertise RPC protocol v2");
      }

      const negotiation = await adapter.request(
        "negotiate_protocol",
        { protocolVersion: 2 },
        COMMAND_TIMEOUT_MS,
      );
      if (negotiation.success !== true) throw new Error("OMP RPC v2 negotiation failed");
      adapter.state = "ready";
      return adapter;
    } catch (error) {
      if (adapter) await adapter.close();
      if (error instanceof Error && error.message.startsWith("Timed out waiting for")) throw error;
      throw error instanceof Error ? error : new Error("Failed to start OMP RPC adapter");
    }
  }

  onActivity(handler: ActivityHandler): () => void {
    if (this.state === "closed" || this.state === "failed") {
      throw new Error("OMP RPC adapter is not active");
    }
    this.activityHandlers.add(handler);
    return () => this.activityHandlers.delete(handler);
  }

  async startReadOnlyTask(prompt: string): Promise<RunResult> {
    if (this.state !== "ready") throw new Error("OMP RPC adapter is not ready");
    if (this.startedTask) throw new Error("This adapter process accepts one task only");
    if (typeof prompt !== "string" || prompt.trim().length === 0 || prompt.length > 2_000) {
      throw new Error("Read-only task prompt must contain 1–2000 characters");
    }

    this.startedTask = true;
    const requestId = `m0-task-${crypto.randomUUID()}`;
    const activeRun: ActiveRun = {
      requestId,
      result: Promise.withResolvers<RunResult>(),
      sessionSettledSeen: false,
      cancelAcknowledged: false,
    };
    this.activeRun = activeRun;

    try {
      const response = await this.request(
        "prompt",
        { message: prompt },
        COMMAND_TIMEOUT_MS,
        requestId,
      );
      if (response.success !== true) throw new Error("OMP rejected the read-only task");

      const responseData = isRecord(response.data) ? response.data : null;
      if (responseData?.agentInvoked === false) {
        return {
          requestId,
          agentInvoked: false,
          status: "not_invoked",
          sessionSettled: true,
          cancelAcknowledged: false,
        };
      }

      return await withTimeout(activeRun.result.promise, "read-only task result", TASK_TIMEOUT_MS);
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error("Read-only task failed"));
      throw error instanceof Error ? error : new Error("Read-only task failed");
    } finally {
      if (this.activeRun === activeRun) this.activeRun = null;
    }
  }

  getStatus(): AdapterStatus {
    return {
      phase: this.currentPhase(),
      activeRequestId: this.activeRun?.requestId ?? null,
      cancelState: this.cancelState,
    };
  }

  // Bounded point-in-time poll of the OMP session state. It proves RPC
  // responsiveness at the instant of the reply and nothing else: no
  // liveness between polls, no hang detection, no heartbeat.
  async getRuntimeState(): Promise<PointInTimeHealth> {
    const checkedAt = new Date().toISOString();
    const base = { checkedAt, heartbeat: false as const, activeRequestId: this.activeRun?.requestId ?? null };
    const unavailable = (reason: HealthReason): PointInTimeHealth => ({
      ...base,
      responsive: false,
      isSettled: null,
      hasPendingAsyncWork: null,
      reason,
    });

    if (this.state !== "ready") return unavailable("adapter_not_ready");
    if (this.activeRun) return unavailable("active_task");

    try {
      const response = await this.request("get_state", {}, HEALTH_TIMEOUT_MS);
      if (response.success !== true) return unavailable("request_failed");
      const data = isRecord(response.data) ? response.data : null;
      if (!data || typeof data.isSettled !== "boolean" || typeof data.hasPendingAsyncWork !== "boolean") {
        return unavailable("invalid_state_payload");
      }
      return {
        ...base,
        responsive: true,
        isSettled: data.isSettled,
        hasPendingAsyncWork: data.hasPendingAsyncWork,
        reason: "ok",
      };
    } catch (error) {
      const timedOut = error instanceof Error && error.message.startsWith("Timed out waiting for");
      return unavailable(timedOut ? "request_timeout" : "request_failed");
    }
  }

  // Cancels the active read-only task. The promise resolves only after OMP
  // acknowledges the abort command; the terminal task settlement arrives
  // separately through the startReadOnlyTask promise.
  async cancelActiveTask(): Promise<CancelAcknowledgement> {
    if (this.state !== "ready") throw new Error("OMP RPC adapter is not ready");
    const run = this.activeRun;
    if (!run) throw new Error("There is no active task to cancel");
    if (run.cancelPromise) return run.cancelPromise;
    if (run.promptResult) throw new Error("The active task already reached a terminal result");

    this.cancelState = "requested";
    const cancelRequestId = `m0-abort-${crypto.randomUUID()}`;
    this.pendingCancellationRequests.set(cancelRequestId, run);
    run.cancelPromise = this.performCancel(run, cancelRequestId);
    return run.cancelPromise;
  }

  private async performCancel(run: ActiveRun, cancelRequestId: string): Promise<CancelAcknowledgement> {
    try {
      const response = await this.request("abort", {}, CANCEL_TIMEOUT_MS, cancelRequestId);
      if (response.success !== true) throw new Error("OMP did not acknowledge the abort request");
      return { requestId: run.requestId, acknowledged: true, acknowledgedAt: new Date().toISOString() };
    } catch (error) {
      this.cancelState = "failed";
      throw error;
    } finally {
      this.pendingCancellationRequests.delete(cancelRequestId);
    }
  }

  private currentPhase(): AdapterPhase {
    if (this.state === "ready") return this.activeRun ? "active" : "ready";
    return this.state;
  }

  async close(): Promise<void> {
    if (this.state === "closed") return;
    if (this.activeRun) this.fail(new Error("Adapter closed while a task was active"));
    if (this.state !== "failed") this.state = "closing";

    try {
      this.child.stdin.end();
    } catch {
      this.child.kill();
    }

    const exitCode = await Promise.race([
      this.child.exited,
      Bun.sleep(SHUTDOWN_TIMEOUT_MS).then(() => null),
    ]);
    if (exitCode === null) {
      this.child.kill();
      await this.child.exited;
    } else if (exitCode !== 0 && this.state !== "failed") {
      this.state = "closed";
      await this.readLoop.catch(() => {});
      throw new Error(`OMP exited with code ${exitCode}`);
    }
    this.state = "closed";
    await this.readLoop.catch(() => {});
  }

  private async request(
    type: string,
    payload: Record<string, unknown>,
    timeoutMs: number,
    id = `m0-${crypto.randomUUID()}`,
  ): Promise<RpcFrame> {
    if (this.state === "failed" || this.state === "closed" || this.state === "closing") {
      throw this.failure ?? new Error("OMP RPC adapter is not active");
    }
    const response = Promise.withResolvers<RpcFrame>();
    this.pendingResponses.set(id, response);
    try {
      await this.child.stdin.write(`${JSON.stringify({ id, type, ...payload })}\n`);
      await this.child.stdin.flush();
      return await withTimeout(response.promise, `${type} response`, timeoutMs);
    } finally {
      this.pendingResponses.delete(id);
    }
  }

  private async readOutput(): Promise<void> {
    try {
      while (true) {
        const result = await this.reader.read();
        if (result.done) {
          this.buffer += this.decoder.decode();
          if (this.buffer.trim().length > 0) throw new Error("OMP ended during an RPC frame");
          if (this.state !== "closing" && this.state !== "closed" && this.state !== "failed") {
            this.fail(new Error("OMP closed its RPC output unexpectedly"));
          }
          return;
        }
        this.buffer += this.decoder.decode(result.value, { stream: true });
        if (this.buffer.length > MAX_FRAME_BYTES && !this.buffer.includes("\n")) {
          throw new Error("OMP RPC frame exceeded the size limit");
        }
        await this.consumeLines();
      }
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error("Failed to read OMP RPC output"));
    }
  }

  private async consumeLines(): Promise<void> {
    while (true) {
      const newline = this.buffer.indexOf("\n");
      if (newline < 0) return;
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (!line.trim()) continue;
      if (encoder.encode(line).byteLength > MAX_FRAME_BYTES) {
        throw new Error("OMP RPC frame exceeded the size limit");
      }

      let value: unknown;
      try {
        value = JSON.parse(line);
      } catch {
        throw new Error("OMP emitted malformed RPC JSON");
      }
      if (!isRecord(value) || typeof value.type !== "string") {
        throw new Error("OMP emitted a malformed RPC frame");
      }
      if (value.type === "rpc_chunk") {
        throw new Error("Chunked RPC frames are outside this bounded M0 adapter");
      }
      this.consumeFrame(value as RpcFrame);
    }
  }

  private consumeFrame(frame: RpcFrame): void {
    if (frame.type === "ready") {
      this.readyFrame.resolve(frame);
      return;
    }

    if (frame.type === "response" && typeof frame.id === "string") {
      const cancellationRun = this.pendingCancellationRequests.get(frame.id);
      if (cancellationRun) {
        if (frame.success === true) {
          this.cancelState = "acknowledged";
          if (!cancellationRun.promptResult) cancellationRun.cancelAcknowledged = true;
        } else {
          this.cancelState = "failed";
        }
      }
      this.pendingResponses.get(frame.id)?.resolve(frame);
      return;
    }

    const run = this.activeRun;
    if (!run) return;

    if (frame.type === "prompt_result" && frame.id === run.requestId) {
      const agentInvoked = frame.agentInvoked === true;
      run.promptResult = {
        agentInvoked,
        status: safeStatus(frame.status),
        sessionSettled: frame.sessionSettled === true,
      };
      this.completeRunIfSettled(run);
      return;
    }

    if (frame.type === "session_settled") {
      run.sessionSettledSeen = true;
      this.emitActivity({
        requestId: run.requestId,
        attribution: "single-active-task-window",
        type: "session_settled",
      });
      this.completeRunIfSettled(run);
      return;
    }

    if (
      frame.type === "agent_start" ||
      frame.type === "agent_end" ||
      frame.type === "tool_execution_start" ||
      frame.type === "tool_execution_end"
    ) {
      const event: SafeRuntimeEvent = {
        requestId: run.requestId,
        attribution: "single-active-task-window",
        type: frame.type,
      };
      if (frame.type === "tool_execution_start" || frame.type === "tool_execution_end") {
        event.toolName = frame.toolName === "read" ? "read" : "other";
      }
      if (frame.type === "tool_execution_end" && typeof frame.isError === "boolean") {
        event.isError = frame.isError;
      }
      this.emitActivity(event);
    }
  }

  private completeRunIfSettled(run: ActiveRun): void {
    if (!run.promptResult) return;
    const sessionSettled = run.promptResult.sessionSettled || run.sessionSettledSeen;
    if (!sessionSettled) return;
    run.result.resolve({
      requestId: run.requestId,
      agentInvoked: run.promptResult.agentInvoked,
      status: run.promptResult.status,
      sessionSettled: true,
      cancelAcknowledged: run.cancelAcknowledged,
    });
  }

  private emitActivity(event: SafeRuntimeEvent): void {
    for (const handler of this.activityHandlers) {
      try {
        handler(event);
      } catch {
        this.fail(new Error("An activity subscriber failed"));
        return;
      }
    }
  }

  private fail(error: Error): void {
    if (this.state === "failed" || this.state === "closed") return;
    this.state = "failed";
    this.failure = error;
    this.readyFrame.reject(error);
    for (const pending of this.pendingResponses.values()) pending.reject(error);
    this.pendingResponses.clear();
    this.activeRun?.result.reject(error);
    this.child.kill();
  }
}
