import { open } from "node:fs/promises";
import { constants } from "node:fs";
import { AuthStorage } from "@oh-my-pi/pi-ai";
import { AuthBrokerClient, RemoteAuthCredentialStore } from "@oh-my-pi/pi-ai/auth-broker";
import { createGateway, scopedClients } from "./gateway";
import { assertPinnedDependencies } from "./policy";

async function privateFile(path: string | undefined) {
  if (!path) throw new Error("Private file required");
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.uid !== process.getuid!() || (info.mode & 0o077) || info.size > 128 * 1024) {
      throw new Error("Private file permissions invalid");
    }
    const buffer = Buffer.alloc(128 * 1024 + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > 128 * 1024) throw new Error("Private file too large");
    return buffer.subarray(0, bytesRead).toString("utf8");
  } finally { await handle.close(); }
}

async function main() {
  assertPinnedDependencies();
  if (["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"].some(key => process.env[key])) {
    throw new Error("Proxy environment is not supported");
  }
  if (process.env.PI_CODEX_WEBSOCKET && !["0", "false", "FALSE"].includes(process.env.PI_CODEX_WEBSOCKET)) {
    throw new Error("WebSocket override is not supported");
  }
  const brokerToken = (await privateFile(process.env.OMP_AUTH_BROKER_TOKEN_FILE)).trim();
  if (!brokerToken || /\s/.test(brokerToken)) throw new Error("Broker configuration invalid");
  const client = new AuthBrokerClient({ url: "http://127.0.0.1:18765", token: brokerToken, maxRetries: 0, timeoutMs: 5000 });
  const initial = await client.fetchSnapshot();
  if (initial.status !== 200) throw new Error("Broker unavailable");
  const storage = new AuthStorage(new RemoteAuthCredentialStore({ client, initialSnapshot: initial.snapshot }));
  const loadClients = async () => scopedClients(await privateFile(process.env.OMP_GATEWAY_CLIENTS_FILE));
  await loadClients();
  const gateway = createGateway(storage, loadClients);
  const server = Bun.serve({ hostname: "127.0.0.1", port: 14000,
    maxRequestBodySize: 16 * 1024 * 1024, idleTimeout: 125,
    fetch: req => gateway.handle(req), error: () => Response.json({ error: "Gateway unavailable" }, { status: 503 }),
  });
  const stop = async () => { await server.stop(true); gateway.close(); storage.close(); process.exit(0); };
  process.on("SIGTERM", stop); process.on("SIGINT", stop);
}

// No raw exceptions, token values, profile dump, or provider payloads in logs.
main().catch(() => { console.error("Single-attempt gateway startup failed"); process.exit(1); });
