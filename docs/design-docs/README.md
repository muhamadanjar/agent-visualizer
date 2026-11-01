# Agent Office Design Docs

Dokumen-dokumen ini merinci keputusan arsitektur berdasarkan [PRD baru](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md). Status mengikuti siklus review desain dan bukti implementasi; prioritas P0/P1/P2 mengikuti PRD. `Implemented` pada dokumen 01–04 hanya menandai scope Phase 1–4 yang selesai, bukan usulan P1/P2 atau seluruh pertanyaan terbuka.

| Design Doc | Fokus | Status | Tanggal |
|---|---|---|---|
| [01 — Agent Registry & Agent Factory](2026-10-27-01-agent-registry.md) | Agent definitions, versions, CRUD/archive, permissions, Model Endpoint, Agent Factory | Implemented (roadmap Phase 1 / P0 scope) | 2026-11-01 |
| [02 — Agent Runtime](2026-10-27-02-agent-runtime.md) | LangChain execution, workers, runtime adapters, cancellation, lifecycle | Implemented (roadmap Phase 2 / P0 scope) | 2026-11-01 |
| [03 — Event System](2026-10-27-03-event-system.md) | Event schema/persistence, Redis Streams, WebSocket, reconnect, state synchronization | Implemented (roadmap Phase 3 / P0 scope) | 2026-11-01 |
| [04 — Virtual Office 3D](2026-10-27-04-virtual-office-3d.md) | Three.js, karakter, animasi, workstation, Reception bersama, kamera, performance | Implemented (roadmap Phase 4 / P0 scope) | 2026-11-01 |
| [05 — Multi-Agent Orchestration](2026-10-27-05-multi-agent-orchestration.md) | LangGraph supervisor, task dependencies, dynamic routing, agent collaboration | Draft (roadmap Phase 5 / Not started) | 2026-11-01 |
| [06 — Codex Subscription via Model Gateway](2026-10-30-06-codex-subscription.md) | OMP broker/private single-attempt gateway + LangChain Chat Completions integration; broader acceptance open | In Review (roadmap Phase 6 / In Progress) | 2026-11-01 |

## Status flow

`Draft → In Review → Approved` describes design review; `Implemented` records verified delivery and requires evidence in the progress register. A doc may be marked `Implemented` for a bounded roadmap phase/P0 scope while its design-review approval or reviewer assignment remains pending. `Rejected` and `Deprecated` are terminal design statuses.

`Implemented` on docs 01–04 refers only to their completed roadmap Phase 1–4 scope. It does not mean proposed P1/P2 work or every open question is implemented. Status of the roadmap phase and design review/approval are distinct.
