# Agent Office — Implementation Roadmap

**Status (2026-11-09):** Roadmap Phases 1–4 are Complete. Phase 5 remains In Progress: on 2026-11-09, an Owner-only confirmed reconciliation endpoint was verified on disposable PostgreSQL using the provisioned least-privilege application role; audit `UPDATE`/`DELETE`/`TRUNCATE` remain denied. The full isolated API suite passed `160 tests` (`2` warnings; no skips). Attempts 55–58 provide bounded Owner-flow success, dependency-output, failure-blocking, and restart evidence. A two-app lifespan test verifies stale-controller fencing and the new corruption-reconciliation behavior. Runner execution during takeover, live Owner reconciliation, and the private API stop-and-snapshot procedure remain unverified; live API/Owner database operations were not performed. Phase 6 remains In Progress for authorized Codex gateway scope; its real-runtime acceptance is separate.
…

**Progress register:** [`progress.md`](progress.md)  
**Design baseline:** [`phase-0-design.md`](phase-0-design.md)  
**Design docs:** [`design-docs/README.md`](design-docs/README.md)

## Purpose and status rules

This roadmap maps the selected PRD into an implementation order. It separates PRD priority from current code evidence:

- **Not started**: no implementation evidence for the workstream.
- **In progress**: some implementation exists, but its PRD exit evidence is incomplete.
- **Blocked**: a named dependency prevents the next deliverable; record an owner and next action rather than carrying a generic blocker.
- **Complete**: all listed acceptance evidence is recorded in `progress.md`. Prior phase completion does not automatically complete a remapped workstream.

The PRD remains Draft. Proposals, user-confirmed design decisions, implemented behavior, and unverified capabilities must remain distinct.

**Phase naming:** Roadmap Phase 1–6 are engineering workstreams, not the two product milestones in PRD §30. PRD Phase 1 (Core Platform & 3D MVP) spans roadmap workstreams 1–4; PRD Phase 2 (Multi-Agent Workflow) maps to roadmap workstream 5. The split follows the new PRD's dependency order, not the superseded PRD. Use “roadmap phase” when reporting these six statuses.

## Confirmed planning decisions

- Follow the PRD's delivery order: Agent Registry → Agent Runtime → Event System → Virtual Office 3D → Multi-Agent Orchestration → external runtimes/integrations.
- Preserve P0/P1/P2 priorities; LangGraph orchestration remains P1 and P2 runtime spawning/integrations do not become P0.
- Owner-only authorization is P0; Operator/Viewer roles move to P1. This confirmed decision is reflected in PRD §25.2.
- One character represents each `Agent Instance`, replacing the proposed default of one avatar per `Agent Definition` in PRD §34.
- Definitions are archived rather than hard-deleted; no new Run may use an archived definition, while an active Run may finish.
- LangChain replaces the current custom completion loop through a direct cutover. Each Run receives an isolated container; egress is limited to its selected Model Endpoint.
- PostgreSQL is the canonical event store; Redis Streams is event fan-out. A Redis outage delays live delivery but must not lose committed events.
- Phase 3 P0 event taxonomy is Run lifecycle, heartbeat/freshness, cancellation, terminal outcome, safe tool-action summaries, and approval transitions; Agent/Task/workflow/delegation events are deferred. Preserve PostgreSQL Activity Event records during the pilot; cap Redis Streams near 100,000 entries. Temporary local benchmark target: 10 WebSocket clients, 100 events/second, p95 ≤1 second; this does not claim production capacity.
- Every delegated child Run requires owner approval. Allowlisted safe tools run without per-call approval; sensitive operations require a specific approval or are rejected.
- Model Endpoint is selected per `Agent Definition Version`; credential values remain in allowlisted server environment references.
- Cancellation is terminal only after worker stop is acknowledged/verified. An uncertain outcome remains pending/unknown and is not retried blindly.
- Migrations preserve IDs/history and do not delete records; schema constraints may change when required by the selected PRD, as migration 0011 does for duplicate Definition names. API cutover is breaking; all known callers move together.
- The temporary local Event System benchmark profile is confirmed; PRD/production performance capacity targets remain provisional until pilot hardware and network are selected. Demo mode, if enabled, is isolated and clearly labeled; it never proves real execution.
- Calendar estimates are not assigned without team capacity.
- The user authorized deferring Phase 2's remaining end-to-end verification while proceeding with Phase 3; the sequencing override did not waive acceptance criteria. Later evidence closed those Phase 2 gates, so Phase 2 is Complete.
- The Owner authorized continuing Phase 4 event visualization while Phase 3 remained In Progress. That sequencing override did not waive Phase 3 criteria; subsequent evidence closed Phase 3, and Phase 4 is now Complete on its own exit evidence.
- The Owner previously authorized P1 FR-3D-006 during Phase 4. On 2026-11-01 the Owner approved resolving all remaining Phase 4 blockers, including persisted Office Area Assignment (FR-3D-007) and dynamic allocation (FR-3D-008); the earlier deferral is superseded.
- On 2026-10-30 the Owner explicitly requested implementation of the Codex Subscription design before Phase 4 closed. This was a sequencing override for that Phase 6 integration only; it did not waive either phase's acceptance criteria.
- For Phase 4 reference testing, the Owner's previously selected managed-browser profile (1440×900 CSS at DPR 1.25) is accepted as the provisional reference profile. It is not a claim about a mobile device; GPU identity remains masked. Same-account Codex concurrency guards remain in force.
- Owner decision (2026-11-01): Reception is a separate shared physical room, not an assignable Office Area or default Agent Instance location. Keep the six persisted FR-3D-007 areas unchanged.

