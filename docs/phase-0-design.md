# Agent Office — Current Design Baseline

**Status (2026-10-27):** Baseline remapped to the selected Draft PRD v1.0. Five subsystem design docs are Draft; architecture choices below are confirmed planning decisions or clearly labeled open questions, not claims of implementation.

**Product source:** [PRD — Dynamic AI Agent Virtual Office](../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md)  
**Roadmap:** [phase-plan.md](phase-plan.md)  
**Progress/evidence:** [progress.md](progress.md)  
**Design docs:** [design-docs/README.md](design-docs/README.md)

## Product boundary

Agent Office lets an owner create/manage agents, assign real Tasks, execute them through LangChain, and observe verified work in a 3D office. The PRD is broader than the current implementation; no operational completion is inferred from component tests or simulated/demo data.

The delivery order is Agent Registry → Agent Runtime → Event System → Virtual Office 3D → Multi-Agent Orchestration → extended runtimes/integrations. P0/P1/P2 distinctions from the PRD remain explicit. LangGraph is P1; external runtime integrations and automatic agent spawning remain later work.

## Confirmed architecture decisions

- Agent Definition is reusable configuration; Agent Definition Version is immutable; Agent Instance is a persistent office identity; Task and Run / Attempt remain distinct.
- Agent Factory constructs a transient runtime from an immutable definition/version, selected Model Endpoint, tools, and Project/owner policy. It stores no live runtime object.
- LangChain is the first runtime contract and replaces the current custom completion loop at a direct API breaking cutover. All known callers move together; database changes remain additive and preserve IDs/history.
- Each Run executes in its own isolated container. Egress is deny-by-default and allowed only to the selected Model Endpoint. Credentials are referenced through allowlisted server environment variables; secret values never enter browser/API/database snapshots.
- PostgreSQL is canonical for domain state and Activity Event; Redis Streams distributes committed events to authenticated WebSocket subscribers. Redis is not the historical source of truth.
- A child Run requires explicit owner approval. Allowlisted safe tools run without per-call approval; sensitive actions require action-specific approval or are denied.
- A Run is `cancelled` only after worker termination is acknowledged/verified. Uncertain outcomes remain pending/unknown and are not blindly retried.
- One avatar represents one Agent Instance. Visual Agent State is derived from verified Activity Event and Office Scene Version; it cannot set Task/Run state or permissions.
- P0 has one owner; Operator/Viewer roles are deferred to P1. This overrides the proposed role table in PRD §25.2 until the PRD is reconciled.
- Performance figures remain provisional until the target profile is selected. A clearly labeled demo mode is isolated from operational data and acceptance evidence.

## Component responsibilities

| Component | Responsibility | Boundary |
|---|---|---|
| Agent Registry / Agent Factory | Persist definitions/versions/instances; validate config; resolve endpoint, runtime and effective tools. | Prompt and role are not permissions; no runtime object persists in Registry. |
| Agent Office API / Execution Manager | Auth, owner/project policy, Task/Run commands, snapshots, approvals and event persistence. | Never trusts client or model output to authorize work. |
| Runner supervisor | Dispatch and supervise isolated Run containers, enforce limits, egress, cancellation and event outbox. | Agent container receives no DB/admin secret or host Docker socket. |
| Runtime Adapter / Provider Adapter | LangChain execution boundary and model-protocol boundary respectively. | Runtime lifecycle/tool authority and model inference remain separate. |
| PostgreSQL / Redis Streams | Canonical domain/event history and real-time fan-out. | Persist before acknowledgement; reconnect rehydrates from PostgreSQL. |
| Web application / 3D office | Agent List, task/run views, scene, camera, identity and event-derived visual state. | Scene, animation and user movement cannot write work state or permissions. |

## Trust and state invariants

- `Agent Definition`, `Agent Definition Version`, `Agent Instance`, `Runtime Session`, `Task`, `Run / Attempt`, `Delegation`, `Activity Event`, and `Visual Agent State` are distinct domain concepts.
- Prompt Markdown, Skill content, workspace files, tool responses and agent messages are untrusted.
- Effective child permissions are bounded by owner/project policy, parent scope, child Definition Version, and the approved tool set.
- A committed event and projection precede Runner acknowledgement. Redis/browser disconnection cannot erase execution evidence or stop the Run.
- Freshness is separate from Run status. Missing/stale evidence is shown as unknown/unavailable; work animation freezes.
- Office rooms, workstations, camera focus, and avatar proximity are presentation/navigation only.
- Demo-mode data, if enabled, is explicitly labeled and cannot be mistaken for real Agent Instance, Task, Run, or Activity Event evidence.

## Open design/verification items

The subsystem documents carry remaining decisions in their **Pertanyaan Terbuka** sections. Current shared items include the exact GLB asset/license and animation clips, target benchmark hardware/network, LangChain version/capabilities, secure container-launcher and endpoint-only egress enforcement, Redis topology/retention, secret-reference allowlist/rotation, and LangGraph graph term/limits. These are not claims that the architecture has been implemented or verified.

## Historical baseline

The earlier Phase 0 design described a narrower custom Agent Office Execution Engine and one configured endpoint. It is superseded as the active architecture baseline; implementation evidence and historical decisions remain in `progress.md`. The former `PRD.md` has been removed at the user's request.