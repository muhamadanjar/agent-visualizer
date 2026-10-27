import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

const REQUEST_TIMEOUT_MS = 30_000;
const REQUEST_BODY_LIMIT = 64 * 1024;
const RESPONSE_BODY_LIMIT = 1024 * 1024;
const MAX_COMPLETION_TOKENS = 64;
const MOCK_TOKEN = "m0-local-mock-only";
const DEFAULT_PROVIDER_MODEL_ID = "openrouter/cohere/north-mini-code:free";

type Completion = {
  model: string;
  content: string;
  requestId: string | null;
};

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

function validateBaseUrl(raw: string): URL {
  const url = new URL(raw);
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error("Provider URL must use HTTP or HTTPS");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("Provider URL must not contain credentials, query, or fragment");
  }
  const localHttp = url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !localHttp) {
    throw new Error("Non-local provider endpoints must use HTTPS");
  }
  const basePath = url.pathname.replace(/\/+$/, "") || "/v1";
  url.pathname = `${basePath}/chat/completions`;
  return url;
}

function rejectUnsafeProviderUrls(): number {
  const unsafeUrls = [
    "http://provider.example/v1",
    "https://user:secret@provider.example/v1",
    "https://provider.example/v1?token=secret",
  ];
  for (const candidate of unsafeUrls) {
    let rejected = false;
    try {
      validateBaseUrl(candidate);
    } catch {
      rejected = true;
    }
    if (!rejected) throw new Error("Provider URL validation accepted an unsafe URL");
  }
  return unsafeUrls.length;
}

async function readBoundedJson(response: Response): Promise<Record<string, unknown>> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > RESPONSE_BODY_LIMIT) {
    await response.body?.cancel();
    throw new Error("Provider response exceeds the 1 MiB limit");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Provider response has no body");

  const chunks: Uint8Array[] = [];
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > RESPONSE_BODY_LIMIT) {
      await reader.cancel();
      throw new Error("Provider response exceeds the 1 MiB limit");
    }
    chunks.push(value);
  }
  const body = Buffer.concat(chunks).toString("utf8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    throw new Error("Provider returned invalid JSON");
  }
  return requireRecord(parsed, "Provider response");
}

async function requestCompletion(baseUrl: string, apiKey: string, model: string): Promise<Completion> {
  const endpoint = validateBaseUrl(baseUrl);
  if (!apiKey.trim()) throw new Error("Provider API key is required");
  if (!model.trim() || model.length > 256) throw new Error("Provider model ID must contain 1–256 characters");

  const response = await fetch(endpoint, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: "system", content: "Reply with exactly: m0-ok" },
        { role: "user", content: "Return the requested token." },
      ],
      max_tokens: MAX_COMPLETION_TOKENS,
      temperature: 0,
      stream: false,
    }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new Error(`Provider request failed with HTTP ${response.status}`);
  }

  const payload = await readBoundedJson(response);
  const choices = payload.choices;
  if (!Array.isArray(choices) || choices.length !== 1) {
    throw new Error("Provider response must contain exactly one choice");
  }
  const message = requireRecord(requireRecord(choices[0], "Provider choice").message, "Provider message");
  if (typeof message.content !== "string" || message.content.length > 4096) {
    throw new Error("Provider response content is missing or exceeds 4096 characters");
  }
  const returnedModel = payload.model;
  if (typeof returnedModel !== "string" || returnedModel.length > 256) {
    throw new Error("Provider response model is missing or invalid");
  }
  const rawRequestId = response.headers.get("x-request-id") ?? payload.id;
  return {
    model: returnedModel,
    content: message.content.trim(),
    requestId: typeof rawRequestId === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(rawRequestId)
      ? rawRequestId
      : null,
  };
}

