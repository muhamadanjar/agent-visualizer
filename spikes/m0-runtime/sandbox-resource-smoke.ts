import { mkdir, mkdtemp, readFile, rm, rmdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

/**
 * Bounded local resource-control smoke inside a no-provider Bubblewrap boundary.
 *
 * Claims this file may make:
 *  - Per-process limits are verified by fixed, bounded probes:
 *      - RLIMIT_CPU: a generous-limit control survives; soft enforcement
 *        terminates at SIGXCPU; an SIGXCPU-ignoring child is killed at its
 *        lower hard limit.
 *      - RLIMIT_AS and RLIMIT_FSIZE: a below-limit operation succeeds under
 *        the configured limit while an above-limit operation is refused.
 *    These checks prove only these process-level scenarios, not a runner-wide
 *    or process-tree aggregate limit.
 *  - Process count is constrained only by temporary cgroup v2 `pids.max`,
 *    a cgroup-level control rather than a per-process rlimit. No RLIMIT_NPROC
 *    guarantee is claimed.
 *  - These are process-level controls only. They are NOT container, cgroup,
 *    kernel, or host isolation guarantees, and they do not bound aggregate
 *    resource use of a process tree (every process carries its own counters).
 *
 * Cleanup policy for the owned cgroup leaf: never migrate members to the
 * parent cgroup, never remove files recursively under /sys/fs/cgroup. Write
 * cgroup.kill, wait (bounded) until cgroup.events explicitly reports
 * `populated 0`, then remove the owned leaf with rmdir only. Any unreadable
 * event file, kill failure, drain timeout, or rmdir failure fails the run.
 *
 * No provider/model call, no external network, no host HOME access, temporary
 * files only, bounded wall time, allowlisted output only.
 */

const OVERALL_BUDGET_MS = 300_000;
const SCENARIO_TIMEOUT_MS = 30_000;
const VERSION_TIMEOUT_MS = 5_000;
const STREAM_TIMEOUT_MS = 5_000;
const CGROUP_DRAIN_TIMEOUT_MS = 15_000;
const CLEANUP_RESERVE_MS = CGROUP_DRAIN_TIMEOUT_MS + 5_000;
const CGROUP_ADMISSION_EXIT_CODE = 90;
const CGROUP_ADMISSION_VERIFY_EXIT_CODE = 91;

const AS_LIMIT_KB = 131_072; // 128 MiB address space
const AS_BELOW_LIMIT_MB = 64; // fits under AS_LIMIT_KB
const AS_ABOVE_LIMIT_MB = 400; // must not fit under AS_LIMIT_KB
const FSIZE_LIMIT_BLOCKS = 64; // bash `ulimit -f` unit == 1024 bytes => 64 KiB
const FSIZE_BELOW_LIMIT_KB = 32;
const FSIZE_ABOVE_LIMIT_KB = 1024;
const PROC_COUNT_LIMIT = 32; // cgroup v2 pids.max, leaving room for sandbox setup
const FORK_PROBE_COUNT = 48;

const SCENARIO_SCRIPTS: Record<string, string> = {
  "isolation.sh": `#!/bin/bash
set -u
[ "\${HOME:-}" = "/workspace" ] || { echo "isolation=home-not-workspace"; exit 2; }
[ -n "\${HOST_HOME_PATH:-}" ] || { echo "isolation=host-home-unset"; exit 3; }
[ ! -e "$HOST_HOME_PATH" ] || { echo "isolation=host-home-visible"; exit 4; }
[ ! -e "$HOST_CANARY_PATH" ] || { echo "isolation=host-canary-visible"; exit 5; }
[ -z "$(awk '$2 == "00000000" { print "yes" }' /proc/net/route)" ] || { echo "isolation=default-route-present"; exit 6; }
[ -w /tmp ] || { echo "isolation=tmp-not-writable"; exit 7; }
echo "isolation=ok"
`,
  "cpu-probe.sh": `#!/bin/bash
set -u
ulimit -S -t "$1" || { echo "cpu=ulimit-failed"; exit 2; }
ulimit -H -t "$2" || { echo "cpu=ulimit-failed"; exit 2; }
if [ "$3" = "ignore" ]; then
  /usr/bin/bash -c 'trap "" XCPU; while :; do :; done' &
else
  /usr/bin/bash -c 'while :; do :; done' &
fi
child=$!
/usr/bin/sleep 4
if kill -0 "$child" 2>/dev/null; then
  kill -9 "$child" 2>/dev/null
  wait "$child" 2>/dev/null
  echo "cpu=child-survived"
  exit 0
fi
wait "$child" 2>/dev/null
echo "cpu=child-status-$?"
`,
  "as-probe.sh": `#!/bin/bash
set -u
ulimit -v ${AS_LIMIT_KB} || { echo "as=ulimit-failed"; exit 2; }
size=$(("$1" * 1000000))
/usr/bin/perl -e "my \\$s = 'x' x $size; print qq{as=alloc-ok\\n};"
echo "as=perl-status-$?"
`,
  "fsize-probe.sh": `#!/bin/bash
set -u
ulimit -f "$1" || { echo "fsize=ulimit-failed"; exit 2; }
/usr/bin/dd if=/dev/zero of=/tmp/fsize-probe bs="$2" count="$3"
echo "fsize-dd-status-$?"
/usr/bin/stat -c '%s' /tmp/fsize-probe
`,
  "fork-probe.sh": `#!/bin/bash
set -u
/usr/bin/perl -e '
use Errno qw(EAGAIN);
my $limit = shift;
my @kids;
for (1 .. $limit) {
  my $pid = fork();
  if (!defined $pid) {
    if ($! == EAGAIN) { print "fork=blocked\n"; last; }
    exit 2;
  }
  if ($pid == 0) { sleep 5; exit 0; }
  push @kids, $pid;
}
print "fork=alive-", scalar @kids, "\n";
kill "KILL", @kids;
waitpid($_, 0) for @kids;
print "fork=done\n";
' "$1"
`,
};

type ProcessOutcome = {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  reaped: boolean;
  groupGone: boolean;
};

type LimitVerdict = {
  limit: string;
  mechanism: string;
  enforced: boolean;
  evidence: string;
  control: string;
  scope: string;
};

const STDERR_MARKERS: Record<string, string> = {
  "cpu time limit exceeded": "sigxcpu",
  "file too large": "efbig",
  "file size limit exceeded": "efbig",
  "out of memory": "alloc_failed",
  "resource temporarily unavailable": "eagain",
  killed: "sigkill",
};

function markerFor(stderr: string): string | null {
  const haystack = stderr.toLowerCase();
  for (const [marker, code] of Object.entries(STDERR_MARKERS)) {
    if (haystack.includes(marker)) return code;
  }
  return null;
}

function stdoutHas(stdout: string, token: string): boolean {
  return stdout.split("\n").some((line) => line.trim() === token);
}

function stdoutValue(stdout: string, prefix: string): string | null {
  for (const line of stdout.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith(prefix)) return trimmed.slice(prefix.length);
  }
  return null;
}

