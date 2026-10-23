# Agent Office — Phase 0 Design Baseline

**Status:** Runtime-neutral adapter direction recorded 2026-10-23; runtime behavior and performance claims remain unverified.  
**Phase plan:** [`phase-plan.md`](phase-plan.md)  
**Progress:** [`progress.md`](progress.md)  
**Product source:** [`../PRD.md`](../PRD.md)

## 1. Purpose

This document records the product and architecture contract for implementation planning. It does not claim that the proposed stack, any Runtime Adapter, runner isolation, or 3D performance has been proven. Those claims remain subject to the phase-plan verification gates.

## 2. Product boundary

Agent Office is a private workspace for observing and managing real AI-agent work through a live, explorable 3D office plus equivalent list/detail workflows. The MVP must run against a real Managed runtime adapter and support providers through explicitly available and verified Provider Adapters. OpenAI is not a provider requirement. A scene with simulated activity can validate visual design but cannot satisfy operational MVP acceptance.

The initial pilot targets one owner, one private workspace, several projects, and one configured repository/workspace accessed through a runner. User assignment is explicit. Delegation targets a registered and permitted agent definition; prose mentioning a role does not create a delegation. The Runtime Adapter is selected per Runtime Connection and distinct from a model provider endpoint; a configured endpoint may be a local model service or a router. OMP is an existing prototype candidate; Codex app-server is an unverified candidate; ACP is a protocol usable only with a compatible runtime.

### Out of scope for MVP

- Multi-operator or public multi-tenant service.
- Simulated activity presented as real runtime work.
- Automatic agent routing or unrestricted agent creation/delegation.
- Script execution merely because a skill package was uploaded.
- Pre-integrating every provider and runtime at once. MVP starts with a provider-neutral Provider Adapter contract and runtime-neutral Runtime Adapter contract. Each provider/model/endpoint and each runtime adapter works only when its adapter exists and the required capabilities and safety boundaries pass; no specific vendor or API protocol is mandatory.
- Video/spatial audio, multiplayer presence, or a free-form world editor.

## 3. Domain boundaries and invariants

| Concept | Meaning for this design | Invariant |
| --- | --- | --- |
| Workspace | Private owner scope containing projects, configuration, and policy | Every server operation is checked against workspace/project scope. |
| Project | Work context and access boundary for resources/repository | Visual room membership does not grant project access. |
| Agent Definition | Versioned reusable role instructions and skill references | Creating/editing a definition does not start work or consume runtime capacity. |
| Skill Version | Versioned instruction/text resources and declared dependencies | Attachment does not imply loading or use; it grants no new permission. |
| Agent Instance | Office identity linked to an agent definition | An instance may be idle or disconnected without an active run. |
| Runtime Connection | Authenticated link to a runtime/runner with capabilities and health | Connection health is not proof that a particular session is executing. |
| Runtime Session | Runtime execution context | It is distinct from persistent agent identity and individual task attempts. |
| Model Provider | Hosted or local service that supplies model inference | Separate from the runtime that executes agent tasks. |
| Runtime Adapter | Integration mapping application commands and runtime evidence to an agent execution runtime | It is distinct from the model provider and the model endpoint. |
| Model Endpoint | Configured inference destination and model/route ID | May point directly to a provider or to a router; each run snapshots the selected non-secret configuration. |
| Provider Router / Gateway | Endpoint that forwards or chooses among upstream providers | The actual upstream and cost are unknown unless the router reports them. |
| Task | User-visible work request with project, input, acceptance criteria, and dependencies | Task acceptance is separate from runtime completion. |
| Run / Attempt | One execution attempt for a task | Retry creates a new run; each run retains its effective configuration snapshot. |
| Delegation | Explicit parent run → child task/run relationship | Parentage is never inferred from prompt text or avatar movement. |
| Activity Event | Runtime evidence or a recorded user/application action | Event source and timestamps are explicit; duplicate delivery is idempotent. |
| Artifact | Result linked to a run, such as a diff, document, or report | Origin, run relationship, and availability are recorded. |
| Approval | User decision scoped to a specific action and parameters | Material payload changes invalidate the old approval. |
| Office Scene Version | Versioned layout, rooms, desks, colliders, walkable areas, and anchors | Layout affects presentation/navigation, not authorization. |
| Player Avatar / Presence | User-controlled visual character and camera state | It is not an AI runtime session. |
| Visual Agent State | Presentation pose/animation derived from verified job state and scene | It cannot set run status, permissions, or task acceptance. |

