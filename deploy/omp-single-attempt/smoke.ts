/** Focused local smoke only. No broker, profile, production token, or live host.
 * Run with a clean environment and HOME pointing to a disposable directory.
 */
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { getBundledModels } from "@oh-my-pi/pi-catalog/models";
import { hashAuthGatewayToken } from "@oh-my-pi/pi-ai/auth-gateway";
import { assertPinnedDependencies, codexStream, singleAttemptFetch } from "./policy";
import { createGateway, scopedClients } from "./gateway";
import { requestOnce } from "./transport";
import type { Model } from "@oh-my-pi/pi-ai";

assertPinnedDependencies();
const model = getBundledModels("openai-codex").find(m => m.api === "openai-codex-responses") as Model<"openai-codex-responses">;
assert.ok(model);
const context = { systemPrompt: ["Local smoke"], messages: [{ role: "user" as const, content: "ping", timestamp: 0 }] };
// Fabricated token, no signature or real credential. Provider only decodes identity.
const key = `fixture.${Buffer.from(JSON.stringify({ "https://api.openai.com/auth": { chatgpt_account_id: "fake" } })).toString("base64url")}.fixture`;
let mode = "success", requests = 0, upgrades = 0;
const server = createServer(async (req, res) => {
  requests++;
  assert.equal(req.url, "/codex/responses");
  for await (const _ of req) { /* consume fake request without printing it */ }
  if (mode === "reset") { req.socket.destroy(); return; }
  if (mode === "timeout") return;
  if (/^\d+$/.test(mode)) {
    res.writeHead(Number(mode), { location: "/must-not-follow", "retry-after": "0" }); res.end("rejected"); return;
  }
  res.writeHead(200, { "content-type": "text/event-stream" });
  if (mode === "truncated") { res.write("event: response.created\ndata: {}\n\n"); res.destroy(); return; }
  if (mode === "provider-failed") {
    res.end(`data: ${JSON.stringify({ type: "response.failed", response: { error: { code: "server_error", message: "fixture failure" } } })}\n\n`); return;
  }
  const item = { type: "message", id: "msg_fixture", role: "assistant", status: "completed",
    content: [{ type: "output_text", text: "ok", annotations: [] }] };
  const events = [
    { type: "response.output_item.added", output_index: 0, item: { ...item, content: [] } },
    { type: "response.output_text.delta", item_id: item.id, output_index: 0, content_index: 0, delta: "ok" },
    { type: "response.output_item.done", output_index: 0, item },
    { type: "response.completed", response: { id: "resp_fixture", status: "completed", output: [item],
      usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } } },
  ];
  res.end(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""));
});
server.on("upgrade", (_req, socket) => { upgrades++; socket.destroy(); });
await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
const address = server.address();
assert.ok(address && typeof address !== "string");
const upstream = `http://127.0.0.1:${address.port}/codex/responses`;
const localFetch: typeof requestOnce = (input, init, onTransportFailure) => {
  assert.equal(String(input), upstream, "smoke may never leave its fake upstream");
  return requestOnce(input, init, onTransportFailure);
};
try {
  for (const scenario of ["success", "401", "429", "503", "302", "reset", "truncated", "provider-failed", "timeout"]) {
    mode = scenario; requests = 0;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), scenario === "timeout" ? 50 : 5000);
    let responseStatus: number | undefined;
    const result = await codexStream(model, context, { apiKey: key, sessionId: "fixture", preferWebsockets: true,
      codexSseMaxAttempts: 6 }, controller, status => { responseStatus = status; }, undefined, undefined, upstream, localFetch).result();
    clearTimeout(timer);
    assert.equal(requests, 1, `${scenario}: exactly one received upstream request`);
    assert.equal(upgrades, 0, `${scenario}: no WebSocket upgrade`);
    if (scenario === "success") assert.equal(result.stopReason, "stop");
    else {
      assert.ok(["error", "aborted"].includes(result.stopReason), `${scenario}: must fail`);
      if (scenario === "401") assert.equal(responseStatus, 401);
    }
    console.log(`PASS ${scenario}: upstream=1 websocket=0`);
  }
  mode = "success"; requests = 0;
  const fetchRequest = new Request(upstream, { method: "POST", body: "fixture-body" });
  const fetchResponse = await requestOnce(fetchRequest);
  assert.equal(fetchResponse.status, 200);
  await fetchResponse.text();
  assert.equal(requests, 1, "a Request input with a streaming body is sent once");
  // Explicit replay attempt after an uncertain network exception cannot reset budget.
  let attempts = 0;
  const once = singleAttemptFetch(new AbortController(), upstream, async () => { attempts++; throw new Error("fixture reset"); });
  await assert.rejects(once(upstream, { method: "POST" }));
  await assert.rejects(once(upstream, { method: "POST" }));
  assert.equal(attempts, 1);
  const canceled = new AbortController(); canceled.abort();
  await assert.rejects(singleAttemptFetch(canceled, upstream, localFetch)(upstream, { method: "POST" }));

  let invalidRequestFailure: string | undefined;
  await assert.rejects(requestOnce(upstream, { method: "GET" }, failure => { invalidRequestFailure = failure; }));
  assert.equal(invalidRequestFailure, "invalid_request");

  // Exercise real Chat Completions translation and auth admission without a broker.
  const clientToken = "fixture-client";
  const grants = scopedClients(JSON.stringify({ fixture: { tokenSha256: hashAuthGatewayToken(clientToken),
    models: [`openai-codex/${model.id}`], accounts: { "openai-codex": ["email:fixture@example.invalid"] } } }));
  let resolutions = 0;
  const storage = { sessions: { restrict: () => ({}), unrestrict() {} }, credentials: { async reload() {} },
    keys: { async getWithCredential() { resolutions++; return { apiKey: key }; } } };
  const gateway = createGateway(storage as never, async () => grants, [model],
    (m, c, o, ctl, onStatus, onStart, onFailure) => codexStream(m, c, o, ctl, onStatus, onStart, onFailure, upstream, localFetch));
  const request = (body?: object, token = clientToken) => new Request("http://127.0.0.1/v1/chat/completions", {
    method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(body ?? { model: `openai-codex/${model.id}`, messages: [{ role: "user", content: "ping" }] }),
  });
  mode = "success"; requests = 0;
  assert.equal((await gateway.handle(request(undefined, "invalid"))).status, 401);
  const health = await gateway.handle(new Request("http://127.0.0.1/healthz", {
    headers: { authorization: `Bearer ${clientToken}` },
  }));
  assert.equal(health.status, 200);
  const policy = await health.json();
  assert.equal(policy.maxUpstreamAttempts, 1);
  assert.equal(policy.websockets, false);
  assert.equal((await gateway.handle(new Request("http://127.0.0.1/v1/usage", {
    headers: { authorization: `Bearer ${clientToken}` },
  }))).status, 404);
  assert.equal(resolutions, 1);
  resolutions = 0;
  assert.equal(requests, 0);
  assert.equal((await gateway.handle(request({ model: `openai-codex/${model.id}`, stream: true,
    messages: [{ role: "user", content: "ping" }] }))).status, 400);
  assert.equal(requests, 0);
  const reply = await gateway.handle(request());
  assert.equal(reply.status, 200);
  assert.equal((await reply.json()).choices[0].message.content, "ok");
  mode = "reset"; requests = 0; resolutions = 0;
  const resetReply = await gateway.handle(request());
  assert.ok([502, 504].includes(resetReply.status));
  assert.equal(resetReply.headers.get("x-agent-office-codex-failure-stage"), "upstream_request");
  assert.equal(resetReply.headers.get("x-agent-office-codex-transport-failure"), "network");
  assert.equal(requests, 1); assert.equal(resolutions, 1);
  mode = "401"; requests = 0; resolutions = 0;
  const rejected = await gateway.handle(request());
  assert.equal(rejected.status, 502);
  assert.equal(rejected.headers.get("x-agent-office-codex-upstream-status"), "401");
  assert.equal(rejected.headers.get("x-agent-office-codex-failure-stage"), "upstream_response");
  assert.equal((await rejected.text()).includes("rejected"), false);
  assert.equal(requests, 1); assert.equal(resolutions, 1);
  mode = "provider-failed"; requests = 0; resolutions = 0;
  const streamFailure = await gateway.handle(request());
  assert.equal(streamFailure.status, 502);
  assert.equal(streamFailure.headers.get("x-agent-office-codex-upstream-status"), "200");
  assert.equal((await streamFailure.text()).includes("fixture failure"), false);
  assert.equal(streamFailure.headers.get("x-agent-office-codex-failure-stage"), "upstream_response");
  assert.equal(requests, 1); assert.equal(resolutions, 1);
  gateway.close();
  console.log("PASS gateway auth, translation, streaming rejection, single credential resolve, uncertain-network failure");
} finally {
  server.closeAllConnections();
  await new Promise<void>(done => server.close(() => done()));
}
