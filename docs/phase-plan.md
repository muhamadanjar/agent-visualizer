# Agent Office — Implementation Roadmap

**Status (2026-10-29):** Remapped to the user's selected [PRD v1.0](../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md). Phases 1–4 remain In Progress. Runner exact-root validation and Runtime Launcher transient-log recovery remain regression-tested; API policy and Launcher accept the deployed 24-turn limit. The `services` policy is revision 4 (600 seconds, 24 inference turns, 20 tool calls, `list_files`/`read_file` only), and Ultima's Definition is version 5. Attempts 24–25 completed with persisted `run.succeeded` events and Run Results using the free Cohere model. Run Detail shows successful results; `/office` shows the latest succeeded projection as stale, not a fresh work animation. Inventory package-manager evidence conflicts (Bun vs pnpm). See [`progress.md`](progress.md) for current evidence and remaining gates.

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
| 1 — Agent Registry & Agent Factory | P0 | In progress | Owner-scoped registry/Factory evidence remains. Dashboard Project policy revision 4 permits only `list_files`/`read_file`, with 600 seconds, 24 inference turns, and 20 tool calls; Ultima's Agent Definition is version 5. Attempts 24–25 completed the real read-only inventory Task with persisted safe actions and final Run Results. This does not complete all Registry/Factory acceptance criteria. |
| 2 — Agent Runtime | P0 | In progress | Workspace root validation and transient log-gateway retry are regression-tested; Runner `25 passed`, Launcher `22 passed`, Runtime Launcher healthy. Attempts 24–25 completed a provider-backed read-only Task and persisted results. Cancellation/recovery, egress-denial proof, and remaining Runtime acceptance criteria remain open. |
| 3 — Event System | P0 | In progress | PostgreSQL event/projection/outbox, Redis Streams fan-out, authenticated snapshot/replay WebSocket, and frontend cursor/reconnect remain implemented. Attempts 24–25 persisted `run.started`, heartbeat, `run.tool_action`, and `run.succeeded`, visible in Activity API and Run Detail with final results. Restart/reconnect recovery, Redis outage recovery, full taxonomy, retention/backlog, and latency gates remain open. |
| 4 — Virtual Office 3D | P0 | In progress | Persisted Agent Instances, CC0 GLB rigs, event-driven animation mapping, fallback, accessibility, and camera controls remain. `/office` shows Ultima's latest `succeeded` projection as stale; Run Detail verifies successful work, but fresh success animation and target-profile performance proof remain open. |
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