## Delivery sequence and current evidence

| Phase | PRD priority | Status | Current evidence and next acceptance gate |
|---|---|---|---|
| 1 — Agent Registry & Agent Factory | P0 | Complete | Owner-scoped Registry/Model Endpoint operations, immutable Definition versions, policy-limited tool resolution, invalid/disabled/archived rejection, and dynamic LangChain construction are covered by focused API (`23 passed`), PostgreSQL persistence (`1 passed`), and Runner execution (`2 passed`) tests. Attempts 24–25 provide real provider-backed Run and persisted-result evidence. This closes roadmap Phase 1 only; PRD Phase 1 spans workstreams 1–4 and remains open. |
| 2 — Agent Runtime | P0 | Complete | Live cancellation passed on Attempt 26; Attempt 27 (model `openrouter/cohere/north-mini-code:free`) succeeded after Runtime Launcher restart; Attempt 40 (`ag/gemini-3.8-flash`) succeeded after Runner restart; Attempt 41 (`ag/gemini-3.8-flash`) persisted `run.succeeded` and one Run Result across API restart. Runs `53bf4f84-c174-4b15-9870-c34fa1c5896b` and `37590e3e-d958-40af-9cd5-011b7c4262f2` both succeeded while overlapping on distinct workspaces using `ag/gemini-3.8-flash`. Test Project policy was restored disabled, and Definition concurrency was restored to 1. Earlier failed provider/restart attempts remain historical evidence; they do not negate the later passing acceptance runs. Remaining UI/3D reload observation belongs to Phases 3–4 and is not a Phase 2 blocker. |
…
| 3 — Event System | P0 | Complete | PostgreSQL event/projection/outbox, Redis Streams fan-out, authenticated snapshot/replay WebSocket, and frontend cursor/reconnect are implemented. The selected P0 taxonomy, pilot PostgreSQL retention, Redis cap, and temporary load profile are recorded. Approval transition persistence and migration `events_0017` passed isolated PostgreSQL integration. Real Run `3d4dc8e1-7552-4267-9f00-e37327c18368` survived a 6-second Redis outage; all 18 events replayed after recovery. Two isolated TCP fan-out runs delivered 1,000 events each to 10 clients at about 100 events/second; p95 599.32 ms and 132.99 ms. PostgreSQL outbox was excluded. After API process start at `2026-10-29T15:41:03Z`, browser auth/snapshot returned 200, its WebSocket was accepted, and the page showed the feed connected. The Owner decision route is live, but no current Run path creates approval requests, so approval-triggering execution is not claimed.
| 4 — Virtual Office 3D | P0 | Complete | FR-3D-005/006: persisted live Run lifecycle was shown Working at its workstation; a controlled persisted-heartbeat pause showed owner-scoped Offline/inactive at the workstation, then Idle after Runner recovery. FR-3D-007/008: `office_area_0020` is at head with the six-value check; persisted rows cover Architecture, Engineering, Review, Security, Meeting, Lounge; API/PostgreSQL tests cover explicit/automatic assignment, owner/archive boundaries, legacy backfill, unique workstations and growing layout. Reception is a separate shared physical room—not an assignment/default—with desk, sign and waiting seat inside the shared envelope and clearance from Meeting furniture. Full Web regression: 21 tests / 530 assertions; TypeScript and production build pass; rebuilt Compose web is healthy. Synthetic, explicitly labeled 20-instance fixture showed Reception on desktop and mobile; mobile 390×844 CSS had document width 375px/no overflow, all 20 selectable, and six assignable areas clearly separated from Reception. On the Owner-approved 1440×900 CSS/DPR1.25 Chrome156 reference profile (6 logical CPUs, 8 GiB reported), a focused/visible 20-instance synthetic scene measured 34.3 FPS over 5.099 seconds, above the proposed ≥30 FPS target. This is a local synthetic acceptance sample, not physical pilot evidence; GPU identity, uncached cold-load/asset transfer, draw calls, and physical mobile remain unverified. A focused independent review found no remaining Reception findings. Same-account Codex HTTP 429 remains a Phase 6 guard. Detailed evidence and limits: `progress.md`. |
| 5 — Multi-Agent Orchestration | P1 | In Progress | Persisted LangGraph checkpoint/controller, parent Task/Run planning, child Delegations/Runs, Owner approval, lease-fenced recovery, bounded outputs, private planning broker, P1 events, and workflow UI are merged to `master`. Full isolated API suite `160 passed, 2 warnings; no skips`; Launcher suite `72 passed`; prior integrated/security review of Phase 5 found no remaining findings. Attempts 55 and 57 completed exact-approval-gated dependent workflows; Attempt 55's result and `run.succeeded` survived reload with terminal checkpoint purge. Attempt 56's malformed output was rejected and its dependent blocked. Attempt 58 recovered an approved node after API restart with one successful dispatch, then completed the dependent and parent after exact approval/reload. Isolated two-app PostgreSQL takeover proves stale-holder fencing before child Run creation and one durable queued dispatch by the replacement; Runner execution during takeover remains unverified. The Owner-approved reconciliation API was exercised using the restricted application role: explicit Owner confirmation, active queued-child refusal without mutation, immutable audit snapshot, one `run.unknown`, Delegation/parent terminalization, corrupt checkpoint removal, idempotent replay, and active-workflow release all passed. Audit update/delete/truncate remain denied. Live Owner reconciliation and the stop-and-snapshot procedure remain open; no live API/Owner DB was modified.

