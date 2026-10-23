import { OmpRpcAdapter } from "./omp-rpc-adapter";

let adapter: OmpRpcAdapter | null = null;
let succeeded = false;

try {
  adapter = await OmpRpcAdapter.connect({ cwd: process.cwd() });
  const status = adapter.getStatus();
  const health = await adapter.getRuntimeState();
  if (status.phase !== "ready" || status.activeRequestId !== null) {
    throw new Error("The idle adapter did not report a ready state");
  }
  if (
    !health.responsive ||
    health.reason !== "ok" ||
    health.heartbeat !== false ||
    health.isSettled !== true ||
    health.hasPendingAsyncWork !== false
  ) {
    throw new Error("The idle runtime-state poll did not return the expected settled state");
  }

  console.log(JSON.stringify({
    adapter: "m0-omp-rpc",
    phase: status.phase,
    responsive: health.responsive,
    reason: health.reason,
    heartbeat: health.heartbeat,
    isSettled: health.isSettled,
    hasPendingAsyncWork: health.hasPendingAsyncWork,
  }, null, 2));
  succeeded = true;
} catch {
  console.error(JSON.stringify({ adapter: "m0-omp-rpc", result: "failed", error: "idle_health_smoke_failed" }));
  process.exitCode = 1;
} finally {
  if (adapter) {
    try {
      await adapter.close();
    } catch {
      if (succeeded) {
        console.error(JSON.stringify({ adapter: "m0-omp-rpc", result: "shutdown_failed" }));
        process.exitCode = 1;
      }
    }
  }
}