const bwrapPath = Bun.which("bwrap");
let processGroupLeaks = 0;
let cgroupDiscoveryCleanupDir: string | null = null;

function groupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

async function waitForExit(
  child: { exited: Promise<number> },
  timeoutMs: number,
): Promise<number | null> {
  if (timeoutMs <= 0) return null;
  const guard = Promise.withResolvers<null>();
  const timer = setTimeout(() => guard.resolve(null), timeoutMs);
  const code = await Promise.race([child.exited, guard.promise]);
  clearTimeout(timer);
  return code;
}

async function waitForGroupExit(pid: number, timeoutMs: number): Promise<boolean> {
  const stopAt = Date.now() + Math.max(0, timeoutMs);
  while (Date.now() < stopAt) {
    if (!groupAlive(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return !groupAlive(pid);
}
const hostHomePath = process.env.HOME ?? "";
const startTime = Date.now();
const deadline = startTime + OVERALL_BUDGET_MS;
const workloadDeadline = deadline - CLEANUP_RESERVE_MS;
const activeProcessGroups = new Set<number>();
let interruptSignal: "SIGINT" | "SIGTERM" | null = null;

function remainingMs(): number {
  return deadline - Date.now();
}

function budgetFor(capMs: number): number {
  return Math.min(capMs, Math.max(0, workloadDeadline - Date.now()));
}

function assertNotInterrupted(): void {
  if (interruptSignal) throw new Error(`interrupted_${interruptSignal.toLowerCase()}`);
}

function handleTermination(signal: "SIGINT" | "SIGTERM"): void {
  interruptSignal ??= signal;
  for (const pid of activeProcessGroups) killTree(pid);
}

process.on("SIGINT", () => handleTermination("SIGINT"));
process.on("SIGTERM", () => handleTermination("SIGTERM"));

function killTree(pid: number): void {
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    // Process group already gone.
  }
  try {
    process.kill(pid, "SIGKILL");
  } catch {
    // Process already gone.
  }
}

type SpawnedStreams = {
  stdout: ReadableStream;
  stderr: ReadableStream;
};

async function collectStreams(
  child: SpawnedStreams,
  timeoutMs: number,
): Promise<{ stdout: string; stderr: string; timedOut: boolean }> {
  const reads = Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text()]);
  const guard = Promise.withResolvers<null>();
  const timer = setTimeout(() => guard.resolve(null), Math.max(1, timeoutMs));
  const result = await Promise.race([reads, guard.promise]);
  clearTimeout(timer);
  if (result === null) return { stdout: "", stderr: "", timedOut: true };
  const [stdout, stderr] = result;
  return { stdout, stderr, timedOut: false };
}

