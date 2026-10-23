# Agent Office — Implementation Phase Plan

**Status:** Phase 0 and Phase 2 complete; Phase 1/M0 remains in progress. UI-only P0 presentation previews span work in Phases 3–8; those phases' operational exit criteria remain open.

**Product source:** [`../PRD.md`](../PRD.md)  
**Progress log:** [`progress.md`](progress.md)  
**Current design baseline:** [`phase-0-design.md`](phase-0-design.md)

## Purpose and status rules

This roadmap translates the P0 requirements and delivery stages in the PRD into implementation phases. It is not an implementation claim. A phase is **Not started** until work begins, **In progress** while its deliverables are being produced, **Blocked** when an explicit dependency prevents progress, and **Complete** only when its exit criteria have evidence recorded in `progress.md`.

No calendar estimates are assigned. Phase 1/M0 remains in progress; Phase 2 is complete with its exit-criteria evidence recorded in `progress.md`. The production web app now exposes authenticated API-backed owner-scoped project routes plus sample-labeled previews for later domain areas; those later-phase previews remain presentation-only. The isolated M0 runtime and 3D spikes remain feasibility evidence, not operational application behavior.

## Confirmed planning baseline

The following choices were confirmed in the planning conversation:

- MVP must operate on a real runtime; a simulated 3D prototype is not the operational MVP.
- Use the PRD's recommended Next.js/TypeScript, React Three Fiber, FastAPI, PostgreSQL, Python dispatcher/runner, and Docker Compose as a baseline to validate, not as proven implementation choices.
- Core uses a vendor-neutral Runtime Adapter contract; OMP is the existing prototype candidate, Codex app-server is an unverified candidate, and ACP is a protocol for compatible runtimes. No engine is a permanent product dependency; each adapter must pass the same lifecycle, capability, and safety gates before it is supported.
- Model-provider scope is protocol-neutral and separate from Runtime Adapter choice. A connection selects an available Provider Adapter, provider/model or route, optional endpoint, and credential reference. Native provider APIs and compatible endpoints are supported only when an adapter exists and required capabilities pass; no adapter means fail closed.
- Pilot scope: one owner, one private workspace, several projects, one configured repository/workspace through the runner.
- Start with a single private Linux host and an isolated runner process. Reconsider separation if M0 shows that security or resource needs require it.
- Begin with a readable, simple 3D office template; defer detailed art direction until readability and performance have been tested.
- Import Markdown prompts and text-based skill resources; uploading a skill does not execute scripts or grant permissions.
- Task assignment is explicit. Delegation is explicit and limited to registered/allowed agent definitions.
- Enforce execution limits and set monetary budget values before the pilot. Do not claim a hard spending cap unless the runtime can enforce it.
- Use the PRD's proposed 30-day detailed event/log and 90-day definition/run-summary/approval/artifact retention as configurable starting values.
- Do not assign calendar estimates before M0 and team capacity are known.

## Phase 0 — Product contract and design baseline

**Status: Complete** — documentation baseline and decision/open-question gates recorded in `progress.md`.

### Work
- Record the accepted product boundary, terminology, invariants, and confirmed decisions.
- Draft the architecture/data-flow and domain boundaries without presenting proposals as implemented behavior.
- Define event/command reliability, permissions, runner isolation, version snapshots, recovery behavior, and capability validation requirements.
- Keep unresolved choices and spike-dependent claims explicit.

### Deliverables
- This phase plan and [`progress.md`](progress.md).
- [`phase-0-design.md`](phase-0-design.md), a design baseline derived from the PRD and confirmed choices.

### Exit criteria
- Product and domain invariants are documented without conflating task, run, connection health, visual state, or task acceptance.
- Architecture responsibilities and trust boundaries are explicit.
- Every unverified runtime/deployment assumption is labeled and has a verification owner/phase.
- Open design decisions are recorded; no unsupported capability is represented as available.

## Phase 1 — M0: Runtime and 3D feasibility spikes

- **Status: In progress** — the provisional managed-browser profile (1440×900 CSS / DPR 1.25) passed the narrow active 50-avatar/10-stream walking/follow-camera workload (RAF samples 51/41/43/56/52 FPS); idle-only and hidden/headless results varied below 30 FPS and remain caveats. The real-task criterion is met narrowly through the isolated OMP prototype: `cl/thinkingmachines/inkling-small:free` completed one synthetic no-tools task with one HTTP 200 provider request and a settled result correlated by request ID; OMP activity attribution is adapter-generated for a single active-task window. Runtime-neutral architecture is documented. Codex app-server's isolated initialize handshake passed with temporary `CODEX_HOME`; no task/provider request was sent. Orca CLI is selected for orchestration only; Agent Office owns the future approval flow, which remains unimplemented, and OMP lacks a bound invocation ID. Managed gates remain open: approval identity/scope, default child scope, production runner isolation/resource enforcement, active health, safe cancellation, interrupted-task reconciliation, tool capability, and applicable spend limits. Phase 2 proceeds only by explicit user authorization.
- **Approval boundary:** User chose Orca CLI for orchestration only. Orca's documented Task-DAG decision gates are not runtime tool approvals. Agent Office owns the future per-call approval flow; it remains unimplemented and fail-closed because OMP's observed approval frame lacks `toolCallId`.

