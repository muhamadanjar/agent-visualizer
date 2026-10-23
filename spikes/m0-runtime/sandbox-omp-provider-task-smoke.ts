import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  createServer,
  request as httpRequest,
  type IncomingMessage,
  type Server,
  type ServerResponse,
  type ClientRequest,
} from "node:http";
import { createConnection, type Socket } from "node:net";
import { OmpRpcAdapter, type SafeRuntimeEvent } from "./omp-rpc-adapter";
import {
  OMP_PACKAGE_NAME,
  OMP_PACKAGE_VERSION,
  copyRunningFile,
  openAbsoluteDirectory,
  pathsOverlap,
  stageRuntimePackages,
} from "./sandbox-runtime";
import { isRecord } from "./runtime-types";

const DEFAULT_ROUTER_MODEL_ID = "cl/thinkingmachines/inkling-small:free";
const ROUTER_MODEL_ID = process.env.M0_ROUTER_MODEL_ID ?? DEFAULT_ROUTER_MODEL_ID;
const OMP_MODEL = `m0-router/${ROUTER_MODEL_ID}`;
const LOCAL_PORT = 43871;
const REQUEST_BODY_LIMIT = 131_072;
const UPSTREAM_RESPONSE_LIMIT = 1_048_576;
const REQUEST_TIMEOUT_MS = 65_000;
const MAX_PROVIDER_REQUESTS = 1;
const SYSTEM_MOUNTS = ["/usr", "/lib", "/lib64"];

function listenUnix(server: Server, path: string): Promise<void> {
  const { promise, resolve, reject } = Promise.withResolvers<void>();
  server.once("error", reject);
  server.listen(path, () => {
    server.off("error", reject);
    resolve();
  });
  return promise;
}

function trackConnections(server: Server): Set<Socket> {
  const sockets = new Set<Socket>();
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  });
  return sockets;
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "content-type": "application/json", connection: "close" });
  response.end(JSON.stringify(body));
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.byteLength;
    if (size > REQUEST_BODY_LIMIT) throw new Error("provider_request_too_large");
    chunks.push(bytes);
  }
  return Buffer.concat(chunks, size);
}

async function closeServer(server: Server | null, sockets: Set<Socket>): Promise<void> {
  if (!server) return;
  const closed = Promise.withResolvers<void>();
  if (server.listening) server.close((error) => error ? closed.reject(error) : closed.resolve());
  else closed.resolve();
  for (const socket of sockets) socket.destroy();
  const timer = setTimeout(() => closed.reject(new Error("provider_bridge_shutdown_timeout")), 2_000);
  try {
    await closed.promise;
  } finally {
    clearTimeout(timer);
  }
}

async function requireFreeModel(baseUrl: URL, apiKey: string): Promise<void> {
  const response = await fetch(new URL("/v1/models", baseUrl), {
    headers: { authorization: `Bearer ${apiKey}` },
    redirect: "error",
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok || !response.body) throw new Error("router_model_catalog_unavailable");
  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let size = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      size += result.value.byteLength;
      if (size > 2_097_152) {
        await reader.cancel();
        throw new Error("router_model_catalog_too_large");
      }
      chunks.push(Buffer.from(result.value));
    }
  } finally {
    reader.releaseLock();
  }
  const payload: unknown = JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
  const models = isRecord(payload) && Array.isArray(payload.data) ? payload.data : null;
  if (!models?.some((item) => isRecord(item) && item.id === ROUTER_MODEL_ID)) {
    throw new Error("free_router_model_not_listed");
  }
}