async function runProcess(command: string[], capMs: number): Promise<ProcessOutcome> {
  assertNotInterrupted();
  const budget = budgetFor(capMs);
  if (budget <= 0) {
    return {
      exitCode: null,
      stdout: "",
      stderr: "",
      timedOut: true,
      reaped: false,
      groupGone: true,
    };
  }
  // detached: true puts the child in its own process group so a timeout or
  // catchable termination can stop the complete process tree.
  const child = Bun.spawn(command, {
    env: {},
    stdout: "pipe",
    stderr: "pipe",
    detached: true,
  });
  activeProcessGroups.add(child.pid);
  let timedOut = false;
  const timeout = Promise.withResolvers<null>();
  const timer = setTimeout(() => {
    timedOut = true;
    timeout.resolve(null);
  }, budget);
  try {
    let exitCode = await Promise.race([child.exited, timeout.promise]);
    clearTimeout(timer);
    if (exitCode === null) {
      killTree(child.pid);
      exitCode = await waitForExit(child, Math.min(5_000, Math.max(0, remainingMs())));
      const drained = await collectStreams(child, budgetFor(STREAM_TIMEOUT_MS));
      const groupGone = await waitForGroupExit(
        child.pid,
        Math.min(2_000, Math.max(0, remainingMs())),
      );
      if (!groupGone) processGroupLeaks += 1;
      return {
        exitCode,
        stdout: drained.stdout,
        stderr: drained.stderr,
        timedOut: true,
        reaped: exitCode !== null,
        groupGone,
      };
    }
    const drained = await collectStreams(child, budgetFor(STREAM_TIMEOUT_MS));
    if (drained.timedOut) killTree(child.pid);
    const groupGone = await waitForGroupExit(
      child.pid,
      Math.min(2_000, Math.max(0, remainingMs())),
    );
    if (!groupGone) processGroupLeaks += 1;
    return {
      exitCode,
      stdout: drained.stdout,
      stderr: drained.stderr,
      timedOut: drained.timedOut || timedOut,
      reaped: true,
      groupGone,
    };
  } finally {
    clearTimeout(timer);
    activeProcessGroups.delete(child.pid);
  }
}