### Work
- Use the existing OMP RPC prototype to record lifecycle and safety capability evidence: start, activity events, terminal result, heartbeat/health, follow-up input, approval/dialog, cancel request and acknowledgement, and constrained sub-agent/delegation behavior.
- Evaluate Codex app-server as an alternate runtime candidate against the same canonical contract; run a bounded task smoke only if local authentication/configuration and safety constraints permit. Otherwise record the exact missing prerequisite and keep Codex unsupported. Assess ACP only as a protocol path for a compatible execution runtime. Do not treat an orchestrator or screen reader as an execution runtime without an identified agent-runtime API.
- Record Orca CLI as orchestration-only; do not repurpose its coordinator-owned Task-DAG gates for runtime tool approval. Keep the Phase 1 approval blocker open until Agent Office can enforce a call-bound decision and a runtime or mediated path supplies stable invocation identity.
- Test disconnect, timeout, duplicate/late events, session completion semantics, and replay/snapshot options.
- Probe endpoint-restricted egress with a local mock and no provider credentials; record this as bridge feasibility only, not OMP/provider integration evidence.
- Produce a capability matrix that distinguishes verified, unsupported, and unknown behavior.
- Build a small 3D scene spike with player movement, diagonal/follow camera, collision, click-to-move pathfinding, and basic interaction.
- Measure target-device responsiveness and render performance; check reduced-motion and keyboard focus behavior.

### Deliverables
- Reproducible spike notes, adapter-neutral lifecycle/capability matrix, candidate evidence (task-tested or explicit blocker), integration risks, and recommendation. No candidate is supported solely because it implements a named protocol.
- 3D feasibility evidence and a list of scene limitations.

### Exit criteria
- A real task is observed through the adapter and produces attributable event/result evidence; simulations do not count.
- Managed capabilities required by P0 are either demonstrated or explicitly identified as a blocking gap.
- Character movement and collision are viable on the target profile.
- Runtime-neutral core boundary is documented, and at least one candidate has task evidence; any untested candidate is clearly marked unsupported with the missing verification stated.
- If minimum Managed behavior cannot be supported safely, revise scope explicitly before proceeding; do not relabel Observe or simulated activity as Managed MVP.

## Phase 2 — Application foundation and private pilot deployment

**Status: Complete.** The private web/API/database foundation, owner-scoped project routes, restricted API database role, one-shot migration runner, and dispatcher/runner boundaries are implemented and verified. Phase 1/M0 remains in progress; Phase 2 completion does not claim runtime, task, or execution readiness.

### Work
- Scaffold the approved repository structure for web app, API, dispatcher, runner, database migrations, and tests.
- Add local development configuration and Docker Compose for the private single-host pilot.
- Implement owner authentication, server-side project/resource scope checks, secure session defaults, and WebSocket origin validation.
- Establish structured logs, correlation IDs, configuration/secrets boundaries, backup, and restore procedure.

### Exit criteria
- Services start reproducibly from documented instructions and configuration contains no committed secret.
- Owner/project scope is enforced server-side; browser cannot connect to a shell/CLI or database.
- A backup restore is demonstrated, not merely configured.

## Phase 3 — Canonical domain, configuration, and persistence

**Status: In progress — UI-only slices remain present.** Phase 2's application foundation is complete; canonical domain models, provider/runtime configuration persistence, and domain migrations remain unimplemented.

### Work
- Implement versioned agent definitions and skills, links, import/export, archive, and configuration snapshots.
- Implement projects, agent instances, task revisions, runs/attempts, delegations, approvals, events, artifacts, audit entries, and office scene versions.
- Preserve source Markdown and unmapped metadata; parse front matter as data only.
- Validate skill/resource/dependency compatibility before execution; do not execute uploaded scripts.
- Add stable IDs, database constraints, idempotency scopes, and task dependency cycle checks.
- Provide usable list/detail views before 3D is available.
- Add versioned provider-connection configuration, separate from Runtime Connection: Provider Adapter ID/protocol, provider/service ID, model/route ID, optional endpoint/base URL, and optional secret reference. Keep secret values out of configuration exports and run snapshots.
- Persist per-endpoint/model capability state and evidence separately from declared compatibility; preserve the selected non-secret endpoint/model configuration version in each run snapshot.

