import { AuthGatewayClientTable, parseAuthGatewayClients } from "@oh-my-pi/pi-ai/auth-gateway";
import type { AuthGatewayClient } from "@oh-my-pi/pi-ai/auth-gateway";
import type { AuthStorage, Model } from "@oh-my-pi/pi-ai";
import { getBundledModels } from "@oh-my-pi/pi-catalog/models";
import * as chat from "@oh-my-pi/pi-ai/providers/openai-chat-server";
import { codexStream, POLICY } from "./policy";
import type { SafeTransportFailure } from "./transport";

const json = (status: number, value: unknown) => Response.json(value, { status });
type FailureStage = "gateway_setup" | "provider_request" | "upstream_request" | "upstream_response";
const safeUpstreamStatus = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isInteger(value) && value >= 100 && value <= 599 ? value : undefined;
const error = (
  status: number,
  upstreamStatus?: number,
  failureStage?: FailureStage,
  transportFailure?: SafeTransportFailure,
) => {
  const headers = new Headers({ "content-type": "application/json" });
  if (upstreamStatus !== undefined) headers.set("x-agent-office-codex-upstream-status", String(upstreamStatus));
  if (failureStage !== undefined) headers.set("x-agent-office-codex-failure-stage", failureStage);
  if (transportFailure !== undefined) headers.set("x-agent-office-codex-transport-failure", transportFailure);
  return new Response(JSON.stringify({ error: { message: "Codex gateway request failed", type: "gateway_error" } }),
    { status, headers });
};

async function readBody(req: Request, signal: AbortSignal) {
  const reader = req.body?.getReader();
  if (!reader) throw new Error("Body required");
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", abort, { once: true });
  let size = 0, text = "";
  const decoder = new TextDecoder();
  try {
    while (true) {
      signal.throwIfAborted();
      const next = await reader.read();
      signal.throwIfAborted();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > 16 * 1024 * 1024) throw new Error("Body limit");
      text += decoder.decode(next.value, { stream: true });
    }
    return JSON.parse(text + decoder.decode());
  } finally { signal.removeEventListener("abort", abort); await reader.cancel().catch(() => {}); }
}

export function scopedClients(text: string): AuthGatewayClient[] {
  const clients = parseAuthGatewayClients(text);
  for (const client of clients) {
    const accounts = client.accounts;
    if (!accounts || Object.keys(accounts).length !== 1 || accounts["openai-codex"]?.length !== 1
      || !client.models?.length || client.models.some(id => !/^openai-codex\/(?:[A-Za-z0-9._-]+|\*)$/.test(id))) {
      throw new Error("Exactly one Codex account and explicit Codex model grants required");
    }
  }
  return clients;
}

/** Narrow Chat Completions gateway. No tool executor, dynamic model discovery,
 * arbitrary endpoint configuration, global bearer, or auth recovery resolver.
 * Tests inject storage, model and stream only through this in-process API.
 */
