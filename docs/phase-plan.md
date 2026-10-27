# Agent Office — Implementation Phase Plan

**Status: Phase 0, Phase 1 feasibility, and Phase 2 are complete; Phase 3 is in progress.** The current P0 target is a real, bounded task run with safe activity visible in 3D—not full pilot readiness. The initial path uses one configured provider endpoint; multi-provider validation, child delegation, large-scale NFRs, and advanced recovery are deferred.

**Product source:** [`../PRD.md`](../PRD.md)  
**Progress log:** [`progress.md`](progress.md)  
**Current design baseline:** [`phase-0-design.md`](phase-0-design.md)

## Purpose and status rules

This roadmap translates the P0 requirements and delivery stages in the PRD into implementation phases. It is not an implementation claim. A phase is **Not started** until work begins, **In progress** while its deliverables are being produced, **Blocked** when an explicit dependency prevents progress, and **Complete** only when its exit criteria have evidence recorded in `progress.md`.

Phase 1/M0 established bounded provider-inference and 3D feasibility only. The first live run still requires the minimal Agent Office Engine/Runner, scoped tool allowlist, per-run bounds, and genuine lifecycle events. Provider pricing/account spend limits remain an explicit risk; never claim a `:free` route is zero-cost. Phase 2 is complete and Phase 3 is in progress.

## Confirmed planning baseline

The following choices were confirmed in the planning conversation:

- P0 must execute real Tasks and show verified activity in 3D; simulated activity is never operational evidence.
- Use the PRD's recommended Next.js/TypeScript, React Three Fiber, FastAPI, PostgreSQL, Python dispatcher/runner, and Docker Compose as a baseline to validate, not as proven implementation choices.
- Agent Office owns the task/tool loop and event attribution. No specific coding-agent product is a P0 dependency.
- Start with one configured OpenAI-compatible provider endpoint. Preserve the provider-neutral Adapter boundary; additional adapters/native APIs are P1.
- Pilot baseline remains one owner, private workspace, and one repository/workspace through an isolated Runner.
- Several Agent Instances may run independent Tasks concurrently; child delegation is P1.
- Tools in the configured workspace allowlist run without per-call approval. Destructive/out-of-scope actions and new egress remain policy/approval-gated.
- Bound each Run by configured inference turns/tokens, execution time, and tool policy; unknown upstream and price are not represented as free.
- Start with a readable, simple 3D office template; defer detailed art direction until readability/performance evidence exists.
- Import Markdown system prompts; optional Skill resources are a P1 extension and never grant permissions or execute scripts merely because uploaded.
- Set monetary budget values before pilot; do not claim a hard spend cap unless the relevant provider/router enforces it.
- Use the PRD's proposed retention values as configurable pilot candidates; they are not a gate for the focused local visualization slice.
- Do not assign calendar estimates before team capacity is known.

### Focused P0 build order

`Phase 3 core records/config → Phase 6 minimal Engine/Runner → Phase 4 lifecycle/safe-action events → Phase 5 live 3D projection → Phase 8 focused acceptance.` Phase 5 scene work may continue now, but it cannot claim live agent visualization until Phase 4 and Phase 6 events are connected. Phase 7 and broader Phase 8 pilot evidence are deferred.

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

## Phase 1 — M0: Provider API and 3D feasibility

**Status: Complete for the user-directed provider-inference and 3D feasibility scope (2026-10-26).** One bounded, non-streaming Chat Completions request through the configured 9Router endpoint returned a correlated text completion using `openrouter/cohere/north-mini-code:free` (response model `cohere/north-mini-code:free`; request ID `gen-1792987848-yosMf7dxWMAVHf0bu7DO`). The separate Gemma `:free` route returned HTTP 503. The evidence matrix records only the tested endpoint/route capabilities. This closes feasibility, not product integration, zero-cost assurance, or operational Managed readiness.
- **Execution boundary:** Model providers return inference output only. Agent Office must own any future task loop, tools, workspace policy, approvals, lifecycle, and event attribution. A provider response never authorizes tool execution.

### Work
- Probe bounded, direct OpenAI-compatible Chat Completions requests without launching OMP, Codex, or another coding-agent product. Use a local mock for protocol/error tests; any real-provider request requires an explicitly configured endpoint and enforced account/egress limits.
- Record request/response, model/route, and tested capabilities for each provider endpoint; do not infer support from a provider name or compatible base URL. Test multiple providers only where credentials and safe limits are available.
- Determine which features the compatible contract actually covers, including streaming and tool calls, and represent unsupported features explicitly. Native APIs require separate Provider Adapters; they are not implied by Chat Completions compatibility.
- Keep provider inference separate from an agent tool loop, filesystem access, task lifecycle, approvals, and safe execution; those remain Agent Office responsibilities and unproven.
- Retain previous OMP/Codex findings as historical M0 evidence, not as product integration targets or Phase 1 completion evidence.
- Retain the no-provider local egress and resource-control probes as generic feasibility evidence.
- Produce a capability matrix that distinguishes verified, unsupported, and unknown behavior.
- Build a small 3D scene spike with player movement, diagonal/follow camera, collision, click-to-move pathfinding, and basic interaction.
- Measure target-device responsiveness and render performance; check reduced-motion and keyboard focus behavior.