### Required state separation

1. **Office layout state:** persisted and versioned; changes are loaded as scene versions.
2. **Movement/presentation state:** local frame-loop state with periodic user-pose checkpoints; never becomes a work event per frame.
3. **AI work state:** sourced from runtime evidence, validated and persisted by the backend, then delivered to clients as snapshot/delta.

Run execution state, connection health, task workflow/acceptance, and visual state remain separate dimensions. For example, a stale connection may show the last observed run status with its timestamp; it must not turn that run into Failed, Succeeded, or Idle.

## 4. Proposed component responsibilities

These are proposed boundaries from the PRD, not existing code modules.

- **Browser / web app:** list/detail/task workflows, office renderer, user controls, and client-side scene movement. Browser never connects directly to shell/CLI or database.
- **API/control plane:** authentication, scope/policy enforcement, domain commands, queries, event validation/persistence, and WebSocket snapshots/deltas.
- **PostgreSQL:** durable domain state, commands/outbox, event ledger/cursors, audit records, and scene metadata.
- **Dispatcher:** claims durable commands with short leases and sends commands to registered runners; it does not hold database locks while an agent is working.
- **Runner:** authenticates outbound to the control plane, prepares isolated workspaces/processes, supervises the selected Runtime Adapter lifecycle, and spools events until acknowledged.
- **Runtime Adapter / runtime:** adapter maps canonical application commands and evidence to a selected execution runtime; runtime runs under the runner's constrained scope. No engine-specific concepts leak into the task/run domain model.
- **Model provider adapter / endpoint:** protocol adapter translates inference requests to a selected provider API. It is distinct from the execution runtime; the configured endpoint may be a local service or router, and upstream identity, cost, and provider-side limits are unknown unless verifiable telemetry is returned.
- **Artifact storage:** private storage for larger output, with metadata/checksum and run attribution in the application data model.

### Baseline topology

```mermaid
flowchart LR
    Browser[Browser: 3D office and work panels] -->|HTTPS commands and queries| API[FastAPI control plane]
    API -->|WSS snapshot and deltas| Browser
    API --> DB[(PostgreSQL)]
    API --> Artifacts[Private artifact storage]
    DB --> Dispatcher[Python dispatcher]
    Dispatcher -->|Authenticated command| Runner[Isolated runner process]
    Runner --> RuntimeAdapter["Selected Runtime Adapter"]
    RuntimeAdapter --> Runtime["Selected execution runtime"]
    Runtime --> Workspace["Run-scoped project workspace"]
    Runtime -->|selected provider protocol| ProviderAdapter["Available provider adapter"]
    ProviderAdapter --> ModelEndpoint["Configured model endpoint or router"]
    ModelEndpoint -->|optional routing; upstream may be unknown| Upstream["Underlying provider/model service"]
```

The initial pilot places the runner on the same private Linux host as the control plane, but as an isolated process. The runner connection is authenticated and scoped. A separate host remains an option if M0 or pilot resource/security evidence requires it.

## 5. Command and event contract baseline

### Command path

1. The client submits an explicit command with an idempotency/request ID.
2. The API checks identity, project/resource scope, policy, capability, and configured limits.
3. The API commits the command intent and outbox record with domain state in one transaction.
4. The dispatcher claims a ready command with a short lease and sends it to an authenticated runner.
5. The runner deduplicates the command, prepares the scoped workspace, and invokes the selected runtime.
6. Command status is queryable if the browser event connection drops. A sent frame alone is not success.

