# Spec: M0 OMP RPC adapter prototype

**Status:** Bounded prototype exists. Idle health is verified against OMP; active-task health and cancellation ordering/idempotency are verified only with synthetic fixtures. The changed real task smoke remains unrun.

## Objective

Create an isolated TypeScript adapter prototype in `spikes/m0-runtime/` that launches the installed Oh My Pi process over RPC v2, sends one real read-only task, correlates the terminal result by request ID, and exposes allowlisted activity metadata. OMP activity frames do not carry request IDs; activity attribution is therefore limited to a single active task window and is not upstream event correlation. This tests one narrow Phase 1 adapter path.

This is not a production Managed adapter. The application is not scaffolded, the production architecture is not approved, and required Managed safety capabilities remain unknown.

## Existing evidence and constraints

- Installed target: OMP v18.12.3; RPC v2 negotiation and one real `read` task have been observed.
- [`rpc-task-smoke.ts`](rpc-task-smoke.ts) is a direct RPC probe, not an adapter.
- The PRD requires a Managed adapter to expose start, activity events, terminal result, health/heartbeat, follow-up input, cancel acknowledgement, and constrained delegation (FR-05).
- Scoped approval semantics, process/workspace isolation, health heartbeat, cancellation safety under side effects, and interrupted-task recovery semantics remain unproven. The initial child probe observed `bash` despite the parent's `--tools=read,task`; an explicit `.omp/agents/read-only-child.md` profile with only `read` passed the rerun but does not constrain unspecified/default child agents. The isolated adapter itself does not expose delegation and must not be labeled Managed-ready.
- No test framework or application source exists in this repository. Bun and the existing bounded smoke script are the local convention.

## Scope

- Add `OmpRpcAdapter` under `spikes/m0-runtime/`.
- Own OMP subprocess startup/shutdown, RPC v2 negotiation, bounded JSONL frame reading, request correlation, and prompt-result completion.
- Expose allowlisted activity metadata only: event type, local task request ID, `single-active-task-window` attribution, tool name when present, safe terminal status, error flag, and settlement state. Never log prompt content, tool arguments, tool output, credentials, or full RPC frames.
- Keep the executable smoke read-only and constrain it to the existing `read` tool scenario. Do not add auto-approval, shell/write tools, an approval API, child delegation controls, session persistence, or automatic retries.
- Provide explicit capability status: verified narrowly in this prototype (one read-only task start, observed activity associated with its sole active-task window, terminal result matched by request ID); unavailable or unknown (`approval`, heartbeat, safe cancellation, delegation outside the explicit child profile, isolation, interrupted-turn reconciliation/replay). `--continue` can reopen a same-session RPC process after SIGKILL and accept a fresh task, but does not replay the interrupted prompt.
- Document that follow-up and abort were exercised directly against OMP but are not established as safe adapter capabilities by this prototype.

## Prototype interface

```ts
export interface OmpRpcAdapter {
  startReadOnlyTask(prompt: string): Promise<RunResult>;
  onActivity(handler: (event: SafeRuntimeEvent) => void): () => void;
  getStatus(): AdapterStatus;
  getRuntimeState(): Promise<PointInTimeHealth>;
  cancelActiveTask(): Promise<CancelAcknowledgement>;
  close(): Promise<void>;
}
```

`RunResult` contains the RPC request ID, `agentInvoked`, sanitized terminal status, `sessionSettled`, and whether an abort acknowledgement arrived before the terminal result. `cancelActiveTask()` reports the abort acknowledgement; the task promise separately reports final settlement. `getRuntimeState()` is an idle-only point-in-time poll: while a task is active it returns `reason: "active_task"` without issuing `get_state`, because OMP's `get_state` waits for the session to settle. This is not a heartbeat. `SafeRuntimeEvent` is an allowlisted metadata projection, not an RPC frame passthrough. Its request ID is assigned locally while the sole task is active; it does not come from the OMP activity frame. One task per adapter process; reject concurrent starts rather than queueing implicitly.

## Commands

- Safe idle RPC probe: `rtk bun run spikes/m0-runtime/rpc-health-smoke.ts` (no task/provider call).
- Adapter idle-health smoke: `rtk bun run spikes/m0-runtime/adapter-health-smoke.ts` (no model call).
- Synthetic adapter-boundary failures: `rtk bun run spikes/m0-runtime/adapter-failure-smoke.ts`.
- No-provider Bubblewrap boundary smoke: `rtk bun run spikes/m0-runtime/sandbox-smoke.ts`.
- No-provider OMP-in-Bubblewrap health smoke: `rtk bun run spikes/m0-runtime/sandbox-omp-health-smoke.ts`.
- Do not run model-backed task/adapter/child/recovery/abort probes until the task and adapter are isolated and a provider/egress path is approved; the current Bubblewrap health smoke covers only process startup and idle state.
- Do not run `rpc-approval-smoke.ts` on the host: it enables `bash`; that probe has not been exercised in this isolation profile and cannot establish call-ID binding.
- No unit-test, lint, or build command is configured for this spike; do not invent a project-wide test suite.