async function runMockSmoke(): Promise<void> {
  const unsafeUrlCasesRejected = rejectUnsafeProviderUrls();
  const rootPathMatched = validateBaseUrl("http://localhost:20128").pathname === "/v1/chat/completions";
  const versionedPathMatched = validateBaseUrl("http://localhost:20128/v1").pathname === "/v1/chat/completions";
  if (!rootPathMatched || !versionedPathMatched) {
    throw new Error("Local mock smoke did not normalize both provider base URL forms");
  }
  let requestCount = 0;
  const server = createServer(async (request, response) => {
    requestCount += 1;
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of request) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += buffer.byteLength;
      if (size > REQUEST_BODY_LIMIT) {
        response.writeHead(413).end();
        return;
      }
      chunks.push(buffer);
    }
    const body = requireRecord(JSON.parse(Buffer.concat(chunks).toString("utf8")), "Request");
    const messages = body.messages;
    const valid = request.method === "POST"
      && request.url === "/v1/chat/completions"
      && request.headers.authorization === `Bearer ${MOCK_TOKEN}`
      && body.model === "openrouter/cohere/north-mini-code:free"
      && body.max_tokens === MAX_COMPLETION_TOKENS
      && body.stream === false
      && Array.isArray(messages)
      && messages.length === 2;
    if (!valid) {
      response.writeHead(400).end();
      return;
    }
    response.writeHead(200, { "content-type": "application/json", "x-request-id": "m0-mock-request-1" });
    response.end(JSON.stringify({
      id: "m0-mock-response-1",
      model: "openrouter/cohere/north-mini-code:free",
      choices: [{ index: 0, message: { role: "assistant", content: "m0-ok" }, finish_reason: "stop" }],
    }));
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    const address = server.address() as AddressInfo;
    const childProcess = Bun.spawn([process.execPath, "run", import.meta.path, "--provider"], {
      env: {
        ...process.env,
        M0_PROVIDER_ALLOW_REAL_REQUEST: "I_ACKNOWLEDGE_PROVIDER_COST",
        M0_PROVIDER_BASE_URL: "",
        M0_PROVIDER_API_KEY: "",
        M0_PROVIDER_MODEL_ID: "",
        NINE_ROUTER_BASE_URL: "",
        NINE_ROUTER_API_KEY: "",
        M0_ROUTER_MODEL_ID: "",
        NINEROUTER_URL: `http://127.0.0.1:${address.port}`,
        NINEROUTER_KEY: MOCK_TOKEN,
      },
      stdout: "pipe",
      stderr: "pipe",
    });
    const stdoutPromise = new Response(childProcess.stdout).text();
    const stderrPromise = new Response(childProcess.stderr).text();
    const exitCode = await childProcess.exited;
    const stdout = await stdoutPromise;
    await stderrPromise;
    if (exitCode !== 0) {
      throw new Error("Local provider CLI rejected the NINEROUTER configuration");
    }
    const providerResult = requireRecord(JSON.parse(stdout.trim()), "Provider CLI result");
    if (
      providerResult.mode !== "provider"
      || providerResult.responseModel !== "openrouter/cohere/north-mini-code:free"
      || providerResult.completionMatched !== true
      || requestCount !== 1
    ) {
      throw new Error("Local provider CLI did not use the NINEROUTER settings for one correlated completion");
    }
    console.log(JSON.stringify({
      mode: "local-mock",
      requests: requestCount,
      correlated: true,
      completionMatched: true,
      rootAndVersionedBasePathsMatched: true,
      unsafeUrlCasesRejected,
    }));
  } finally {
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

async function runProviderSmoke(): Promise<void> {
  if (process.env.M0_PROVIDER_ALLOW_REAL_REQUEST !== "I_ACKNOWLEDGE_PROVIDER_COST") {
    throw new Error("Set M0_PROVIDER_ALLOW_REAL_REQUEST=I_ACKNOWLEDGE_PROVIDER_COST to opt in; a real provider may incur charges");
  }
  const baseUrl = process.env.M0_PROVIDER_BASE_URL || process.env.NINEROUTER_URL || process.env.NINE_ROUTER_BASE_URL || "http://localhost:20128";
  const apiKey = process.env.M0_PROVIDER_API_KEY || process.env.NINEROUTER_KEY || process.env.NINE_ROUTER_API_KEY;
  const model = process.env.M0_PROVIDER_MODEL_ID || process.env.M0_ROUTER_MODEL_ID || DEFAULT_PROVIDER_MODEL_ID;
  if (!apiKey) {
    throw new Error("Set M0_PROVIDER_API_KEY, NINEROUTER_KEY, or NINE_ROUTER_API_KEY");
  }
  const result = await requestCompletion(baseUrl, apiKey, model);
  if (result.content !== "m0-ok") {
    throw new Error("Provider did not return the required smoke response");
  }
  console.log(JSON.stringify({
    mode: "provider",
    requestId: result.requestId,
    responseModel: result.model,
    completionMatched: true,
    completionCharacters: result.content.length,
    requests: 1,
  }));
}

const mode = process.argv[2];
if (mode === "--self-test") {
  await runMockSmoke();
} else if (mode === "--provider") {
  await runProviderSmoke();
} else {
  console.error("Usage: bun run spikes/m0-runtime/provider-completions-smoke.ts --self-test|--provider");
  process.exitCode = 2;
}