async function main(): Promise<void> {
  const bwrap = Bun.which("bwrap");
  const omp = Bun.which("omp");
  const home = process.env.HOME;
  const routerKey = process.env.NINE_ROUTER_API_KEY;
  const rawRouterUrl = process.env.NINE_ROUTER_BASE_URL;
  if (!bwrap || !omp || !home?.startsWith("/") || !routerKey || !rawRouterUrl) {
    throw new Error("bubblewrap_omp_or_local_ninerouter_credentials_unavailable");
  }

  if (
    process.env.M0_ROUTER_MODEL_ID !== undefined &&
    (!ROUTER_MODEL_ID.endsWith(":free") || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/.test(ROUTER_MODEL_ID))
  ) {
    throw new Error("model_override_must_be_safe_catalog_free_id");
  }
  const routerUrl = new URL(rawRouterUrl);
  if (
    routerUrl.protocol !== "http:" ||
    routerUrl.hostname !== "localhost" ||
    routerUrl.port !== "20128" ||
    !["", "/", "/v1", "/v1/"].includes(routerUrl.pathname) ||
    routerUrl.username || routerUrl.password || routerUrl.search || routerUrl.hash
  ) {
    throw new Error("local_router_endpoint_outside_approved_loopback_profile");
  }
  await requireFreeModel(routerUrl, routerKey);

  const bun = process.execPath;
  const installRoot = await realpath(join(home, ".bun", "install"));
  const packageRoot = await realpath(join(installRoot, "global", "node_modules"));
  const ompEntry = await realpath(omp);
  const expectedOmpEntry = await realpath(join(packageRoot, OMP_PACKAGE_NAME, "dist", "cli.js"));
  const hostHome = await realpath(home);
  const systemMountRoots = await Promise.all(SYSTEM_MOUNTS.map((path) => realpath(path)));
  if (
    systemMountRoots.some((root) => pathsOverlap(hostHome, root)) ||
    ompEntry !== expectedOmpEntry
  ) {
    throw new Error("unsupported_omp_install_layout");
  }

  const tempRoot = await mkdtemp(join("/tmp", "m0-omp-provider-task-"));
  const workspace = join(tempRoot, "workspace");
  const stagedNodeModules = join(tempRoot, "node_modules");
  const socketPath = join(tempRoot, "provider-egress.sock");
  const scopedToken = randomUUID();
  const buffer = new Uint8Array(64 * 1024);
  let egress: Server | null = null;
  let egressSockets = new Set<Socket>();
  let adapter: OmpRpcAdapter | null = null;
  let launchStderrText = "";
  let launchStderrBytes = 0;
  const counters = { forwarded: 0, denied: 0, upstreamStatus: 0 };
  const activeUpstreamRequests = new Set<ClientRequest>();

  try {
    await mkdir(workspace, { mode: 0o700 });
    await mkdir(join(workspace, ".omp", "agent"), { recursive: true, mode: 0o700 });
    await writeFile(join(workspace, ".omp", "agent", "models.yml"), [
      "providers:",
      "  m0-router:",
      `    baseUrl: http://127.0.0.1:${LOCAL_PORT}/v1`,
      "    api: openai-completions",
      "    apiKey: M0_ROUTER_BRIDGE_TOKEN",
      "    authHeader: true",
      "    models:",
      `      - id: ${ROUTER_MODEL_ID}`,
      "        name: M0 synthetic free-route smoke",
      "        supportsTools: false",
      "        reasoning: false",
      "        input: [text]",
      "        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }",
      "        contextWindow: 128000",
      "        maxTokens: 64",
      "",
    ].join("\n"), { mode: 0o600 });

    const packageRootHandle = await openAbsoluteDirectory(packageRoot);
    try {
      await stageRuntimePackages(packageRootHandle, stagedNodeModules, buffer);
    } finally {
      await packageRootHandle.close();
    }
    await copyRunningFile(bun, join(tempRoot, "bun"), buffer);

    egress = createServer((request, response) => {
      const timeout = setTimeout(() => request.destroy(), REQUEST_TIMEOUT_MS);
      request.once("close", () => clearTimeout(timeout));
      void (async () => {
        const path = request.url ?? "";
        if (
          request.method !== "POST" ||
          path !== "/v1/chat/completions" ||
          request.headers.host !== `127.0.0.1:${LOCAL_PORT}`
        ) {
          counters.denied += 1;
          sendJson(response, 403, { error: "provider_route_denied" });
          request.resume();
          return;
        }
        if (request.headers.authorization !== `Bearer ${scopedToken}`) {
          counters.denied += 1;
          sendJson(response, 401, { error: "scoped_bridge_token_denied" });
          request.resume();
          return;
        }

        let body: Buffer;
        try {
          body = await readBody(request);
        } catch {
          counters.denied += 1;
          sendJson(response, 413, { error: "request_body_limit" });
          return;
        }
        let payload: unknown;
        try {
          payload = JSON.parse(body.toString("utf8"));
        } catch {
          counters.denied += 1;
          sendJson(response, 400, { error: "invalid_provider_json" });
          return;
        }
        if (!isRecord(payload)) {
          counters.denied += 1;
          sendJson(response, 400, { error: "invalid_provider_json" });
          return;
        }
        const hasMaxTokens = Object.hasOwn(payload, "max_tokens");
        const hasMaxCompletionTokens = Object.hasOwn(payload, "max_completion_tokens");
        const tokenField = hasMaxCompletionTokens ? "max_completion_tokens" : "max_tokens";
        const requestedMaxTokens = payload[tokenField];
        if (
          !isRecord(payload) ||
          payload.model !== ROUTER_MODEL_ID ||
          payload.stream !== true ||
          !Array.isArray(payload.messages) ||
          hasMaxTokens === hasMaxCompletionTokens ||
          !Number.isSafeInteger(requestedMaxTokens) ||
          typeof requestedMaxTokens !== "number" ||
          requestedMaxTokens < 1 ||
          (Object.hasOwn(payload, "tools") && (!Array.isArray(payload.tools) || payload.tools.length !== 0)) ||
          (payload.tool_choice !== undefined && payload.tool_choice !== "none")
        ) {
          counters.denied += 1;
          sendJson(response, 403, { error: "provider_request_shape_denied" });
          return;
        }
        if (counters.forwarded >= MAX_PROVIDER_REQUESTS) {
          counters.denied += 1;
          sendJson(response, 429, { error: "provider_request_count_limit" });
          return;
        }
        const boundedBody = Buffer.from(JSON.stringify({
          model: ROUTER_MODEL_ID,
          messages: payload.messages,
          stream: true,
          [tokenField]: Math.min(requestedMaxTokens, 64),
        }));
        counters.forwarded += 1;

        const upstream = httpRequest({
          hostname: routerUrl.hostname,
          port: Number(routerUrl.port),
          method: "POST",
          path: "/v1/chat/completions",
          headers: {
            authorization: `Bearer ${routerKey}`,
            "content-type": "application/json",
            "content-length": String(boundedBody.byteLength),
            connection: "close",
          },
        }, (upstreamResponse) => {
          counters.upstreamStatus = upstreamResponse.statusCode ?? 502;
          response.writeHead(counters.upstreamStatus, {
            "content-type": upstreamResponse.headers["content-type"] ?? "application/json",
            connection: "close",
          });
          let responseBytes = 0;
          const failResponse = () => {
            if (!response.headersSent) sendJson(response, 502, { error: "local_router_stream_failed" });
            else response.destroy();
          };
          upstreamResponse.on("error", failResponse);
          upstreamResponse.on("aborted", failResponse);
          upstreamResponse.on("data", (chunk: Buffer) => {
            responseBytes += chunk.byteLength;
            if (responseBytes > UPSTREAM_RESPONSE_LIMIT) {
              upstream.destroy(new Error("router_response_too_large"));
              response.destroy();
            }
          });
          upstreamResponse.pipe(response);
        });
        activeUpstreamRequests.add(upstream);
        const deadline = setTimeout(() => upstream.destroy(new Error("router_absolute_timeout")), REQUEST_TIMEOUT_MS);
        upstream.once("close", () => {
          clearTimeout(deadline);
          activeUpstreamRequests.delete(upstream);
        });
        response.once("close", () => {
          if (!response.writableEnded) upstream.destroy(new Error("sandbox_client_disconnected"));
        });
        upstream.on("error", () => {
          if (!response.headersSent) sendJson(response, 502, { error: "local_router_unavailable" });
          else response.destroy();
        });
        upstream.end(boundedBody);
      })().catch(() => {
        if (!response.headersSent) sendJson(response, 500, { error: "provider_bridge_failed" });
        else response.destroy();
      });
    });
    egressSockets = trackConnections(egress);
    await listenUnix(egress, socketPath);

    const wrapper = `
import { createConnection, createServer } from "node:net";
const localPort = ${LOCAL_PORT};
const socketPath = "/run/provider/egress.sock";
if (process.env.NINE_ROUTER_API_KEY !== undefined || process.env.NINE_ROUTER_BASE_URL !== undefined) {
  throw new Error("host_router_credential_visible_in_sandbox");
}
const bridge = createServer((client) => {
  const upstream = createConnection({ path: socketPath });
  client.pipe(upstream);
  upstream.pipe(client);
  const closeBoth = () => { client.destroy(); upstream.destroy(); };
  client.on("error", closeBoth);
  upstream.on("error", closeBoth);
  client.on("close", () => upstream.destroy());
  upstream.on("close", () => client.destroy());
});
await new Promise((resolve, reject) => {
  bridge.once("error", reject);
  bridge.listen(localPort, "127.0.0.1", resolve);
});
const child = Bun.spawn([
  "/opt/bun",
  "/opt/node_modules/${OMP_PACKAGE_NAME}/dist/cli.js",
  "--mode", "rpc",
  "--no-session",
  "--no-ui",
  "--no-extensions",
  "--no-skills",
  "--no-rules",
  "--no-tools",
  "--model=${OMP_MODEL}",
  "--max-time=60s",
], { cwd: "/workspace", stdin: "pipe", stdout: "pipe", stderr: "pipe" });
let stderrText = "";
let stderrBytes = 0;
void child.stderr.pipeTo(new WritableStream({
  write(chunk) {
    stderrBytes += chunk.byteLength;
    if (stderrText.length < 8192) stderrText += new TextDecoder().decode(chunk);
  },
})).catch(() => {});
const stdoutPump = (async () => {
  const reader = child.stdout.getReader();
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) return;
      if (!process.stdout.write(result.value)) {
        await new Promise((resolve) => process.stdout.once("drain", resolve));
      }
    }
  } finally {
    reader.releaseLock();
  }
})();
const stdinPump = (async () => {
  try {
    for await (const chunk of process.stdin) {
      await child.stdin.write(chunk);
      await child.stdin.flush();
    }
  } finally {
    child.stdin.end();
  }
})();
const code = await child.exited;
process.stdin.destroy();
const stderrCategory = /unknown option|unrecognized option|invalid option/i.test(stderrText)
  ? "cli_argument_error"
  : /models\\.ya?ml|yaml|configuration|custom provider/i.test(stderrText)
  ? "provider_config_error"
  : /401|403|429|provider error/i.test(stderrText)
  ? "upstream_http_error"
  : /ECONNREFUSED|fetch failed|connect/i.test(stderrText)
  ? "provider_transport_error"
  : /model.*(not found|unavailable)|no model/i.test(stderrText)
  ? "model_selection_error"
  : stderrBytes > 0 ? "other_omp_stderr" : "no_omp_stderr";
await Bun.write("/workspace/omp-startup-diagnostic.json", JSON.stringify({ category: stderrCategory, bytes: stderrBytes }));
await Promise.allSettled([stdoutPump, stdinPump]);
await new Promise((resolve) => bridge.close(resolve));
process.exitCode = code ?? 1;
`;
    await writeFile(join(workspace, "provider-wrapper.ts"), wrapper, { mode: 0o600 });

    const bwrapArgs = [
      bwrap,
      "--unshare-all",
      "--die-with-parent",
      "--cap-drop", "ALL",
      "--ro-bind", "/usr", "/usr",
      "--ro-bind", "/lib", "/lib",
      "--ro-bind", "/lib64", "/lib64",
      "--dev", "/dev",
      "--proc", "/proc",
      "--tmpfs", "/tmp",
      "--dir", "/workspace",
      "--bind", workspace, "/workspace",
      "--dir", "/opt",
      "--ro-bind", join(tempRoot, "bun"), "/opt/bun",
      "--ro-bind", stagedNodeModules, "/opt/node_modules",
      "--dir", "/run",
      "--dir", "/run/provider",
      "--ro-bind", socketPath, "/run/provider/egress.sock",
      "--chdir", "/workspace",
      "--clearenv",
      "--setenv", "PATH", "/usr/bin:/opt",
      "--setenv", "HOME", "/workspace",
      "--setenv", "M0_ROUTER_BRIDGE_TOKEN", scopedToken,
      "--",
      "/opt/bun",
      "/workspace/provider-wrapper.ts",
    ];

    try {
      adapter = await OmpRpcAdapter.connect({
        launchCommand: bwrapArgs,
        launchEnv: { PATH: "/usr/bin" },
        launchStderr(chunk) {
          launchStderrBytes += chunk.byteLength;
          if (launchStderrText.length < 4096) launchStderrText += new TextDecoder().decode(chunk);
        },
      });
    } catch {
      const details: unknown = await Bun.file(join(workspace, "omp-startup-diagnostic.json")).json().catch(() => null);
      const sandboxCategory = isRecord(details) && typeof details.category === "string"
        ? details.category
        : launchStderrBytes > 0
        ? /permission denied|failed|mount|socket|not found/i.test(launchStderrText) ? "sandbox_launch_error" : "sandbox_stderr"
        : "diagnostic_unavailable";
      const stderrSummary = launchStderrText
        .replaceAll(routerKey, "[redacted]")
        .replaceAll(scopedToken, "[redacted]")
        .replaceAll(workspace, "/workspace")
        .replace(/\/tmp\/m0-omp-provider-task-[^\s:]*/g, "/tmp/[sandbox]")
        .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 800);
      throw new Error(`omp_start_failed:${sandboxCategory}:requests=${counters.forwarded}:upstream_status=${counters.upstreamStatus}:stderr=${stderrSummary || "empty"}`);
    }
    const events: SafeRuntimeEvent[] = [];
    adapter.onActivity((event) => events.push(event));
    const result = await adapter.startReadOnlyTask(
      "Return exactly this text and nothing else: PHASE1_PROVIDER_SMOKE_OK",
    );
    await adapter.close();
    adapter = null;

    const taskEvents = events.filter((event) => event.requestId === result.requestId);
    const agentStarted = taskEvents.some((event) => event.type === "agent_start");
    const agentEnded = taskEvents.some((event) => event.type === "agent_end");
    if (!result.requestId || !result.agentInvoked || result.status !== "completed" || !result.sessionSettled || !agentStarted || !agentEnded) {
      const eventTypes = taskEvents.map((event) => event.type);
      throw new Error(
        `provider_task_incomplete:invoked=${result.agentInvoked}:status=${result.status}:settled=${result.sessionSettled}:agent_started=${agentStarted}:agent_ended=${agentEnded}:events=${eventTypes.join(",")}:requests=${counters.forwarded}:denied=${counters.denied}:upstream_status=${counters.upstreamStatus}`,
      );
    }
    if (counters.forwarded !== 1 || counters.denied !== 0 || counters.upstreamStatus !== 200) {
      throw new Error("provider_route_policy_or_upstream_failed");
    }

    console.log(JSON.stringify({
      sandbox: "bubblewrap",
      runtime: `OMP ${OMP_PACKAGE_VERSION}`,
      providerModel: ROUTER_MODEL_ID,
      task: { requestId: result.requestId, invoked: result.agentInvoked, status: result.status, settled: result.sessionSettled },
      runtimeEvents: taskEvents.map((event) => event.type),
      providerRequests: counters.forwarded,
      deniedEgressRequests: counters.denied,
      upstreamStatus: counters.upstreamStatus,
      hostProviderCredentialMounted: false,
      network: "isolated-loopback-plus-exact-unix-socket-route",
      data: "synthetic_prompt_only_no_tools",
    }, null, 2));
  } finally {
    if (adapter) await adapter.close().catch(() => {});
    const outstandingUpstreamRequests = [...activeUpstreamRequests];
    for (const upstream of outstandingUpstreamRequests) upstream.destroy(new Error("smoke_cleanup"));
    await Promise.all(outstandingUpstreamRequests.map((upstream) => upstream.closed
      ? Promise.resolve()
      : new Promise<void>((resolve) => upstream.once("close", resolve))));
    await closeServer(egress, egressSockets).catch(() => {});
    await rm(tempRoot, { recursive: true, force: true });
  }
}

main().catch((error: unknown) => {
  console.error(JSON.stringify({
    sandbox: "bubblewrap",
    result: "failed",
    reason: error instanceof Error ? error.message : "unknown_failure",
  }));
  process.exitCode = 1;
});