### Deliverables
- Reproducible direct-provider API and 3D feasibility evidence, supported/unsupported feature matrix, integration risks, and explicit blockers. No provider is supported solely because it advertises OpenAI compatibility.
- 3D feasibility evidence and a list of scene limitations.

### Exit criteria
- A direct, bounded Chat Completions inference request is observed through the proposed Provider Adapter and its response is correlated to that request; this is inference evidence only, not a completed agent task.
- Provider/model/endpoint features are recorded as verified, unsupported, or unknown; every untested combination remains unsupported.
- A direct bounded inference request is feasibility evidence only. A P0 live task additionally requires an Agent Office-owned Engine/Runner, isolated workspace/tool allowlist, per-run bounds, and genuine lifecycle events; child delegation, additional provider adapters, and pilot-scale budgets do not block this focused path.
- Character movement and collision are viable on the provisional target profile; pilot hardware and lifecycle performance caveats remain recorded.
- Do not claim universal provider compatibility or Managed MVP from protocol compatibility or an inference response.
- If the minimum Managed behavior cannot be supported safely, keep it blocked and revise scope explicitly; do not relabel Observe or simulated activity as Managed MVP.

## Phase 2 — Application foundation and private pilot deployment

Phase 2 is complete. Phase 1/M0 closure records provider-inference and 3D feasibility only; it did not implement managed execution. The approved focused P0 Engine/Runner is still planned in Phase 6.

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

**Status: In progress — workspace policy/tool allowlist, Runner registration/heartbeat, idempotent Run dispatch, and policy preflight are implemented. Restricted-role PostgreSQL 17 integration against an isolated `*_test` database verifies migrations 0009–0010, policy and Run snapshots, dispatch, workspace-path serialization, lease expiry to `unknown`, and event projections. Exit criteria remain open pending full API/Runner integration and end-to-end runtime evidence.**

### Work
- Keep the minimal reusable system-prompt Agent Definition and persistent idle Agent Instance flow; Run snapshots freeze the selected prompt version.
- Preserve source Markdown and unmapped metadata; parse front matter as data only.
- Implement the P0 core Task/Run/activity-event/scene records and scoped approval records. Child delegation models, Skill catalogue/import/preflight, archive history, and full audit/history workflows are P1.
- Validate workspace/tool policy before execution; do not execute uploaded scripts.
- Add stable IDs, database constraints, idempotency scopes, and task dependency cycle checks.
- Provide usable list/detail views before 3D is available.
- Configure one Provider Adapter endpoint for the focused path and persist its non-secret selected model/route snapshot. Detailed capability matrices and multiple provider configurations are P1.

### Exit criteria
- PRD AC-01–03, AC-05, and persistence portions of AC-19 pass with evidence; Skill AC-04 and archive AC-22 are P1.
- Task revisions, immutable Run snapshots, provider configuration, and migrations 0008–0010 have restricted-role PostgreSQL evidence from an isolated PostgreSQL 17 `*_test` database. API acknowledgement/idempotency and Runner outbox restart behavior have separate tests; full service-restart reconciliation remains pending.
- Archived definitions remain historically inspectable and do not start new runs when the P1 archive workflow is implemented.
- PRD FR-19 configuration API and Run-snapshot portions are implemented: responses and snapshots include only adapter/endpoint/model/route/version, never credentials. API/UI fixture paths and provider persistence passed; provider execution and egress acceptance remain in Phase 6.

## Phase 4 — Durable event pipeline and work views

**Status: In progress — authenticated internal Runner heartbeat/claim/event routes, idempotent event acknowledgement, allowlist-derived action summaries, durable event projections, owner-scoped reads, reconnect snapshot/delta WebSocket, and API-backed Run/office views are implemented. Restricted-role PostgreSQL and API fixture tests pass; full Runner-to-API-to-PostgreSQL end-to-end verification remains open.**

