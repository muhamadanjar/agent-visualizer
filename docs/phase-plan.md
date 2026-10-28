# Agent Office — Implementation Roadmap

**Status (2026-10-29):** Remapped to the user's selected [PRD v1.0](../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md). Phase 1 Agent Registry, Phase 2 Agent Runtime, and Phase 3 Event System remain incomplete; Phase 4 Virtual Office 3D remains in progress with no live event projections. Evidence is in [`progress.md`](progress.md). The five design docs remain In Review/Draft as individually recorded; no workstream completion is inferred from implementation alone.

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
- Owner-only authorization is P0; Operator/Viewer roles move to P1. This is a confirmed override to PRD §25.2 and must be reconciled before PRD approval.
- One character represents each `Agent Instance`, replacing the proposed default of one avatar per `Agent Definition` in PRD §34.
- Definitions are archived rather than hard-deleted; no new Run may use an archived definition, while an active Run may finish.
- LangChain replaces the current custom completion loop through a direct cutover. Each Run receives an isolated container; egress is limited to its selected Model Endpoint.
- PostgreSQL is the canonical event store; Redis Streams is event fan-out. A Redis outage delays live delivery but must not lose committed events.
- Every delegated child Run requires owner approval. Allowlisted safe tools run without per-call approval; sensitive operations require a specific approval or are rejected.
- Model Endpoint is selected per `Agent Definition Version`; credential values remain in allowlisted server environment references.
- Cancellation is terminal only after worker stop is acknowledged/verified. An uncertain outcome remains pending/unknown and is not retried blindly.
- Migrations preserve IDs/history and do not delete records; schema constraints may change when required by the selected PRD, as migration 0011 does for duplicate Definition names. API cutover is breaking; all known callers move together.
- PRD performance figures are provisional until the test profile is agreed. Demo mode, if enabled, is isolated and clearly labeled; it never proves real execution.
- Calendar estimates are not assigned without team capacity.
- The user authorized deferring Phase 2's remaining end-to-end verification and proceeding with Phase 3; this changes sequencing only. Phase 2 remains In progress, with its original exit criteria open and not waived.
- The user authorized continuing roadmap Phase 4 event visualization while Phase 3 remains In progress. This is a sequencing override only; Phase 3 acceptance criteria and the Phase 2 Runner gate remain open and are not waived.

## Delivery sequence and current evidence

| Phase | PRD priority | Status | Current evidence and next acceptance gate |
|---|---|---|---|
| 1 — Agent Registry & Agent Factory | P0 | In progress | Owner-scoped endpoints, immutable Definition Versions, archive/enable, Factory preflight, non-secret Run snapshots, per-version concurrency, caller migration, and queued legacy-Run handling have implementation/test evidence. Owner-only P0 now aligns with PRD §25.2. Factory unit tests reject disabled/archived Definitions and archived Endpoints. The added PostgreSQL archive/history regression awaits a fresh disposable test database; the current `*_test` database is already migrated, so the Compose integration run failed in legacy migration setup before reaching it. No API-created Run has been observed through Runner/provider. Next: run PostgreSQL integration after approval to recreate only the exact `*_test` database, then verify a real API-created Run without creating sample Task data. |
| 2 — Agent Runtime | P0 | In progress | Per-Run LangChain worker containers, authenticated Docker Launcher, endpoint proxy, cancellation paths, lease reconciliation, and Run Results are implemented. Runner entrypoint recovery and credential separation are verified. Runner tests `24 passed`; Launcher tests `19 passed`; API tests `66 passed` excluding the private migration test; fresh private PostgreSQL/API archive integration `1 passed`; Compose config and API readiness passed. NineRouter `/v1/models` returns `200`; saved `9router free` Endpoint uses the correct base `http://localhost:20128/v1` and an advertised `:free` model. The prior `/v1/models` base is malformed and remains active pending owner re-login; archive refresh fix and route regression are implemented. Existing Task is unassigned; its project lacks workspace path/policy and tool allowlist. No provider completion or API-created Run is verified. Next: assign/configure a real user Task and Agent Instance/project policy, then verify provider-backed success/failure, cancellation/restart, Docker egress-denial, and persisted Event/3D path. Pass FR-RUNTIME-001–008 with end-to-end evidence. |
| 3 — Event System | P0 | In progress | PostgreSQL Activity Event/projection/outbox, Redis Streams fan-out, authenticated snapshot/replay WebSocket, and frontend cursor/reconnect handling are implemented. API suite `66 passed, 1 skipped`; disposable PostgreSQL migration/owner-scope integration `1 passed`; Compose API/web/Redis services built and reached healthy/readiness; web typecheck and Compose config passed. Independent review found an expired-session bypass under continuous Redis queue delivery; a regression reproduced it, the WebSocket now reauthenticates before every forwarded event, and the regression passes. No live Runner → API → Redis → authenticated browser event path, cross-service restart/reconnect, Redis outage recovery, or full Agent/Task/Approval/Delegation taxonomy is verified. Resolve retention/backlog and event-rate/latency profile; pass FR-EVENT-001–008 with deduplication and terminal-state protection. |
| 4 — Virtual Office 3D | P0 | In progress | Persisted Agent Instances render with one self-hosted CC0 GLB rig per detailed Instance, fresh-event-only animations, neutral stale/missing fallback, accessible list/detail selection, responsive simplification, and camera controls; former local Player Avatar/movement was removed. The web runtime image copies `public/`; GLB requests return 200. The uncompressed GLB loads with Draco/Meshopt decoders disabled, avoiding a CSP relaxation. Authenticated Compose `/office` renders 7 persisted Agent Instances in one canvas with no browser errors; the inspector now exposes the latest verified event and Safe Tool-Action Summary from the Activity projection. Current snapshot has 0 event projections, so no live event animation was observed. Live Run → API → browser proof and target-profile performance (including 30 FPS) remain open, as do Phase 3 event taxonomy and Phase 2 Runner proof. |
| 5 — Multi-Agent Orchestration | P1 | Not started | No LangGraph supervisor is claimed. Next: persisted DAG and child Task/Run links, dynamic selection, per-child approval, bounded recovery, and artifacts/structured outputs. Pass FR-ORCH-001–007 and Task dependency acceptance. |
| 6 — Extended runtime and product integrations | P1/P2 | Not started | Add only after P0/P1 boundaries and adapters are verified: extra Runtime Adapters, Skills, GitHub/GitLab, RAG, cost/usage, automatic spawning, VPS, and office customization according to PRD priority. |

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