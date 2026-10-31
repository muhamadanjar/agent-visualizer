import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { streamOpenAICodexResponses } from "@oh-my-pi/pi-ai/providers/openai-codex-responses";
import type { OpenAICodexResponsesOptions } from "@oh-my-pi/pi-ai/providers/openai-codex-responses";
import type { Context, Model } from "@oh-my-pi/pi-ai";
import { requestOnce, type SafeTransportFailure } from "./transport";

export const POLICY = Object.freeze({
  policy: "agent-office-codex-single-attempt-v1",
  ompVersion: "18.12.3",
  maxUpstreamAttempts: 1,
  websockets: false,
});
export const UPSTREAM = "https://chatgpt.com/backend-api/codex/responses";

// Fail on an accidental upgrade, before constructing a listener or touching auth.
export function assertPinnedDependencies() {
  for (const name of ["@oh-my-pi/pi-ai", "@oh-my-pi/pi-catalog"]) {
    const entry = import.meta.resolve(name);
    const path = entry.startsWith("file:") ? fileURLToPath(entry) : entry;
    const manifest = JSON.parse(readFileSync(resolve(dirname(path), "../package.json"), "utf8"));
    if (manifest.name !== name || manifest.version !== POLICY.ompVersion) throw new Error("Unreviewed OMP version");
  }
}

class UpstreamHttpError extends Error {
  constructor(readonly status: number) {
    super("Upstream inference rejected");
    this.name = "UpstreamHttpError";
  }
}

/** One budget per inbound completion, spent BEFORE fetch, even if it throws.
 * This also blocks provider-internal SSE replay/recovery paths not governed by
 * codexSseMaxAttempts. No global monkeypatch and no mutation of vendor files.
 */
export function singleAttemptFetch(
  controller: AbortController,
  upstream = UPSTREAM,
  transport = requestOnce,
  onUpstreamStatus?: (status: number) => void,
  onUpstreamStart?: () => void,
  onTransportFailure?: (failure: SafeTransportFailure) => void,
) {
  let spent = false;
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = input instanceof Request ? input.url : String(input);
    if (spent || url !== upstream || init?.method !== "POST" || controller.signal.aborted) {
      controller.abort();
      throw new Error("Upstream attempt refused");
    }
    spent = true;
    onUpstreamStart?.();
    try {
      const response = await transport(input, {
        ...init, redirect: "manual", keepalive: false,
        signal: init?.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal,
      }, onTransportFailure);
      onUpstreamStatus?.(response.status);
      if (!response.ok) {
        await response.body?.cancel();
        throw new UpstreamHttpError(response.status);
      }
      if (!response.body) {
        controller.abort();
        throw new Error("Upstream response body missing");
      }
      // Bound decoded response bytes and stop recovery after transport errors.
      let size = 0;
      const reader = response.body.getReader();
      const body = new ReadableStream<Uint8Array>({
        async pull(out) {
          try {
            const next = await reader.read();
            if (next.done) { out.close(); return; }
            size += next.value.byteLength;
            if (size > 8 * 1024 * 1024) throw new Error("Response limit");
            out.enqueue(next.value);
          } catch {
            controller.abort();
            await reader.cancel().catch(() => {});
            out.error(new Error("Upstream stream failed"));
          }
        },
        async cancel() { controller.abort(); await reader.cancel().catch(() => {}); },
      });
      return new Response(body, { status: response.status, headers: response.headers });
    } catch (error) {
      controller.abort();
      if (error instanceof UpstreamHttpError) throw error;
      throw new Error("Upstream inference failed");
    }
  };
}

export function codexStream(
  model: Model<"openai-codex-responses">,
  context: Context,
  options: OpenAICodexResponsesOptions,
  controller: AbortController,
  onUpstreamStatus?: (status: number) => void,
  onUpstreamStart?: () => void,
  onTransportFailure?: (failure: SafeTransportFailure) => void,
  // Only local smoke injects these; production does not accept endpoint config.
  upstream = UPSTREAM,
  transport = requestOnce,
) {
  return streamOpenAICodexResponses({
    ...model, baseUrl: upstream.replace(/\/responses$/, ""),
    preferWebsockets: false, headers: undefined,
  }, context, {
    ...options,
    // Direct provider call: no streamSimple auth resolver, account rotation,
    // empty-result retry, model fallback, or outer agent retry machinery.
    signal: controller.signal,
    preferWebsockets: false,
    codexSseMaxAttempts: 1,
    maxHttpAttempts: 1,
    fetch: singleAttemptFetch(controller, upstream, transport, onUpstreamStatus, onUpstreamStart, onTransportFailure),
    providerSessionState: undefined,
    onPayload: undefined,
    streamFirstEventTimeoutMs: 30_000,
    streamIdleTimeoutMs: 30_000,
  });
}