async function runSandbox(
  scriptName: string,
  args: string[],
  workspace: string,
  hostCanaryPath: string,
  cgroupProcsPath?: string,
): Promise<ProcessOutcome> {
  const bwrapArgs = [
    "--unshare-all",
    "--die-with-parent",
    "--cap-drop",
    "ALL",
    "--ro-bind",
    "/usr",
    "/usr",
    "--ro-bind",
    "/lib",
    "/lib",
    "--ro-bind",
    "/lib64",
    "/lib64",
    "--dev",
    "/dev",
    "--proc",
    "/proc",
    "--tmpfs",
    "/tmp",
    "--dir",
    "/workspace",
    "--bind",
    workspace,
    "/workspace",
    "--chdir",
    "/workspace",
    "--clearenv",
    "--setenv",
    "PATH",
    "/usr/bin",
    "--setenv",
    "HOME",
    "/workspace",
    "--setenv",
    "HOST_HOME_PATH",
    hostHomePath,
    "--setenv",
    "HOST_CANARY_PATH",
    hostCanaryPath,
    "--",
    "/usr/bin/bash",
    join("/workspace", scriptName),
    ...args,
  ];

  // The cgroup variant joins the launcher to the pids-limited cgroup and
  // verifies membership before it becomes bwrap. On admission failure it
  // exits without ever running the workload.
  const command = cgroupProcsPath
    ? [
        "/usr/bin/bash",
        "-c",
        'cg="$1"; shift; echo $$ > "$cg" || exit 90; grep -qx "$$" "$cg" || exit 91; exec "$@"',
        "bwrap",
        cgroupProcsPath,
        bwrapPath!,
        ...bwrapArgs,
      ]
    : [bwrapPath!, ...bwrapArgs];

  return runProcess(command, SCENARIO_TIMEOUT_MS);
}

async function discoverPidsCgroup(
  leafName: string,
): Promise<{ dir: string; parent: string } | null> {
  const raw = await readFile("/proc/self/cgroup", "utf8").catch(() => "");
  const unified = raw.split("\n").find((line) => line.startsWith("0::"));
  if (!unified) return null; // cgroup v1 only: no v2 pids controller to delegate
  const segments = unified.slice(3).trim().split("/").filter(Boolean);
  for (let depth = segments.length; depth >= 0; depth--) {
    const parent = join("/sys/fs/cgroup", ...segments.slice(0, depth));
    // A "domain threaded" cgroup rejects moving a task into its children.
    const type = (await readFile(join(parent, "cgroup.type"), "utf8").catch(() => "")).trim();
    if (type !== "domain") continue;
    const dir = join(parent, leafName);
    try {
      await mkdir(dir, { mode: 0o700 });
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === "EEXIST") throw new Error("cgroup_leaf_collision");
      continue; // this parent is not writable; try a shallower one
    }
    const controllers = await readFile(join(dir, "cgroup.controllers"), "utf8").catch(() => "");
    if (controllers.split(/\s+/).includes("pids")) return { dir, parent };
    try {
      await rmdir(dir);
    } catch (error) {
      cgroupDiscoveryCleanupDir = dir;
      const release = await releasePidsCgroup(dir);
      if (!release.ok) {
        throw new Error(`cgroup_leaf_cleanup_failed:${release.reason}`);
      }
      cgroupDiscoveryCleanupDir = null;
    }
  }
  return null;
}

async function releasePidsCgroup(dir: string): Promise<{ ok: boolean; reason: string }> {
  const killed = await writeFile(join(dir, "cgroup.kill"), "1").then(
    () => true,
    () => false,
  );
  if (!killed) return { ok: false, reason: "cgroup_kill_write_failed" };

  async function readPopulation(): Promise<{ value: number | null; reason: string | null }> {
    const events = await readFile(join(dir, "cgroup.events"), "utf8").catch(() => null);
    if (events === null) return { value: null, reason: "cgroup_events_unreadable" };
    const line = events.split("\n").find((entry) => entry.startsWith("populated "));
    if (!line) return { value: null, reason: "cgroup_events_missing_populated" };
    const value = Number(line.split(/\s+/)[1]);
    if (!Number.isFinite(value)) return { value: null, reason: "cgroup_events_unparsable" };
    return { value, reason: null };
  }

  // Always inspect once, even if the workload deadline is exhausted: a leaf
  // already emptied by cgroup.kill can still be removed without waiting.
  let state = await readPopulation();
  if (state.reason) return { ok: false, reason: state.reason };
  const drainDeadline = Math.min(Date.now() + CGROUP_DRAIN_TIMEOUT_MS, deadline);
  while (state.value !== 0 && Date.now() < drainDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    state = await readPopulation();
    if (state.reason) return { ok: false, reason: state.reason };
  }
  if (state.value !== 0) return { ok: false, reason: "cgroup_drain_timeout" };

  const removed = await rmdir(dir).then(
    () => true,
    () => false,
  );
  if (!removed) return { ok: false, reason: "cgroup_rmdir_failed" };
  return { ok: true, reason: "released" };
}