export function createGateway(
  storage: AuthStorage,
  loadClients: () => Promise<AuthGatewayClient[]>,
  models = getBundledModels("openai-codex").filter(m => m.api === "openai-codex-responses") as Model<"openai-codex-responses">[],
  stream = codexStream,
) {
  const clients = new AuthGatewayClientTable(storage.sessions, []);
  const catalogue = new Map(models.map(model => [`openai-codex/${model.id}`, model]));
  let active = 0;
  let reloading: Promise<void> | undefined;
  async function reloadClients() {
    // Serialize admission reloads so an older read cannot restore revoked grants.
    reloading ??= (async () => {
      try { clients.set(await loadClients()); }
      catch { clients.set([]); throw new Error("Client policy unavailable"); }
    })().finally(() => { reloading = undefined; });
    await reloading;
  }
  return {
    close() { clients.close(); },
    async handle(req: Request): Promise<Response> {
      let release: (() => void) | undefined;
      let controller: AbortController | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let counted = false;
      let upstreamStatus: number | undefined;
      let failureStage: FailureStage = "gateway_setup";
      let transportFailure: SafeTransportFailure | undefined;
      try {
        // Reload/revoke before each admission; unreadable/invalid config denies all.
        try { await reloadClients(); } catch { return error(503); }
        const authorization = req.headers.get("authorization") ?? "";
        if (!/^Bearer [!-~]+$/.test(authorization)) return error(401);
        const client = clients.authenticate(authorization.slice(7));
        if (!client) return error(401);
        release = client.retain();
        const url = new URL(req.url);
        if (url.search) return error(400);
        if (req.method === "GET" && url.pathname === "/healthz") {
          const model = [...catalogue.values()].find(item => client.allows(item));
          if (!model) return error(503);
          await storage.credentials.reload();
          const credential = await storage.keys.getWithCredential(
            "openai-codex",
            client.credentialSessionId("openai-codex", crypto.randomUUID()),
            { baseUrl: "https://chatgpt.com/backend-api", modelId: model.id, signal: req.signal },
          );
          return credential?.apiKey ? json(200, POLICY) : error(503);
        }
        if (req.method === "GET" && url.pathname === "/v1/models") {
          return json(200, { object: "list", data: [...catalogue].filter(([, m]) => client.allows(m))
            .map(([id]) => ({ id, object: "model", owned_by: "openai-codex" })) });
        }
        if (req.method !== "POST" || url.pathname !== "/v1/chat/completions") return error(404);
        if (active >= 1) return error(429);
        active++; counted = true;
        controller = new AbortController();
        const abort = () => controller!.abort();
        req.signal.addEventListener("abort", abort, { once: true, signal: controller.signal });
        if (req.signal.aborted) controller.abort();
        timer = setTimeout(abort, 120_000);
        const body = await readBody(req, controller.signal);
        // Clients cannot configure routing, transport or provider option hooks.
        if (!body || typeof body !== "object" || ["route", "base_url", "provider", "extra_body",
          "codexSseMaxAttempts", "preferWebsockets"].some(key => key in body)) return error(400);
        const parsed = chat.parseRequest(body);
        if (parsed.stream) return error(400);
        const model = catalogue.get(parsed.modelId);
        if (!model || !client.allows(model)) return error(403);
        await storage.credentials.reload();
        // The admitted client has a ONE-account restriction. Resolve once,
        // before any inference; never refresh/rotate in response to inference failure.
        const key = await storage.keys.getWithCredential("openai-codex",
          client.credentialSessionId("openai-codex", crypto.randomUUID()),
          { baseUrl: "https://chatgpt.com/backend-api", modelId: model.id, signal: controller.signal });
        if (!key?.apiKey || controller.signal.aborted) return error(503);
        const options = parsed.options;
        failureStage = "provider_request";
        const events = stream(model, parsed.context, {
          apiKey: key.apiKey, oauthIdentity: key.oauthIdentity,
          maxTokens: Math.min(options.maxOutputTokens ?? 1024, 8192),
          toolChoice: options.toolChoice && typeof options.toolChoice === "object" && !("type" in options.toolChoice)
            ? { type: "tool", name: options.toolChoice.name } : options.toolChoice,
          reasoning: options.reasoning,
          disableReasoning: options.disableReasoning,
        }, controller, status => {
          upstreamStatus ??= safeUpstreamStatus(status);
          failureStage = "upstream_response";
        }, () => {
          failureStage = "upstream_request";
        }, failure => {
          transportFailure ??= failure;
          failureStage = "upstream_request";
        });
        // Current Runner uses non-streaming Chat Completions. Buffer the bounded
        // provider result so failures never expose raw upstream error envelopes.
        // Streaming is refused explicitly rather than pretending to support it.
        for await (const event of events) {
          if (event && typeof event === "object" && "error" in event) {
            const eventError = event.error;
            if (eventError && typeof eventError === "object" && "errorStatus" in eventError) {
              upstreamStatus ??= safeUpstreamStatus(eventError.errorStatus);
            }
          }
        }
        const result = await events.result();
        upstreamStatus ??= safeUpstreamStatus(result.errorStatus);
        if (result.stopReason !== "stop" && result.stopReason !== "toolUse") {
          return error(502, upstreamStatus, failureStage, transportFailure);
        }
        return json(200, chat.encodeResponse(result, parsed.modelId));
      } catch {
        return error(controller?.signal.aborted ? 504 : 502, upstreamStatus, failureStage, transportFailure);
      }
      finally {
        if (timer) clearTimeout(timer);
        controller?.abort();
        if (counted) active--;
        release?.();
      }
    },
  };
}