### Work
- Persist and project lifecycle events with scope validation, event-ID deduplication, monotonic latest-state rules, terminal-state protection, and owner cursors. The restricted-role repository behavior is verified against isolated PostgreSQL 17; restart/reconnect remains unverified.
- Authenticate internal Runner registration/heartbeat, claim, and event ingestion with the shared Runner token; derive event scope from the persisted dispatch, acknowledge only after durable event persistence, and deduplicate replayed event IDs. Safe action summaries come only from the fixed tool allowlist; never expose model reasoning or file contents.
- Add owner-authenticated snapshot and Run event reads plus an authenticated WebSocket snapshot/delta feed; client views show projection freshness independently of browser socket health.
- P0 views: Agent List/3D inspector and Run Detail use persisted projections/events when available. No agent position or action summary is inferred from lifecycle events. Full Inbox, searchable event history, broad timeline, and advanced replay are P1.
- Never store/display hidden chain-of-thought or unrelated sensitive content.

### Exit criteria
- P0 AC-11, AC-12, AC-16, AC-19, AC-21, and AC-34 pass with evidence.
- The schema and repository code encode scoped persistence, deduplication, monotonic latest-state projection, terminal-state protection, action summaries, and Runner event acknowledgement; isolated PostgreSQL 17 integration passed. Full process-restart/reconnect reconciliation remains pending.
- Browser snapshots reconcile owner-scoped state, then WebSocket deltas continue from the snapshot cursor; client status/freshness remains separate from browser connection health.

## Phase 5 — Explorable 3D office

**Status: In progress — the single office-template scene restores only the owner's browser-local avatar position and shows persisted Agent Instance identities in its list/inspector. Office and Run Detail can consume authenticated persisted event projections, but no real Runner-generated Run has populated them; no verified 3D agent markers, work locations, or live activity are claimed. Server-backed scene version and AC-34 evidence remain open.**

### Work
- Implement versioned template scene, player avatar, walkable grid, colliders, camera controls, and safe spawn points.
- Add keyboard and click-to-move, pathfinding, minimap, focus-to-agent, search/filter, and Overview/Explore modes.
- Add interactions for agent, desk, project board, and doors; opening a context does not itself send a task or model message.
- Connect scene badges, safe activity summaries, freshness, and avatar destinations to the committed event projection from Phase 4; keep home desks stable and move avatars only after verified assignment/start events.
- Add keyboard-accessible HTML alternatives, visible focus, reduced motion, mobile/tablet inspection paths, and no-WebGL fallback.

### Exit criteria
- P0 AC-23–31 and AC-34 pass with evidence; AC-34 uses two independent Tasks/Runs, not child delegation.
- Character/camera presentation never writes job state or grants permission.
- Performance targets are measured on a recorded target profile; unmet targets are reported, not hidden.

## Phase 6 — Minimal Agent Office Execution Engine and Runner

**Status: In progress — a Compose Runner process now claims persisted Runs, executes bounded Chat Completions tool loops over the three-file-tool allowlist, and writes authenticated lifecycle/action events through a SQLite WAL outbox. Restricted-role PostgreSQL, API auth/ack tests, and a local HTTP-mock tool-loop smoke pass. No real configured-provider Run has been observed. OS-enforced egress, cancel acknowledgement, artifact/final-answer persistence, and full restart reconciliation remain open.**

### Work
- Implement authenticated Runner registration and durable, idempotent Task/Run start/acknowledgement for the focused path.
- Run the Agent Office Execution Engine in an isolated workspace per Run; keep supervisor credentials and Docker access outside the agent process.
- Use one configured Provider Adapter endpoint; protect credentials and enforce egress only to that endpoint.
- Enforce workspace/project scope and a pre-approved tool allowlist without approval at every call. Require policy/approval for destructive actions, out-of-scope resources, or new egress.
- Bound inference turns/tokens, execution duration, and tool calls per Run; disclose unknown upstream/provider cost rather than imply free usage.
- Emit verified start, safe tool-action summary, waiting, terminal result, heartbeat/freshness, and cancel acknowledgement events; do not emit model reasoning as status.
- Support multiple Agent Instances on separate Tasks with a separate workspace per Run; avoid concurrent writes to one checkout through isolated worktrees or explicit serialization.
- Implement basic cancel request/acknowledgement, bounded retry after known failure, and no automatic retry from uncertain state.
- Enable only tested capabilities; pause/resume, child delegation, multiple providers/native APIs, and advanced runtime controls are deferred.
- Disable unsupported controls and explain the adapter limitation.

### Exit criteria
- PRD AC-05–07, AC-10–11, AC-14, AC-17–18, AC-21, and AC-34 pass.
- A Run becomes Running only after Engine acknowledgement; active action labels derive from real events.
- Cancel remains pending until acknowledged; uncertain attempts stay stale/unknown and are not retried blindly.
- One configured endpoint and allowlisted tools work under explicit per-run bounds; credentials and egress stay scoped.

## Phase 7 — Delegation and advanced recovery (P1, deferred)

