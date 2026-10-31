import * as http from "node:http";
import * as https from "node:https";
import { Readable } from "node:stream";

/** One ClientRequest, one connection, no pool, proxy, redirect or retry driver.
 * The caller's attempt guard is spent before entering this function. A network
 * exception is an uncertain result and never causes another request here.
 */

export type SafeTransportFailure = "timeout" | "tls" | "network" | "invalid_request" | "other";

function safeTransportFailure(error: unknown): SafeTransportFailure {
  if (!error || typeof error !== "object" || !("code" in error) || typeof error.code !== "string") return "other";
  if (["ETIMEDOUT", "UND_ERR_CONNECT_TIMEOUT"].includes(error.code)) return "timeout";
  if (["CERT_HAS_EXPIRED", "CERT_NOT_YET_VALID", "DEPTH_ZERO_SELF_SIGNED_CERT",
    "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "ERR_TLS_CERT_ALTNAME_INVALID"].includes(error.code)) return "tls";
  if (["ECONNRESET", "EPIPE", "ECONNABORTED", "ECONNREFUSED", "ENETUNREACH",
    "EHOSTUNREACH", "EAI_AGAIN", "ENOTFOUND"].includes(error.code)) return "network";
  return "other";
}
export function requestOnce(
  input: RequestInfo | URL,
  init?: RequestInit,
  onTransportFailure?: (failure: SafeTransportFailure) => void,
): Promise<Response> {
  const { promise, resolve, reject } = Promise.withResolvers<Response>();
  try {
    const requestInput = input instanceof Request ? input : undefined;
    const url = new URL(requestInput?.url ?? String(input));
    const method = init?.method ?? requestInput?.method;
    const body = init && "body" in init ? init.body : requestInput?.body;
    const signal = init?.signal ?? requestInput?.signal;
    if (!["http:", "https:"].includes(url.protocol) || method !== "POST" ||
      !(typeof body === "string" || body instanceof Uint8Array || body instanceof ArrayBuffer ||
        ArrayBuffer.isView(body) || (body !== null && typeof body === "object" && "getReader" in body))) {
      onTransportFailure?.("invalid_request");
      reject(new Error("Transport configuration invalid"));
      return promise;
    }
    const headers = new Headers(init?.headers ?? requestInput?.headers);
    headers.set("connection", "close");
    headers.set("accept-encoding", "identity");
    let request: http.ClientRequest;
    try {
      request = (url.protocol === "https:" ? https : http).request(url, {
        method: "POST", headers: Object.fromEntries(headers), agent: false,
        signal: signal ?? undefined, timeout: 30_000, maxHeaderSize: 32 * 1024,
      }, response => {
        try {
          const responseHeaders = new Headers();
          for (const [name, value] of Object.entries(response.headers)) {
            if (value !== undefined) responseHeaders.set(name, Array.isArray(value) ? value.join(", ") : value);
          }
          if (![undefined, "identity"].includes(response.headers["content-encoding"])) {
            response.destroy(); reject(new Error("Unsupported upstream encoding")); return;
          }
          resolve(new Response(Readable.toWeb(response) as ReadableStream<Uint8Array>, {
            status: response.statusCode ?? 502, headers: responseHeaders,
          }));
        } catch {
          response.destroy(); reject(new Error("Upstream response invalid"));
        }
      });
    } catch (error) {
      onTransportFailure?.(safeTransportFailure(error));
      reject(new Error("Upstream transport failed"));
      return promise;
    }
    // Bun's node:http does not reliably apply the RequestOptions signal to an
    // in-flight request. Explicit destruction makes cancellation authoritative.
    const abort = () => {
      request.destroy();
      reject(new Error("Upstream request aborted"));
    };
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    }
    request.on("upgrade", (_response, socket) => {
      socket.destroy(); reject(new Error("Protocol upgrade refused"));
    });
    request.on("timeout", () => {
      onTransportFailure?.("timeout");
      request.destroy(new Error("Upstream timeout"));
    });
    request.on("error", error => {
      onTransportFailure?.(safeTransportFailure(error));
      signal?.removeEventListener("abort", abort);
      reject(new Error("Upstream transport failed"));
    });
    if (body !== null && typeof body === "object" && "getReader" in body) {
      const source = Readable.fromWeb(body as ReadableStream<Uint8Array>);
      source.on("error", () => request.destroy(new Error("Upstream request body failed")));
      source.pipe(request);
    } else if (ArrayBuffer.isView(body)) {
      request.end(Buffer.from(body.buffer, body.byteOffset, body.byteLength));
    } else if (body instanceof ArrayBuffer) {
      request.end(Buffer.from(body));
    } else {
      request.end(body);
    }
  } catch (error) {
    onTransportFailure?.(safeTransportFailure(error));
    reject(new Error("Upstream transport failed"));
  }
  return promise;
}