For start, the UI may display Starting while awaiting runtime acknowledgement; it must not display Running until the adapter has evidence appropriate to the tested capability. For cancel, display Cancel requested until acknowledgement. If the outcome cannot be established, retain an explicit uncertain state and do not retry blindly.

### Event path

1. Runtime/runner emits an event with source identity, event ID, run/session/project context where available, event type, occurrence time, and sequence/cursor if provided.
2. The control plane validates source authentication and scope, redacts/minimizes sensitive payload, and deduplicates.
3. Persist event and projection durably before acknowledging it to the runner.
4. Deliver committed updates to clients. On reconnect, restore a consistent snapshot and continue from a cursor; if replay is unavailable, request a snapshot and disclose any history gap.

Late events may be retained in history but cannot regress a terminal run state. When a source field is unavailable, represent it as unknown rather than infer it from prose.

Runtime Adapter is a versioned application boundary, not a promise of universal runtime compatibility. OMP RPC is an existing prototype candidate; Codex app-server is another candidate whose integration remains unverified; ACP is a protocol, not an execution runtime. Phase 1 must compare candidates against the same lifecycle and safety contract before any adapter is treated as supported.

The canonical contract covers:

- start, runtime acknowledgement, and session identity;
- activity, terminal result, attribution, and settled/completion semantics;
- health/heartbeat and reconnect/replay behavior;
- follow-up input and approval/dialog behavior;
- cancel request and acknowledgement, including queued follow-up behavior;
- sub-agent/delegation parentage and constraints;
- process/workspace isolation and credential boundaries;
- capabilities, limits, and explicit `unknown` / unsupported states.

The core stores canonical task/run/session/events and adapter identity/version; adapter-specific payloads remain at the integration boundary. The runner enforces workspace, credential, egress, and resource policy independently of runtime claims. Unsupported or unknown required capabilities block Managed execution; they are not silently emulated or inferred from a protocol/vendor label.

## 7. Trust, access, and data boundaries

- Prompt Markdown, front matter, skill resources, filenames, repository contents, and runtime output are untrusted input.
- Import parses front matter as data; it never executes instructions or follows unsafe paths.
- Runner policy—not prompt wording, agent role, room placement, or skill attachment—enforces filesystem, network, tool, secret, and resource access.
- Child scope is the intersection of parent run permissions, child definition permissions, and project policy. Parent secrets/conversation are not inherited wholesale.
- Provider credentials stay server-side/on runner or in a secret store; exclude them from browser snapshots, exports, and general activity logs.
- Runner egress is denied by default and limited to the configured, approved model endpoint. The endpoint must not become a path to unrelated private hosts or cloud metadata.
- A provider router is a separate network/budget boundary: its upstream routing and fallback are outside the runner's direct egress control unless the router exposes enforceable policy and attribution.
- Orca CLI digunakan untuk orkestrasi saja; decision gate Task-DAG-nya bukan otoritas approval tool runtime. Agent Office bertanggung jawab atas approval per-tool-call dan tetap menerapkan policy serta audit. Keputusan harus terikat ke invocation ID runtime yang stabil, action, hash parameter/resource, nonce sekali pakai, expiry, dan identitas approver; sampai jalur itu tersedia, approval fail-closed. OMP approval frame yang diamati belum memiliki `toolCallId`.
- Approvals identify the action and parameters. Material changes require a new approval.
- Logs and artifacts are minimized/redacted; no hidden chain-of-thought is requested or stored.
- Workspace/runner and artifact retrieval are authorized by server-side scope checks, not by client-supplied identifiers alone.

## 8. Office interaction contract

- Explore Mode and Overview Mode are presentation choices and do not restart runtime sessions.
- Local input drives local movement; no per-frame API/model/audit event is emitted.
- Click-to-move uses the walkable grid/pathfinding; collision prevents crossing solid geometry. Invalid destinations must explain why and stop movement.
- Interaction opens the relevant context panel. Assign, start, cancel, approval, and input require explicit commands under the same policy as list/detail views.
- If the connection is stale, freeze agent work animation and show last-seen time; the user's local exploration may continue.
- If WebGL or scene assets fail, retain list/detail/task/inbox access.