**Status: Deferred — not a prerequisite for the focused 3D visualization path. Basic stale-state handling and safe retry rules remain P0 in Phases 4/6.**

### Work
- Implement explicit parent-run → child-task → child-run with user approval per child, bounded scope, and definition version.
- Enforce delegation depth/concurrency/duration limits and least-privilege intersection.
- Add advanced event spool/replay, lease fencing, multi-run recovery, and conflict recovery beyond the P0 snapshot/stale path.
- Verify extended retention, deletion/audit policies, and export excluding credentials.

### Exit criteria
- P1 AC-08–09 and AC-13 pass; child failures/access/parentage remain correctly scoped.
- Advanced recovery never replays uncertain side effects automatically.

## Phase 8 — Focused P0 acceptance and deferred pilot readiness

**Status: Not started — acceptance for the focused agent/3D path only; full pilot readiness and target-profile scale are P1.**

### Work
- Run all P0 PRD acceptance criteria and record evidence; label P1 criteria as deferred.
- Demonstrate two independent Agent Instances running separate real Tasks; observe verified lifecycle/safe-action events in 3D and list/detail; handle stale/disconnect without false status.
- Measure the focused scene/event path and baseline accessibility. The proposed 50-avatar/10-Run and pilot hardware NFR targets remain P1.
- Record the configured endpoint, per-run limits, workspace/tool policy, known cost uncertainty, and operator recovery steps.

### Exit criteria
- P0 acceptance criteria pass with traceable evidence from real Engine/Runner events; simulation cannot substitute.
- Basic stale, reconnect, safe cancellation, and isolated workspace behavior are verified.
- Pilot budget, broader recovery, retention, and scale readiness are explicitly deferred, not implied complete.

## Phase 9 — Post-MVP evolution

**Status: Not started; begins only after pilot evidence and product prioritization**

Candidate work: child delegation and parent/child recovery, more Provider Adapters/native APIs, advanced replay/fencing, 50-avatar/10-Run and pilot-profile performance, GitHub/GitLab connectors, reusable workflows, multi-operator support, and external artifact storage. None is a prerequisite for the focused P0 visualization path.

## Cross-phase invariants

- Runtime evidence is the source for AI work state; visual animation is presentation only.
- Runtime session, agent identity, task, run/attempt, and task acceptance remain distinct.
- No retry after an uncertain attempt until reconciliation or explicit isolation.
- No permission is granted by proximity, room/desk placement, prompt text, or skill attachment.
- Missing telemetry is shown as unknown/unavailable, never fabricated as zero, progress, success, or skill use.
- Every phase change, decision, blocker, and verification result is recorded in [`progress.md`](progress.md).

## User-directed UI-first sequence

**Current status: Phase 1/M0 feasibility is complete only for bounded provider/3D probes; Phase 2 is complete; Phase 3 remains in progress with workspace policy and allowlist persistence plus restricted-role PostgreSQL evidence. Phase 4 has authenticated Runner event ingestion, acknowledgement, and safe-action projection code with component-level evidence, but no full service-restart/end-to-end proof. Phase 5 has no verified live 3D run. Phase 6 is in progress with bounded Runner implementation, but no real configured-provider execution, OS-enforced egress, or cancel acknowledgement evidence.**

The agreed UI contract:
- After login, the 3D office is the desktop home; Agent List, Task Board, Run Detail, and Settings stay one click away. Inbox and Skill-library workflows are P1.
- Use a warm, stylized low-poly visual direction.
- Use persisted Agent Instance APIs where available. Use deterministic fixtures visibly labeled “Sample” only for domains without real APIs; never fabricate agent/task state. The office's static geometry is a template, while agent status/activity/location require canonical runtime/event data.
- Enable Start/Assign only after the minimal authenticated Engine/Runner, workspace allowlist, and real event path exist; unsupported/destructive actions still require policy/approval. Never simulate work state.
- Preserve tablet/mobile list/detail fallback, keyboard access, reduced motion, WebGL fallback, and state separation.

**Current status: Phase 1/M0 feasibility is complete only for bounded provider/3D probes; Phase 2 is complete; Phase 3 remains in progress with migration 0008 and preflight checks implemented but PostgreSQL evidence and workspace policy/allowlist configuration open; Phase 4 has event reads/projections and views implemented but no authenticated Runner writer or safe-action mapping; Phase 5 has persisted Agent Instance identities and event-projection display but no verified live runtime/location; Phase 6 remains not started.**

UI previews for domains without persisted APIs remain labeled “Sample” and cannot start work. Project, Definition, Agent Instance, Task, and bounded Run/Attempt creation use owner-scoped APIs; optional Skill routes remain a P1 feature. Run records stay `created` until the P0 Engine/Runner accepts them; live 3D requires real lifecycle/safe-action events.
