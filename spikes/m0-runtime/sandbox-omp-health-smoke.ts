import { isRecord } from "./runtime-types";
import { mkdir, mkdtemp, realpath, rm } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import {
  OMP_PACKAGE_NAME,
  OMP_PACKAGE_VERSION,
  copyRunningFile,
  openAbsoluteDirectory,
  pathsOverlap,
  stageRuntimePackages,
  isWithin,
  withTimeout,
} from "./sandbox-runtime";

const RESPONSE_TIMEOUT_MS = 10_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const MAX_FRAME_BYTES = 1_048_576;
const NEGOTIATION_ID = "m0-sandbox-omp-negotiate";
const STATE_ID = "m0-sandbox-omp-state";

type RpcFrame = Record<string, unknown> & { type: string };



const bwrap = Bun.which("bwrap");
const omp = Bun.which("omp");
const home = process.env.HOME;
if (!bwrap || !omp || !home?.startsWith("/")) {
  console.error(JSON.stringify({ sandbox: "bubblewrap", omp: "rpc_health", result: "unavailable" }));
  process.exitCode = 1;
} else {
  const bun = process.execPath;
  const installRoot = await realpath(join(home, ".bun", "install"));
  const packageRoot = await realpath(join(installRoot, "global", "node_modules"));
  const ompEntry = await realpath(omp);
  const expectedOmpEntry = await realpath(join(packageRoot, OMP_PACKAGE_NAME, "dist", "cli.js"));
  const hostHome = await realpath(home);
  const systemMountRoots = await Promise.all(["/usr", "/lib", "/lib64"].map((path) => realpath(path)));
  const homeOverlapsSystemMount = systemMountRoots.some((root) => pathsOverlap(hostHome, root));
  if (
    homeOverlapsSystemMount ||
    !isAbsolute(bun) ||
    !isWithin(installRoot, packageRoot) ||
    ompEntry !== expectedOmpEntry
  ) {
    console.error(JSON.stringify({
      sandbox: "bubblewrap",
      omp: "rpc_health",
      result: homeOverlapsSystemMount ? "host_home_overlaps_system_mount" : "unsupported_install_layout",
    }));
    process.exitCode = 1;
  } else {
    const sandboxEntry = join("/opt/node_modules", OMP_PACKAGE_NAME, "dist", "cli.js");
    const tempRoot = await mkdtemp(join("/tmp", "m0-omp-sandbox-health-"));
    const workspace = join(tempRoot, "workspace");
    const stagedNodeModules = join(tempRoot, "node_modules");
    let child: ReturnType<typeof Bun.spawn> | null = null;
    let succeeded = false;
    let stderrBytes = 0;
    let buffer = "";
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();

    async function nextFrame(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<RpcFrame | null> {
      while (true) {
        const newline = buffer.indexOf("\n");
        if (newline >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          if (!line.trim()) continue;
          if (encoder.encode(line).byteLength > MAX_FRAME_BYTES) {
            throw new Error("omp_rpc_frame_too_large");
          }
          const parsed: unknown = JSON.parse(line);
          if (!isRecord(parsed) || typeof parsed.type !== "string") {
            throw new Error("omp_rpc_frame_invalid");
          }
          return parsed as RpcFrame;
        }
        if (encoder.encode(buffer).byteLength > MAX_FRAME_BYTES) {
          throw new Error("omp_rpc_frame_too_large");
        }
        const result = await withTimeout(reader.read(), "omp_rpc_response_timeout", RESPONSE_TIMEOUT_MS);
        if (result.done) return null;
        buffer += decoder.decode(result.value, { stream: true });
      }
    }

    async function responseFor(
      reader: ReadableStreamDefaultReader<Uint8Array>,
      id: string,
    ): Promise<RpcFrame> {
      while (true) {
        const frame = await nextFrame(reader);
        if (!frame) throw new Error("omp_exited_before_rpc_response");
        if (frame.type === "response" && frame.id === id) return frame;
      }
    }

    try {
      const canonicalTempRoot = await realpath(tempRoot);
      if (pathsOverlap(hostHome, canonicalTempRoot)) throw new Error("sandbox_temp_root_overlaps_host_home");
      const copyBuffer = new Uint8Array(64 * 1024);
      await copyRunningFile(bun, join(tempRoot, "bun"), copyBuffer);
      const packageRootHandle = await openAbsoluteDirectory(packageRoot);
      try {
        await stageRuntimePackages(packageRootHandle, stagedNodeModules, copyBuffer);
      } finally {
        await packageRootHandle.close();
      }
      await mkdir(workspace, { mode: 0o700 });
      child = Bun.spawn(
        [
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
          "--chdir", "/workspace",
          "--clearenv",
          "--setenv", "PATH", "/usr/bin:/opt",
          "--setenv", "HOME", "/workspace",
          "--",
          "/opt/bun",
          sandboxEntry,
          "--mode", "rpc",
          "--no-session",
          "--no-ui",
          "--tools=read",
          "--no-extensions",
          "--no-skills",
          "--no-rules",
          "--model=openai/gpt-5.2",
          "--max-time=60s",
        ],
        { env: {}, cwd: workspace, stdin: "pipe", stdout: "pipe", stderr: "pipe" },
      );
      void child.stderr.pipeTo(new WritableStream({
        write(chunk) {
          stderrBytes += chunk.byteLength;
        },
      })).catch(() => {});
      const reader = child.stdout.getReader();
      const ready = await nextFrame(reader);
      const versionList = Array.isArray(ready?.supportedProtocolVersions) ? ready.supportedProtocolVersions : [];
      if (!ready || ready.type !== "ready" || !versionList.includes(2)) {
        throw new Error(
          `omp_rpc_v2_ready_frame_missing:type=${ready?.type ?? "eof"}:versions=${versionList.length}:stderrBytes=${stderrBytes}:exitCode=${child.exitCode}`,
        );
      }

      await child.stdin.write(`${JSON.stringify({ id: NEGOTIATION_ID, type: "negotiate_protocol", protocolVersion: 2 })}\n`);
      await child.stdin.flush();
      const negotiated = await responseFor(reader, NEGOTIATION_ID);
      if (negotiated.success !== true) throw new Error("omp_rpc_v2_negotiation_failed");

      await child.stdin.write(`${JSON.stringify({ id: STATE_ID, type: "get_state" })}\n`);
      await child.stdin.flush();
      const stateResponse = await responseFor(reader, STATE_ID);
      if (!isRecord(stateResponse.data) || stateResponse.success !== true) {
        throw new Error("omp_sandbox_state_response_invalid");
      }
      const state = stateResponse.data;
      if (typeof state.isSettled !== "boolean" || typeof state.hasPendingAsyncWork !== "boolean") {
        throw new Error("omp_sandbox_state_fields_missing");
      }

      console.log(JSON.stringify({
        sandbox: "bubblewrap",
        ompRpcV2: true,
        responsive: true,
        isSettled: state.isSettled,
        hasPendingAsyncWork: state.hasPendingAsyncWork,
        providerCall: false,
        externalNetwork: false,
        hostHomeMounted: false,
      }, null, 2));
      succeeded = true;
    } catch (error) {
      console.error(JSON.stringify({
        sandbox: "bubblewrap",
        omp: "rpc_health",
        result: "failed",
        reason: error instanceof Error ? error.message : "unknown_failure",
      }));
      process.exitCode = 1;
    } finally {
      if (child) {
        if (succeeded) {
          child.stdin.end();
          const exitCode = await withTimeout(child.exited, "omp_shutdown_timeout", SHUTDOWN_TIMEOUT_MS).catch(() => null);
          if (exitCode === null) {
            child.kill();
          } else if (exitCode !== 0) {
            console.error(JSON.stringify({ sandbox: "bubblewrap", omp: "rpc_health", result: "shutdown_failed" }));
            process.exitCode = 1;
          }
        } else {
          child.kill();
        }
        await child.exited;
      }
      await rm(tempRoot, { recursive: true, force: true });
    }
  }
}
