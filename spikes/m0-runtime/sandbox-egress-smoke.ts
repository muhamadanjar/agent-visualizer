import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, realpath, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  createServer as createHttpServer,
  request as httpRequest,
  type IncomingMessage,
  type Server as HttpServer,
  type ServerResponse,
} from "node:http";
import type { Socket } from "node:net";

const SANDBOX_TIMEOUT_MS = 15_000;
const BODY_LIMIT_BYTES = 1024 * 1024;
const LOCAL_PORT = 43871;
const MODEL_ID = "test-provider/test-model";
const REQUEST_TIMEOUT_MS = 5_000;
const SERVER_CLOSE_TIMEOUT_MS = 2_000;
const ALLOWED_PATHS: Record<string, true> = {
  "/provider/infer": true,
};
const TEST_SCOPED_TOKEN = randomUUID();

type RequestCounters = { forwarded: number; denied: number };

function listenHttp(server: HttpServer, port: number, hostname: string): Promise<void> {
  const { promise, resolve, reject } = Promise.withResolvers<void>();
  server.once("error", reject);
  server.listen(port, hostname, () => {
    server.off("error", reject);
    resolve();
  });
  return promise;
}

function listenUnix(server: HttpServer, path: string): Promise<void> {
  const { promise, resolve, reject } = Promise.withResolvers<void>();
  server.once("error", reject);
  server.listen(path, () => {
    server.off("error", reject);
    resolve();
  });
  return promise;
}

function trackConnections(server: HttpServer): Set<Socket> {
  const sockets = new Set<Socket>();
  server.on("connection", (socket: Socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  });
  return sockets;
}

function boundRequestLifetime(request: IncomingMessage): void {
  const timeout = setTimeout(() => request.destroy(), REQUEST_TIMEOUT_MS);
  request.once("close", () => clearTimeout(timeout));
}

async function close(server: HttpServer | null, sockets: Set<Socket>): Promise<void> {
  if (!server) return;
  const closed = Promise.withResolvers<void>();
  if (server.listening) {
    server.close((error) => error ? closed.reject(error) : closed.resolve());
  } else {
    closed.resolve();
  }
  for (const socket of sockets) socket.destroy();

  const timeout = Promise.withResolvers<null>();
  const timeoutHandle = setTimeout(() => timeout.resolve(null), SERVER_CLOSE_TIMEOUT_MS);
  try {
    const result = await Promise.race([closed.promise.then(() => true), timeout.promise]);
    if (result === null) throw new Error("http_server_shutdown_timed_out");
  } finally {
    clearTimeout(timeoutHandle);
  }
}

async function readRequestBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.byteLength;
    if (size > BODY_LIMIT_BYTES) throw new Error("request_body_too_large");
    chunks.push(bytes);
  }
  return Buffer.concat(chunks, size);
}

function modelFromBody(body: Buffer): unknown {
  const parsed: unknown = JSON.parse(body.toString("utf8"));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || !("model" in parsed)) {
    throw new Error("request_model_missing");
  }
  return parsed.model;
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "content-type": "application/json", connection: "close" });
  response.end(JSON.stringify(body));
}

