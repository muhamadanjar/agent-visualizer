import { access, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

const SANDBOX_TIMEOUT_MS = 10_000;
const SANDBOX_PROBE = `set -eu
[[ -n "$HOST_HOME_PATH" && ! -e "$HOST_HOME_PATH" ]]
[[ ! -e "$HOST_CANARY_PATH" ]]
[[ -w /workspace ]]
printf 'workspace-write-ok\\n' > /workspace/result
[[ "$(< /workspace/result)" == 'workspace-write-ok' ]]
mkdir -p "\${TMP_CANARY_PATH%/*}"
printf 'tmpfs-write-ok\\n' > "$TMP_CANARY_PATH"
interface_count=0
loopback_count=0
while IFS= read -r line; do
  case "$line" in
    *:*)
      interface_count=$((interface_count + 1))
      case "$line" in *lo:*) loopback_count=$((loopback_count + 1)) ;; esac
      ;;
  esac
done < /proc/net/dev
[[ "$interface_count" -eq 1 && "$loopback_count" -eq 1 ]]
has_default_route() {
  local interface destination remainder
  while IFS=$' \\t' read -r interface destination remainder; do
    [[ "$destination" == "00000000" ]] && return 0
  done <<< "$1"
  return 1
}
route_table="$(< /proc/net/route)"
if has_default_route "$route_table"; then exit 41; fi
tabbed_default_route=$'Iface\\tDestination\\tGateway\\tFlags\\neth0\\t00000000\\t01010101\\t0003'
if ! has_default_route "$tabbed_default_route"; then exit 42; fi
printf 'isolation-smoke-passed\\n'
`;

const bwrap = Bun.which("bwrap");
const hostHomePath = process.env.HOME;
if (!bwrap || !hostHomePath?.startsWith("/")) {
  console.error(JSON.stringify({ sandbox: "bubblewrap", result: "unavailable" }));
  process.exitCode = 1;
} else {
  const tempRoot = await mkdtemp(join("/tmp", "m0-sandbox-smoke-"));
  const workspace = join(tempRoot, "workspace");
  const tmpCanaryPath = join(tempRoot, "tmp-canary");
  const hostCanaryPath = join(tempRoot, "host-canary");
  let child: ReturnType<typeof Bun.spawn> | null = null;

  try {
    await mkdir(workspace, { mode: 0o700 });
    await writeFile(hostCanaryPath, "host-only-canary", { mode: 0o600 });
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
        "--chdir", "/workspace",
        "--clearenv",
        "--setenv", "PATH", "/usr/bin",
        "--setenv", "HOME", "/workspace",
        "--setenv", "HOST_HOME_PATH", hostHomePath,
        "--setenv", "HOST_CANARY_PATH", hostCanaryPath,
        "--setenv", "TMP_CANARY_PATH", join("/tmp", basename(tempRoot), "tmp-canary"),
        "--",
        "/usr/bin/bash", "-eu", "-c", SANDBOX_PROBE,
      ],
      { env: {}, stdout: "pipe", stderr: "pipe" },
    );

    let resolveTimeout: (value: null) => void = () => {};
    const timeout = new Promise<null>((resolve) => {
      resolveTimeout = resolve;
    });
    const timeoutHandle = setTimeout(() => resolveTimeout(null), SANDBOX_TIMEOUT_MS);
    const exitCode = await Promise.race([child.exited, timeout]);
    clearTimeout(timeoutHandle);
    if (exitCode === null) {
      child.kill();
      await child.exited;
      throw new Error("sandbox_probe_timed_out");
    }

    const [stdout, stderr] = await Promise.all([
      new Response(child.stdout).text(),
      new Response(child.stderr).text(),
    ]);
    if (exitCode !== 0 || stdout.trim() !== "isolation-smoke-passed") {
      throw new Error(`sandbox_probe_failed:${exitCode}:${stderr.trim()}`);
    }

    const workspaceResult = await readFile(join(workspace, "result"), "utf8");
    if (workspaceResult !== "workspace-write-ok\n") {
      throw new Error("workspace_write_did_not_reach_host_workspace");
    }
    let tmpfsScratchVisibleOnHost = false;
    try {
      await access(tmpCanaryPath);
      tmpfsScratchVisibleOnHost = true;
    } catch {
      // Expected: the sandbox's /tmp is an isolated tmpfs.
    }
    if (tmpfsScratchVisibleOnHost) {
      throw new Error("sandbox_tmpfs_write_reached_host_filesystem");
    }

    console.log(JSON.stringify({
      sandbox: "bubblewrap",
      result: "passed",
      hostHomeVisible: false,
      hostCanaryVisible: false,
      workspaceWritable: true,
      networkNamespace: "loopback_only_no_default_route",
      providerCall: false,
    }, null, 2));
  } catch (error) {
    console.error(JSON.stringify({
      sandbox: "bubblewrap",
      result: "failed",
      reason: error instanceof Error ? error.message : "unknown_failure",
    }));
    process.exitCode = 1;
  } finally {
    if (child && child.exitCode === null) {
      child.kill();
      await child.exited;
    }
    await rm(tempRoot, { recursive: true, force: true });
  }
}