| 6 — Extended runtime and product integrations | P1/P2 | In progress | Owner-authorized Codex gateway integration is implemented, and authorized read-only Tasks have succeeded through the private OMP broker and single-attempt gateway. An independent Codex+NineRouter live Run pair on distinct workspaces overlapped and both succeeded; this does not alter the separate same-account result: Runtime Launcher still rejects a second active worker using the same `OMP_GATEWAY_KEY` with HTTP 429. Same-account concurrency support remains unverified and the guard stays in place. Provider-only egress, quota/cost controls, cancellation/upstream stop, restart/recovery, credential refresh/revocation, and other Phase 6 integrations retain their existing gates.

## Cross-workstream invariants

- `Agent Definition`, `Agent Definition Version`, `Agent Instance`, `Task`, `Run / Attempt`, `Runtime Session`, `Activity Event`, and `Visual Agent State` remain distinct.
- Agent Office applies owner/project/tool policy; prompts, role labels, Skills, rooms, and avatar proximity never grant access.
- Provider inference is not execution evidence. Activity and terminal state come from authenticated, persisted runtime events.
- `Visual Agent State` is presentation-only; stale or missing evidence is displayed as unknown/unavailable, not inferred as work.
- Every event is persisted before acknowledgement. Redis is not the source of canonical event history.
- Preserve historical IDs and records; archive instead of hard-delete. API breaking cutover updates all known callers together.
- A demo mode may use simulated content only when explicitly selected and visibly isolated; it is never the live office's operational state or acceptance evidence.
- Unsupported/unknown runtime capabilities remain disabled and explicit.

## Acceptance and status updates

Each phase closes only after its PRD requirements, failure paths, security boundaries, migration behavior, and operational rollback have recorded evidence in `progress.md`. Component tests are not end-to-end evidence. The P0 product acceptance scenario is create/configure an Agent Definition and Agent Instance, assign/start a real Task, execute via LangChain in an isolated Run container, persist/stream real events, observe the correct character state in 3D, cancel/finish reliably, and inspect history after refresh/restart.

When a dependency is genuinely blocked, record the exact missing dependency and next action in `progress.md`; do not keep generic blockers or mark a phase complete from partial tests.

## Historical evidence

The former Phase 0–9 roadmap and work logs are retained in `progress.md` for traceability. Their labels and completion claims refer to the earlier PRD/scope; they do not imply completion of the six workstreams above. The old `PRD.md` has been superseded and removed at the user's request.