import { expect, test } from "bun:test";
import { subscribeToActivity } from "../app/lib/activity.ts";

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function withFakeBrowser(run, { authStatus = 200 } = {}) {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const previousWebSocket = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
  const previousFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const sockets = [];

  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 1;
      sockets.push(this);
    }

    send() {}

    close(code = 1000) {
      this.readyState = 3;
      this.onclose?.({ code });
    }
  }

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { location: { protocol: "http:", host: "localhost" } },
  });
  Object.defineProperty(globalThis, "WebSocket", {
    configurable: true,
    value: FakeWebSocket,
  });
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async () => new Response(null, { status: authStatus }),
  });

  try {
    await run(sockets);
  } finally {
    if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
    else delete globalThis.window;
    if (previousWebSocket) Object.defineProperty(globalThis, "WebSocket", previousWebSocket);
    else delete globalThis.WebSocket;
    if (previousFetch) Object.defineProperty(globalThis, "fetch", previousFetch);
    else delete globalThis.fetch;
  }
}

test("an expired Owner session stops reconnect retries", async () => {
  await withFakeBrowser(async (sockets) => {
    const messages = [];
    const unsubscribe = subscribeToActivity((message) => messages.push(message));

    sockets[0].close(1008);
    await wait(2_100);

    expect(sockets).toHaveLength(1);
    expect(messages.at(-1)).toEqual({ type: "connection", state: "unavailable" });
    unsubscribe();
  });
});

test("a rejected WebSocket handshake stops retries when the Owner session is invalid", async () => {
  await withFakeBrowser(async (sockets) => {
    const messages = [];
    const unsubscribe = subscribeToActivity((message) => messages.push(message));

    sockets[0].close(1006);
    await wait(2_100);

    expect(sockets).toHaveLength(1);
    expect(messages.at(-1)).toEqual({ type: "connection", state: "unavailable" });
    unsubscribe();
  }, { authStatus: 401 });
});

test("a transient WebSocket close reconnects", async () => {
  await withFakeBrowser(async (sockets) => {
    const messages = [];
    const unsubscribe = subscribeToActivity((message) => messages.push(message));

    sockets[0].close(1006);
    await wait(2_100);

    expect(sockets).toHaveLength(2);
    expect(messages).toContainEqual({ type: "connection", state: "disconnected" });
    unsubscribe();
  });
});