## Project structure

- `spikes/m0-runtime/omp-rpc-adapter.ts` — subprocess and RPC boundary.
- `spikes/m0-runtime/adapter-smoke.ts` — bounded real read-only integration scenario.
- `spikes/m0-runtime/adapter-health-smoke.ts` — no-model idle health verification against the installed OMP RPC process.
- `spikes/m0-runtime/sandbox-smoke.ts` — no-provider Bubblewrap filesystem and network namespace smoke; does not launch OMP.
- `spikes/m0-runtime/sandbox-omp-health-smoke.ts` — starts installed OMP inside Bubblewrap and verifies idle RPC health without a prompt/provider call; not an adapter or task test.
- `spikes/m0-runtime/rpc-approval-smoke.ts` — unverified candidate probe; do not run on host, and do not treat its prompt/argument matching as call identity binding.
- `spikes/m0-runtime/adapter-failure-smoke.ts` — synthetic process fixtures for malformed/chunked frames, negotiation timeout, abnormal exit, duplicate/late events, cancellation ordering, and idempotent abort requests.
- `spikes/m0-runtime/README.md` — reproduction, capabilities, and explicit non-production limit.
- `docs/m0-feasibility.md` and `docs/progress.md` — evidence and Phase 1 status.

## Testing strategy

The previous real read-only smoke proved v2 negotiation, task completion, terminal request-ID matching, single-active-task-window event attribution, concurrent-start rejection, allowlisted output, and shutdown. The new [`adapter-health-smoke.ts`](adapter-health-smoke.ts) has been run against OMP and verified ready/responsive/settled idle health with no heartbeat. [`sandbox-omp-health-smoke.ts`](sandbox-omp-health-smoke.ts) stages only OMP's declared runtime dependency closure and the Bun executable through a no-follow descriptor under `/tmp`, then starts OMP in Bubblewrap and verifies RPC v2 plus idle `get_state` after clearing inherited environment and setting only sandbox `PATH`/`HOME`; it rejects HOME overlap with the read-only `/usr`, `/lib`, or `/lib64` mount sources. It submits no prompt and makes no provider call. This does not launch the adapter or prove a model-backed task can safely run there. The current adapter task smoke also asserts the active-task health path; that model-backed smoke has not been run because provider/egress policy is unapproved and host `read` is not a workspace boundary. Separate synthetic OMP fixtures test adapter failure cleanup.

The adapter's abort contract separates command acknowledgement from terminal settlement and returns `cancelAcknowledged` only when the abort response is consumed before the prompt result. The synthetic `cancel_race` fixture verified both response/result orderings in one input chunk, repeated cancel requests map to one abort, and terminal settlement remains separately reported. This verifies adapter state transitions only; direct OMP abort evidence used a read-only task and does not establish side-effect cancellation safety. The health poll is an idle-time point-in-time responsiveness check only; no heartbeat or active-run health is available.

Managed runtime remains blocked: generic Bubblewrap isolation and no-provider OMP startup/idle RPC health passed, but Bubblewrap is not integrated with the adapter, and resource limits are unverified. No approved provider credential/egress path exists for a real model call inside isolation; default child tools are not constrained, approval is not call-ID-bound, and recovery does not reconcile the interrupted task. Do not run real model-backed probes outside an approved isolation and provider-policy boundary.

## Boundaries

- Always: use a real OMP RPC process; bounded timeouts; no session persistence; read-only task; fail closed on missing/unknown capability; do not store or print sensitive RPC content.
- Ask first: any production app/runner integration, expanded tool permissions, approval behavior, child execution, or changes to the Managed product contract.
- Never: claim production readiness, enable auto-approval, synthesize runtime events, or treat a successful read-only task as proof of isolation or Managed safety.

## Success criteria

1. The adapter starts OMP v18.12.3 and negotiates RPC v2.
2. A real read-only task completes through the adapter; `prompt_result` correlates by its request ID, while activity uses explicitly weaker single-active-task-window attribution.
3. Malformed/chunked frames, a negotiation timeout, and subprocess failure reject the adapter and terminate the child; these paths were exercised with synthetic fixtures. Concurrent starts reject in the real adapter smoke.
4. Output contains no prompt body, tool arguments/output, or credential material.
5. Documentation clearly marks this as an M0 prototype and keeps all unverified Managed capabilities blocked.

## Approval

The user approved implementing this isolated prototype, not a production Managed integration. Scope expansion to production integration, expanded tool permissions, approval behavior, or child execution still requires a separate decision.

