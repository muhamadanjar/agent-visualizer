# Agent Office Design Docs

Dokumen-dokumen ini merinci keputusan arsitektur berdasarkan [PRD baru](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md). Status tiap dokumen dimulai sebagai `Draft`; seluruh prioritas P0/P1/P2 mengikuti PRD.

| Design Doc | Fokus | Status | Tanggal |
|---|---|---|---|
| [01 — Agent Registry & Agent Factory](2026-10-27-01-agent-registry.md) | Agent definitions, versions, CRUD/archive, permissions, Model Endpoint, Agent Factory | In Review | 2026-10-27 |
| [02 — Agent Runtime](2026-10-27-02-agent-runtime.md) | LangChain execution, workers, runtime adapters, cancellation, lifecycle | In Review | 2026-10-27 |
| [03 — Event System](2026-10-27-03-event-system.md) | Event schema/persistence, Redis Streams, WebSocket, reconnect, state synchronization | In Review | 2026-10-28 |
| [04 — Virtual Office 3D](2026-10-27-04-virtual-office-3d.md) | Three.js, karakter, animasi, workstation, kamera, performance | In Review | 2026-10-29 |
| [05 — Multi-Agent Orchestration](2026-10-27-05-multi-agent-orchestration.md) | LangGraph supervisor, task dependencies, dynamic routing, agent collaboration | Draft | 2026-10-27 |

## Status flow

`Draft → In Review → Approved → Implemented`. Status hanya berpindah ke `In Review` setelah human review. `Rejected` dan `Deprecated` adalah status terminal.

Setiap design doc mencatat asumsi/keputusan yang belum terverifikasi di bagian **Pertanyaan Terbuka**. Status `Approved` bukan bukti implementasi; `Implemented` memerlukan deliverable dan acceptance evidence pada progress register.