async function main(): Promise<void> {
  const bwrap = Bun.which("bwrap");
  if (!bwrap) throw new Error("bubblewrap_unavailable");

  const tempRoot = await mkdtemp(join("/tmp", "m0-egress-smoke-"));
  const bridgeDir = join(tempRoot, "bridge");
  const socketPath = join(bridgeDir, "egress.sock");
  const scopedTokenPath = join(tempRoot, "scoped-token");
  const bunPath = await realpath(process.execPath);
  let gateway: HttpServer | null = null;
  let egress: HttpServer | null = null;
  let child: Bun.Subprocess | null = null;
  let gatewaySockets = new Set<Socket>();
  let egressSockets = new Set<Socket>();
  const counters: RequestCounters = { forwarded: 0, denied: 0 };
  let gatewayRequests = 0;

  try {
    await mkdir(bridgeDir, { mode: 0o700 });
    await writeFile(scopedTokenPath, TEST_SCOPED_TOKEN, { mode: 0o600 });

    gateway = createHttpServer((request, response) => {
      boundRequestLifetime(request);
      void (async () => {
        if (
          request.method !== "POST" ||
          !Object.hasOwn(ALLOWED_PATHS, request.url ?? "") ||
          request.headers.authorization !== `Bearer ${TEST_SCOPED_TOKEN}`
        ) {
          sendJson(response, 403, { error: "mock_gateway_denied" });
          request.resume();
          return;
        }
        try {
          const body = await readRequestBody(request);
          const model = modelFromBody(body);
          if (model !== MODEL_ID) {
            sendJson(response, 403, { error: "mock_gateway_denied" });
            return;
          }
          gatewayRequests += 1;
          sendJson(response, 200, { id: "mock-response", model: MODEL_ID, output: [] });
        } catch {
          sendJson(response, 400, { error: "mock_gateway_invalid_request" });
        }
      })().catch(() => {
        if (!response.headersSent) sendJson(response, 500, { error: "mock_gateway_failed" });
        else response.destroy();
      });
    });
    gatewaySockets = trackConnections(gateway);
    await listenHttp(gateway, 0, "127.0.0.1");
    const gatewayAddress = gateway.address();
    if (!gatewayAddress || typeof gatewayAddress === "string") throw new Error("mock_gateway_address_unavailable");

    egress = createHttpServer((request, response) => {
      boundRequestLifetime(request);
      void (async () => {
        const path = request.url ?? "";
        if (
          request.method !== "POST" ||
          !Object.hasOwn(ALLOWED_PATHS, path) ||
          request.headers.host !== `127.0.0.1:${LOCAL_PORT}`
        ) {
          counters.denied += 1;
          sendJson(response, 403, { error: "egress_policy_denied" });
          request.resume();
          return;
        }
        if (request.headers.authorization !== `Bearer ${TEST_SCOPED_TOKEN}`) {
          counters.denied += 1;
          sendJson(response, 401, { error: "scoped_client_denied" });
          request.resume();
          return;
        }

        let body: Buffer;
        try {
          body = await readRequestBody(request);
        } catch {
          counters.denied += 1;
          sendJson(response, 413, { error: "request_body_too_large" });
          return;
        }
        let model: unknown;
        try {
          model = modelFromBody(body);
        } catch {
          counters.denied += 1;
          sendJson(response, 400, { error: "invalid_json" });
          return;
        }
        if (model !== MODEL_ID) {
          counters.denied += 1;
          sendJson(response, 403, { error: "model_not_allowed" });
          return;
        }

        counters.forwarded += 1;
        const upstream = httpRequest({
          hostname: "127.0.0.1",
          port: gatewayAddress.port,
          method: request.method,
          path,
          headers: {
            authorization: `Bearer ${TEST_SCOPED_TOKEN}`,
            "content-type": "application/json",
            "content-length": String(body.byteLength),
            connection: "close",
          },
        }, (upstreamResponse) => {
          response.writeHead(upstreamResponse.statusCode ?? 502, {
            "content-type": upstreamResponse.headers["content-type"] ?? "application/json",
            connection: "close",
          });
          upstreamResponse.pipe(response);
        });
        upstream.on("error", () => sendJson(response, 502, { error: "mock_gateway_unavailable" }));
        upstream.end(body);
      })().catch(() => {
        if (!response.headersSent) sendJson(response, 500, { error: "egress_bridge_failed" });
        else response.destroy();
      });
    });
    egressSockets = trackConnections(egress);
    egress.on("connect", (_request, socket) => {
      counters.denied += 1;
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
    });
    await listenUnix(egress, socketPath);

    const sandboxProbe = `
import { createConnection, createServer } from "node:net";
const port = ${LOCAL_PORT};
const socketPath = "/run/egress/egress.sock";
const token = (await Bun.file("/run/scoped-token").text()).trim();
if (process.env.PROVIDER_API_KEY !== undefined || process.env.HOST_SECRET_CANARY !== undefined) {
  throw new Error("host_credential_environment_visible");
}
const routes = await Bun.file("/proc/net/route").text();
if (routes.split("\\n").slice(1).some((row) => row.trim().split(/\\s+/)[1] === "00000000")) {
  throw new Error("default_route_present");
}
const bridgeClients = new Set();
const bridge = createServer((client) => {
  bridgeClients.add(client);
  client.once("close", () => bridgeClients.delete(client));
  const upstream = createConnection({ path: socketPath });
  client.pipe(upstream);
  upstream.pipe(client);
  const closeBoth = () => { client.destroy(); upstream.destroy(); };
  client.on("error", closeBoth);
  upstream.on("error", closeBoth);
  client.on("close", () => upstream.destroy());
  upstream.on("close", () => client.destroy());
});
const bridgeReady = Promise.withResolvers();
bridge.once("error", bridgeReady.reject);
bridge.listen(port, "127.0.0.1", bridgeReady.resolve);
await bridgeReady.promise;
const send = async (path, model, auth = token) => {
  const response = await fetch("http://127.0.0.1:" + port + path, {
    method: "POST",
    headers: { authorization: "Bearer " + auth, "content-type": "application/json" },
    body: JSON.stringify({ model, input: "synthetic local-only probe" }),
    signal: AbortSignal.timeout(3000),
  });
  return response.status;
};
try {
  const allowed = await send("/provider/infer", ${JSON.stringify(MODEL_ID)});
  const deniedPath = await send("/provider/credentials", ${JSON.stringify(MODEL_ID)});
  const deniedModel = await send("/provider/infer", "test-provider/unapproved-model");
  const deniedToken = await send("/provider/infer", ${JSON.stringify(MODEL_ID)}, "not-a-scoped-token");
  const connectProbe = Promise.withResolvers();
  const connectSocket = createConnection({ host: "127.0.0.1", port });
  let connectData = "";
  const connectTimeout = setTimeout(() => { connectSocket.destroy(); connectProbe.reject(new Error("connect_probe_timeout")); }, 2000);
  connectSocket.on("connect", () => connectSocket.write("CONNECT model-gateway.invalid:443 HTTP/1.1\\r\\nHost: model-gateway.invalid:443\\r\\n\\r\\n"));
  connectSocket.on("data", (chunk) => { connectData += chunk.toString(); if (connectData.includes("\\r\\n\\r\\n")) { clearTimeout(connectTimeout); connectSocket.end(); connectProbe.resolve(connectData.split("\\r\\n", 1)[0]); } });
  connectSocket.on("error", (error) => { clearTimeout(connectTimeout); connectProbe.reject(error); });
  const deniedConnect = await connectProbe.promise;
  const directProbe = Promise.withResolvers();
  const directSocket = createConnection({ host: "1.1.1.1", port: 443 });
  const directTimeout = setTimeout(() => { directSocket.destroy(); directProbe.reject(new Error("direct_network_probe_timed_out")); }, 2000);
  directSocket.once("connect", () => { clearTimeout(directTimeout); directSocket.destroy(); directProbe.reject(new Error("direct_network_reachable")); });
  directSocket.once("error", () => { clearTimeout(directTimeout); directProbe.resolve(true); });
  const directNetworkBlocked = await directProbe.promise;
  if (allowed !== 200 || deniedPath !== 403 || deniedModel !== 403 || deniedToken !== 401 || deniedConnect !== "HTTP/1.1 403 Forbidden" || !directNetworkBlocked) {
    throw new Error("egress_policy_probe_failed");
  }
  console.log(JSON.stringify({
    result: "passed",
    allowedEndpoint: "POST /provider/infer",
    allowedModel: ${JSON.stringify(MODEL_ID)},
    deniedUnapprovedPath: deniedPath === 403,
    deniedUnapprovedModel: deniedModel === 403,
    deniedUnscopedToken: deniedToken === 401,
    deniedConnectTunnel: deniedConnect === "HTTP/1.1 403 Forbidden",
    directNetwork: "blocked",
    hostCredentialEnvironmentVisible: false,
    providerCall: false,
  }));
} finally {
  for (const client of bridgeClients) client.destroy();
  const bridgeClosed = Promise.withResolvers();
  bridge.close(bridgeClosed.resolve);
  await bridgeClosed.promise;
}
`;

    child = Bun.spawn([
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
      "--dir", "/opt",
      "--ro-bind", bunPath, "/opt/bun",
      "--dir", "/run",
      "--ro-bind", bridgeDir, "/run/egress",
      "--ro-bind", scopedTokenPath, "/run/scoped-token",
      "--dir", "/workspace",
      "--chdir", "/workspace",
      "--clearenv",
      "--setenv", "PATH", "/usr/bin:/opt",
      "--setenv", "HOME", "/workspace",
      "--",
      "/opt/bun", "-e", sandboxProbe,
    ], {
      env: { PROVIDER_API_KEY: "host-only-synthetic-canary", HOST_SECRET_CANARY: "host-only-synthetic-canary" },
      stdout: "pipe",
      stderr: "pipe",
      detached: true,
    });

    const timeout = Promise.withResolvers<null>();
    const timeoutHandle = setTimeout(() => timeout.resolve(null), SANDBOX_TIMEOUT_MS);
    const exitCode = await Promise.race([child.exited, timeout.promise]);
    clearTimeout(timeoutHandle);
    if (exitCode === null) {
      child.kill();
      await child.exited;
      const [stdout, stderr] = await Promise.all([
        new Response(child.stdout).text(),
        new Response(child.stderr).text(),
      ]);
      throw new Error(`sandbox_egress_probe_timed_out:${stdout.trim()}:${stderr.trim()}`);
    }
    const [stdout, stderr] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
    ]);
    if (exitCode !== 0 || stdout.trim().length === 0) {
      throw new Error(`sandbox_egress_probe_failed:${exitCode}:${stderr.trim()}`);
    }
    if (gatewayRequests !== 1 || counters.forwarded !== 1 || counters.denied !== 4) {
      throw new Error("unexpected_mock_gateway_request_counts");
    }
    console.log(JSON.stringify({
      sandbox: "bubblewrap",
      bridge: "read_only_unix_socket_to_loopback_mock_gateway",
      ...JSON.parse(stdout.trim()),
      mockGatewayForwardedRequests: gatewayRequests,
      deniedRequests: counters.denied,
      providerCall: false,
    }, null, 2));
  } finally {
    if (child && child.exitCode === null) {
      child.kill();
      await child.exited;
    }
    const shutdown = await Promise.allSettled([
      close(egress, egressSockets),
      close(gateway, gatewaySockets),
    ]);
    await rm(tempRoot, { recursive: true, force: true });
    if (shutdown.some((result) => result.status === "rejected")) {
      throw new Error("host_http_server_cleanup_failed");
    }
  }

  if (await Bun.file(tempRoot).exists()) throw new Error("temporary_egress_files_not_removed");
  console.log(JSON.stringify({ cleanup: "passed", temporaryBridgeAndTokenRemoved: true }));
}

main().catch((error) => {
  console.error(JSON.stringify({
    sandbox: "bubblewrap",
    result: "failed",
    reason: error instanceof Error ? error.message : "unknown_failure",
    providerCall: false,
  }));
  process.exitCode = 1;
});