if (!bwrapPath) {
  console.error(JSON.stringify({ smoke: "sandbox-resource-limits", result: "unavailable" }));
  process.exitCode = 1;
} else {
  const versionOutcome = await runProcess([bwrapPath, "--version"], VERSION_TIMEOUT_MS);
  const version = stdoutValue(versionOutcome.stdout.trim(), "bubblewrap ") ?? "unknown";
  const tempRoot = await mkdtemp(join("/tmp", "m0-resource-smoke-"));
  const workspace = join(tempRoot, "workspace");
  const hostCanaryPath = join(tempRoot, "host-canary");
  const cgroupLeaf = `m0-resource-smoke-${process.pid}-${crypto.randomUUID()}`;
  const verdicts: LimitVerdict[] = [];
  let cgroup: { dir: string; parent: string } | null = null;
  let cleanupOk = true;
  let cleanupReason = "released";
  let timedOutAnywhere = versionOutcome.timedOut;
  let report: Record<string, unknown> | null = null;
  let failureReason: string | null = null;

  try {
    assertNotInterrupted();
    await mkdir(workspace, { mode: 0o700 });
    await writeFile(hostCanaryPath, "host-only-canary", { mode: 0o600 });
    for (const [name, body] of Object.entries(SCENARIO_SCRIPTS)) {
      await writeFile(join(workspace, name), body, { mode: 0o700 });
    }

    const isolation = await runSandbox("isolation.sh", [], workspace, hostCanaryPath);
    timedOutAnywhere = timedOutAnywhere || isolation.timedOut;
    if (isolation.timedOut || isolation.exitCode !== 0 || !stdoutHas(isolation.stdout, "isolation=ok")) {
      throw new Error("boundary_isolation_check_failed");
    }

    const cpuSoftControl = await runSandbox(
      "cpu-probe.sh",
      ["5", "10", "default"],
      workspace,
      hostCanaryPath,
    );
    const cpuSoftLimit = await runSandbox(
      "cpu-probe.sh",
      ["1", "3", "default"],
      workspace,
      hostCanaryPath,
    );
    const cpuHardControl = await runSandbox(
      "cpu-probe.sh",
      ["5", "10", "ignore"],
      workspace,
      hostCanaryPath,
    );
    const cpuHardLimit = await runSandbox(
      "cpu-probe.sh",
      ["1", "2", "ignore"],
      workspace,
      hostCanaryPath,
    );
    timedOutAnywhere =
      timedOutAnywhere ||
      cpuSoftControl.timedOut ||
      cpuSoftLimit.timedOut ||
      cpuHardControl.timedOut ||
      cpuHardLimit.timedOut;
    const cpuSoftEnforced =
      stdoutHas(cpuSoftControl.stdout, "cpu=child-survived") &&
      stdoutValue(cpuSoftLimit.stdout, "cpu=child-status-") === "152" &&
      markerFor(cpuSoftLimit.stderr) === "sigxcpu";
    const cpuHardEnforced =
      stdoutHas(cpuHardControl.stdout, "cpu=child-survived") &&
      stdoutValue(cpuHardLimit.stdout, "cpu=child-status-") === "137" &&
      markerFor(cpuHardLimit.stderr) === "sigkill";
    verdicts.push({
      limit: "cpu_time",
      mechanism: "RLIMIT_CPU",
      enforced: cpuSoftEnforced && cpuHardEnforced,
      evidence: cpuSoftEnforced && cpuHardEnforced
        ? "soft_sigxcpu_and_hard_sigkill"
        : cpuSoftEnforced
          ? "soft_sigxcpu_only"
          : cpuHardEnforced
            ? "hard_sigkill_only"
            : "not_enforced",
      control: "same_script_generous_cpu_limits_child_survived",
      scope: "per_process",
    });

    const asBelowLimit = await runSandbox(
      "as-probe.sh",
      [String(AS_BELOW_LIMIT_MB)],
      workspace,
      hostCanaryPath,
    );
    const asAboveLimit = await runSandbox(
      "as-probe.sh",
      [String(AS_ABOVE_LIMIT_MB)],
      workspace,
      hostCanaryPath,
    );
    timedOutAnywhere = timedOutAnywhere || asBelowLimit.timedOut || asAboveLimit.timedOut;
    const asBelowStatus = stdoutValue(asBelowLimit.stdout, "as=perl-status-");
    const asAboveStatus = stdoutValue(asAboveLimit.stdout, "as=perl-status-");
    const asEnforced =
      asBelowLimit.exitCode === 0 &&
      stdoutHas(asBelowLimit.stdout, "as=alloc-ok") &&
      asBelowStatus === "0" &&
      asAboveLimit.exitCode === 0 &&
      asAboveStatus !== null &&
      asAboveStatus !== "0" &&
      markerFor(asAboveLimit.stderr) === "alloc_failed";
    verdicts.push({
      limit: "address_space",
      mechanism: "RLIMIT_AS",
      enforced: asEnforced,
      evidence: asEnforced
        ? "below_limit_alloc_ok_above_limit_alloc_failed"
        : "not_enforced",
      control: "same_limit_below_limit_allocation_succeeded",
      scope: "per_process",
    });

    const fsizeBelowLimit = await runSandbox(
      "fsize-probe.sh",
      [String(FSIZE_LIMIT_BLOCKS), "1024", String(FSIZE_BELOW_LIMIT_KB)],
      workspace,
      hostCanaryPath,
    );
    const fsizeAboveLimit = await runSandbox(
      "fsize-probe.sh",
      [String(FSIZE_LIMIT_BLOCKS), "1024", String(FSIZE_ABOVE_LIMIT_KB)],
      workspace,
      hostCanaryPath,
    );
    timedOutAnywhere =
      timedOutAnywhere || fsizeBelowLimit.timedOut || fsizeAboveLimit.timedOut;
    const fsizeEnforced =
      fsizeBelowLimit.exitCode === 0 &&
      stdoutHas(fsizeBelowLimit.stdout, "fsize-dd-status-0") &&
      fsizeAboveLimit.exitCode === 0 &&
      stdoutValue(fsizeAboveLimit.stdout, "fsize-dd-status-") !== "0" &&
      markerFor(fsizeAboveLimit.stderr) === "efbig";
    verdicts.push({
      limit: "file_size",
      mechanism: "RLIMIT_FSIZE",
      enforced: fsizeEnforced,
      evidence: fsizeEnforced
        ? "below_limit_write_ok_above_limit_write_refused"
        : "not_enforced",
      control: "same_limit_below_limit_write_succeeded",
      scope: "per_process",
    });


    // Process count: control run without a pids limit, then the same script
    // inside a cgroup whose pids.max is set.
    const forkControl = await runSandbox(
      "fork-probe.sh",
      [String(FORK_PROBE_COUNT)],
      workspace,
      hostCanaryPath,
    );
    if (
      forkControl.timedOut ||
      forkControl.exitCode !== 0 ||
      !stdoutHas(forkControl.stdout, "fork=done")
    ) {
      throw new Error("pids_control_run_invalid");
    }
    const controlAlive = Number(stdoutValue(forkControl.stdout, "fork=alive-") ?? "-1");
    if (controlAlive !== FORK_PROBE_COUNT) {
      throw new Error("pids_control_fork_count_invalid");
    }
    cgroup = await discoverPidsCgroup(cgroupLeaf);
    let forkLimitedAlive = -1;
    let forkLimitedMarker: string | null = null;
    let pidsMaxHits = 0;
    let procCountEnforced = false;
    if (cgroup) {
      const pidsMaxWritten = await writeFile(
        join(cgroup.dir, "pids.max"),
        `${PROC_COUNT_LIMIT}\n`,
      ).then(
        () => true,
        () => false,
      );
      if (!pidsMaxWritten) {
        throw new Error("pids_max_write_failed");
      }
      const forkLimited = await runSandbox(
        "fork-probe.sh",
        [String(FORK_PROBE_COUNT)],
        workspace,
        hostCanaryPath,
        join(cgroup.dir, "cgroup.procs"),
      );
      const forkLimitedAliveText = stdoutValue(forkLimited.stdout, "fork=alive-");
      const forkLimitedDone = stdoutHas(forkLimited.stdout, "fork=done");
      if (forkLimited.timedOut || forkLimited.exitCode !== 0 || !forkLimitedDone) {
        throw new Error(
          `pids_limited_run_invalid:exit=${forkLimited.exitCode ?? "none"}:timeout=${forkLimited.timedOut}:done=${forkLimitedDone}:alive=${forkLimitedAliveText ?? "missing"}:eagain=${markerFor(forkLimited.stderr) === "eagain"}`,
        );
      }
      forkLimitedAlive = Number(forkLimitedAliveText ?? "-1");
      forkLimitedMarker = stdoutHas(forkLimited.stdout, "fork=blocked")
        ? "eagain"
        : markerFor(forkLimited.stderr);
      const events = await readFile(join(cgroup.dir, "pids.events"), "utf8").catch(() => null);
      if (events === null) {
        throw new Error("pids_events_unreadable");
      }
      const maxEvent = events.split("\n").find((line) => line.startsWith("max "));
      pidsMaxHits = Number(maxEvent?.split(/\s+/)[1] ?? "0");
      procCountEnforced =
        controlAlive >= FORK_PROBE_COUNT &&
        forkLimitedMarker === "eagain" &&
        pidsMaxHits > 0 &&
        (forkLimitedAlive < 0 || forkLimitedAlive < FORK_PROBE_COUNT);
    }
    verdicts.push({
      limit: "process_count",
      mechanism: "cgroup_v2_pids_max",
      enforced: procCountEnforced,
      evidence: procCountEnforced
        ? "fork_refused_above_pids_max"
        : cgroup
          ? "pids_max_present_but_not_enforced"
          : "cgroup_v2_pids_max_unavailable",
      control: "same_script_without_pids_limit_all_forks_allowed",
      scope: "cgroup_scoped",
    });

    const allEnforced = verdicts.every((verdict) => verdict.enforced);
    report = {
      smoke: "sandbox-resource-limits",
      result: allEnforced ? "passed" : "failed",
      bubblewrap: version,
      boundary: "bubblewrap_unshare_all_no_provider_no_network",
      providerCall: false,
      hostHomeMounted: false,
      limits: verdicts,
      guarantee: "process_level_controls_only_not_container_or_cgroup_isolation",
      aggregateBound: false,
      wallClockBudgetMs: OVERALL_BUDGET_MS,
    };
    if (!allEnforced) process.exitCode = 1;
  } catch (error) {
    failureReason = error instanceof Error ? error.message : "unknown_failure";
    process.exitCode = 1;
  } finally {
    let cleanupLeftover: string | null = null;
    if (cgroup) {
      const release = await releasePidsCgroup(cgroup.dir);
      cleanupOk = release.ok;
      cleanupReason = release.reason;
    }
    if (cgroupDiscoveryCleanupDir) {
      const release = await releasePidsCgroup(cgroupDiscoveryCleanupDir);
      if (release.ok) {
        cgroupDiscoveryCleanupDir = null;
      } else {
        cleanupOk = false;
        cleanupReason = `cgroup_discovery_cleanup_failed:${release.reason}`;
      }
    }
    if (processGroupLeaks > 0) {
      cleanupOk = false;
      cleanupReason = "process_group_leak";
    }
    if (cleanupOk) {
      await rm(tempRoot, { recursive: true, force: true });
    } else {
      // The cgroup still holds processes, so the bound workspace must stay
      // in place instead of being removed under a live sandbox.
      cleanupLeftover = tempRoot;
    }
    const budgetExhausted = remainingMs() <= 0;
    const failed =
      failureReason !== null ||
      !cleanupOk ||
      timedOutAnywhere ||
      budgetExhausted ||
      interruptSignal !== null;
    if (failed) {
      process.exitCode = 1;
      console.error(
        JSON.stringify(
          {
            smoke: "sandbox-resource-limits",
            result: "failed",
            reason:
              failureReason ??
              (interruptSignal
                ? `interrupted_${interruptSignal.toLowerCase()}`
                : cleanupOk
                  ? "budget_or_timeout"
                  : "cleanup_incomplete"),
            limits: verdicts,
            providerCall: false,
            bubblewrap: version,
            cleanup: cleanupOk ? "ok" : `incomplete:${cleanupReason}`,
            cleanupLeftover,
            timedOut: timedOutAnywhere,
            budgetExhausted,
          },
          null,
          2,
        ),
      );
    } else {
      console.log(
        JSON.stringify(
          {
            ...(report ?? {}),
            cleanup: "ok",
            timedOut: false,
            budgetExhausted: false,
          },
          null,
          2,
        ),
      );
    }
  }
}