### Exit criteria
- PRD AC-01–05, AC-22, and persistence portions of AC-19 pass with evidence.
- Definition/skill edits create new versions; existing runs retain their original effective configuration.
- Archived definitions remain historically inspectable and do not start new runs.
- PRD FR-19 configuration and persistence portions pass; run snapshots identify the chosen endpoint/model/version and contain no secret values. Provider execution and egress acceptance remain in Phase 6.

## Phase 4 — Durable event pipeline and work views

**Status: In progress — the authorized UI-only slice now has a Sample-data Task Board, Run Detail, and Inbox with local search/filter behavior. Durable event ingestion, projections, acknowledgements, WebSocket freshness, and reconciliation remain unimplemented and depend on Phase 3 contracts.**

### Work
- Implement event ingestion, source/scope validation, deduplication, durable acknowledgement, cursor/snapshot handling, and event projections.
- Add WebSocket snapshot/delta delivery, reconnect, freshness indicators, and reconciliation.
- Keep run execution state, connection health, task acceptance, user actions, runtime events, and application summaries distinct.
- Implement Agent List, Task Board, Run Detail, timeline, artifact links, and Attention Inbox.
- Paginate/truncate large output and avoid storing/displaying hidden chain-of-thought or unrelated sensitive content.

### Exit criteria
- PRD AC-11–12, AC-16, AC-19, and AC-21 pass.
- Acknowledged events survive restart; duplicate/late events do not create duplicates or regress terminal state.
- Disconnection never invents a terminal state; freshness and unsupported evidence are visible.

## Phase 5 — Explorable 3D office

**Status: In progress — a presentation-only low-poly office, local owner avatar, sample-agent avatars, movement, camera, minimap, object focus, accessibility path, and WebGL fallback are implemented. Runtime-derived state, versioned layouts, connected events, and the full AC-23–31 verification remain open; this is not the operational runtime office.**

### Work
- Implement versioned template scene, player avatar, walkable grid, colliders, camera controls, and safe spawn points.
- Add keyboard and click-to-move, pathfinding, minimap, focus-to-agent, search/filter, and Overview/Explore modes.
- Add interactions for agent, desk, project board, and doors; opening a context does not itself send a task or model message.
- Map verified job state to visual badges/animations; freeze work animation when data is stale.
- Add keyboard-accessible HTML alternatives, visible focus, reduced motion, mobile/tablet inspection paths, and no-WebGL fallback.

### Exit criteria
- PRD AC-23–31 pass with evidence, including blocked destinations, panel focus, offline exploration, scene version changes, and WebGL/assets failure.
- Character/camera presentation never writes job state or grants permission.
- Performance targets are measured on a recorded target profile; unmet targets are reported, not hidden.

## Phase 6 — Managed runner, task commands, and controls

**Status: Not started for runner/runtime implementation. Provider/runtime configuration and disabled-action surfaces exist as UI previews; Phase 1 capability evidence and Phases 2–4 operational foundations remain prerequisites.**

### Work
- Implement durable command/outbox dispatch, request idempotency, leases, acknowledgements, retry/deduplication, and runner registration/authentication.
- Run the selected Runtime Adapter and execution runtime in an isolated process/workspace with least-privilege access; keep supervisor credentials and Docker access outside the agent process.
- Add task preflight for project permission, resource scope, capacity, dependencies, skill compatibility, and adapter capabilities.
- Resolve the selected Provider Adapter, provider/model/route, endpoint, and credential reference for each run; pass credentials only to the isolated runtime path that requires them, never to browser state, prompt text, or general logs.
- Enforce default-deny runner egress to the exact endpoint approved by the selected adapter; permit local provider/router destinations only when explicitly configured, and reject unrelated/internal destinations.
- For host-local models/routers, expose only the explicitly configured endpoint through a dedicated least-privilege bridge; do not disable network isolation or share unrestricted host networking. Verify the bridge cannot reach unrelated local services.
- Preflight required capabilities per adapter/provider/model/endpoint (including streaming and tool calls); fail closed or disable unsupported functionality rather than infer compatibility from a protocol label.
- Verify at least two available provider adapters and treat supported protocols per adapter; do not require an OpenAI vendor or OpenAI-compatible protocol. Unsupported adapters/capabilities fail closed; upstream provider and cost remain unavailable unless the gateway reports them.
- Implement FIFO queueing, one active run per instance, follow-up input, scoped approval, cancel request/acknowledgement, retry as a new attempt, and artifact attribution.
- Disable unsupported controls and explain the adapter limitation.