## 9. Persistence and pilot operating assumptions

- PostgreSQL is the proposed durable source for domain state, command/outbox state, and event history; schema and migrations remain to be finalized and implemented in Phase 3.
- Large artifacts use private storage rather than unbounded event payloads.
- Each parallel run needs an isolated workspace/worktree or explicit serialization policy.
- Initial concurrency, runtime duration, child count, and delegation depth limits must be enforced before starting/spawning work.
- The PRD proposes 30-day detailed event/log retention and 90-day definitions/run summaries/approvals/artifacts. These are configurable proposed pilot defaults, not yet deployed settings.
- Monetary budget values must be selected before pilot. Show actual/estimated/unavailable distinctly; do not promise a hard spend cap without runtime enforcement.
- Pilot backup target is the PRD proposal (daily backup, RPO 24 hours, RTO 4 hours) and requires a restore exercise before operational use.

## 10. Verification strategy

- **M0 runtime spike:** real task through at least one candidate adapter; compare an alternate candidate where locally available; record event/result provenance, capability matrix, disconnect/cancel/input/delegation/isolation cases.
- **M0 scene spike:** movement/collision, camera, interaction focus, and measured target-device responsiveness.
- **Domain/API:** import preservation, immutable run snapshots, idempotent command/event handling, scope authorization, dependency-cycle rejection, and durable restart behavior.
- **Runner/recovery:** duplicate commands, event spool/replay, lease expiry, process restart, uncertain status, and workspace isolation.
- **Browser:** Explore/Overview, keyboard/panel focus, stale behavior, list fallback, no-WebGL behavior, and scene version changes.
- **End-to-end:** PRD AC-01–33 and the required real-runtime demo; simulated events are not accepted as proof of Managed operation.

This design baseline describes proposed boundaries; executed scenarios and their evidence are tracked in [`progress.md`](progress.md) and [`m0-feasibility.md`](m0-feasibility.md).

## 11. Open questions and verification gates

| Item | Current position | Resolution point |
| --- | --- | --- |
| Runtime adapter contract and candidates | Core is runtime-neutral; OMP has narrow prototype evidence, Codex app-server remains untested, ACP is a protocol for compatible runtimes | Phase 1 capability matrix and candidate task smoke; never infer support from a label. |
| Input, approval, cancel, delegation, and completion semantics | Unverified per installed runtime and adapter | Phase 1 capability matrix and contract tests. |
| Runtime can enforce required isolation | Unverified | Phase 1 security/isolation spike; Managed run blocked if runner cannot enforce it independently. |
| Scene performance and asset budget | PRD targets are proposals | Phase 1 scene spike and Phase 5/8 measurements. |
| Exact DB schema, API contracts, event envelope version | Proposed, not finalized | Refine against Phase 1 spike results before implementation in Phase 3. |
| Pilot budget amounts and provider rate basis | Not provided/set | Before pilot readiness, with selected provider/runtime data. |
| Provider protocol and routing visibility | Scope is provider-neutral through explicit adapters; provider API, feature support, credentials, egress, upstream attribution, and cost remain unverified per provider/model | Phase 3 stores adapter/provider/model configuration and capability evidence; Phase 6 validates compatibility and enforces runner egress. Phase 1 may use any configured provider only after its isolated route and applicable spend controls are verified. |
| Single-host resource suitability | User-selected starting topology, not benchmarked | Phase 1 and pilot measurements; separate runner if evidence requires it. |

## 12. Phase 0 completion checklist

- [x] Product boundary and operational MVP bar recorded.
- [x] Confirmed baseline decisions recorded.
- [x] Domain state boundaries and invariants documented from PRD.
- [x] Provider scope recorded separately from runtime adapter choice; provider APIs require explicit adapters and per-model capability/egress evidence.
- [x] Proposed component responsibilities and trust boundaries documented; proposals labeled as unverified.
- [x] Runtime capability, security, performance, and budget unknowns assigned to explicit verification gates.
- [x] Review design baseline against the Phase 0 exit criteria and record any changes in `progress.md`.