### Exit criteria
- PRD AC-04, AC-06–07, AC-10–11, AC-14, AC-17, and AC-21 pass.
- A run becomes Running only after suitable runtime acknowledgement.
- Cancel remains pending until acknowledged; uncertain old runs cannot be retried blindly.
- PRD AC-33 passes for two available provider adapters without requiring an OpenAI vendor or OpenAI-compatible API; unsupported adapters/capabilities fail closed, secrets remain isolated, and unapproved egress is denied.

## Phase 7 — Delegation, repository isolation, and recovery

**Status: Not started for delegation/isolation/recovery implementation. A read-only delegation/recovery UI preview exists; Phase 6 runtime contracts remain a prerequisite.**

### Work
- Implement explicit parent-run → child-task → child-run relationships, registered definition/version, bounded input, project scope, and budget.
- Enforce configured child/depth/concurrency/duration limits and least-privilege intersection; avoid scheduler slot deadlocks.
- Provide isolated worktree/workspace per parallel run or explicit serialization; expose source/output revision where available.
- Implement runner event spool/replay, lease recovery, reconciliation, and fencing before replacement attempts.
- Verify retention, deletion consequences, audit behavior, and export excluding credentials.

### Exit criteria
- PRD AC-08–09, AC-13, AC-15, and relevant AC-18–19 pass.
- Child failures do not imply parent success; child access does not exceed authorized scope.
- Parallel runs cannot silently write to the same checkout; uncertain prior side effects are not replayed automatically.

## Phase 8 — MVP acceptance and pilot readiness

**Status: Not started for acceptance/pilot execution. A readiness checklist UI preview exists; no operational MVP scenario or target-profile NFR acceptance has been run.**

### Work
- Run all PRD AC-01–32 and record evidence and failures by scenario.
- Exercise the required live demo: explore the office while a real task produces an artifact, perform a parent/child handoff, handle an approval or failure, and recover from a disconnect without false status.
- Measure applicable NFR targets independently: event latency, initial load, render/input responsiveness, event load, restore/reconciliation, durability, accessibility, security, and pilot concurrency.
- Set actual pilot budget values for the selected provider/runtime; configure execution limits and the accepted retention values.
- Document capability limitations and operator recovery procedures.

### Exit criteria
- All agreed P0 acceptance criteria pass with traceable evidence; simulations are not substituted for the required real-runtime demo.
- NFR results are measured and reported against a recorded profile.
- Pilot budget, backup/restore, isolation, retention, and known limitations are explicit.

## Phase 9 — Post-MVP evolution

**Status: Not started; begins only after pilot evidence and product prioritization**

Candidate work: additional Runtime Adapters (including Codex app-server or ACP-compatible runtimes after qualification), provider adapters, GitHub/GitLab connectors, reusable workflows, more scene/layout capability, multi-operator support, and external artifact storage. Each item requires a separate priority decision, design, and acceptance criteria; none is assumed to be part of MVP.

## Cross-phase invariants

- Runtime evidence is the source for AI work state; visual animation is presentation only.
- Runtime session, agent identity, task, run/attempt, and task acceptance remain distinct.
- No retry after an uncertain attempt until reconciliation or explicit isolation.
- No permission is granted by proximity, room/desk placement, prompt text, or skill attachment.
- Missing telemetry is shown as unknown/unavailable, never fabricated as zero, progress, success, or skill use.
- Every phase change, decision, blocker, and verification result is recorded in [`progress.md`](progress.md).

## User-directed UI-first sequence

On 2026-10-23, the user authorized prioritizing the application UI through the 3D office while Phase 1 remains open. Complete UI-relevant Phase 2 work, then proceed with UI-only slices of Phases 3–5. The latest direction extends this to finish all P0-facing UI previews across Phases 3–8 while backend/runtime integration remains deferred. This is an explicit sequencing exception, not a claim that Phase 2 or later phases are complete.

The agreed UI contract:
- After login, the 3D office is the desktop home; Agent List, Task Board, Run Detail, Inbox, and Settings stay one click away.
- Use a warm, stylized low-poly visual direction.
- Use local deterministic fixtures visibly labeled “Sample” until real domain/event APIs exist. They are not canonical records or runtime evidence.
- Camera, navigation, search, filters, panels, and player movement may work locally. Start, Cancel, Assign, and Approval remain disabled with a reason until a real connected path exists; do not simulate work state.
- Preserve the PRD's tablet and mobile list/detail fallback, keyboard access, reduced-motion behavior, WebGL fallback, and state separation.

Phase 2 remains in progress while its deferred backend boundaries/project scope are open. Phase 1 remains in progress; UI work does not close or waive its runtime safety gates. Phases 3–8 operational acceptance criteria remain open.

The current preview includes list/detail and draft/import surfaces where useful. It keeps sample state local, labels it as Sample, and disables persistence, dispatch, approval, assignment, and runtime-changing controls.
