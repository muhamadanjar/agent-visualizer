# 03 — Event System Design Doc

| | |
|---|---|
| **Status** | Implemented — roadmap Phase 3 (P0) scope only |
| **Author(s)** | Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-11-02 |
| **Service Terkait** | `apps/api`, `apps/runner`, Redis Streams, PostgreSQL, `apps/web` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Status implementasi saat ini (2026-11-02)

Roadmap Phase 3 (P0) selesai: PostgreSQL event/projection/outbox, Redis Streams fan-out, authenticated snapshot/replay WebSocket, dan frontend cursor/reconnect telah diimplementasikan. Bukti mencakup Redis outage recovery, post-restart browser session/replay, dan dua isolated TCP fan-out measurements di bawah profil lokal. Status ini hanya Phase 3 P0, bukan seluruh taksonomi event PRD atau usulan P1/P2. Approval decision tersimpan, tetapi belum ada caller Run yang membuat `approval.requested`. Event `run.waiting_children` dan `approval.expired` adalah rencana P1 Phase 5 di bawah; belum diimplementasikan. Partitioning/consumer-group pada skala lebih besar dan batas backlog/alert tetap terbuka.

## Ringkasan (TL;DR)

PostgreSQL menjadi sumber kanonis Activity Event dan projection; Redis Streams hanya mendistribusikan event committed ke WebSocket gateway. 
Runner mengirim event idempotent dari outbox, API menyimpan sebelum acknowledge, 
dan reconnect membangun ulang snapshot/delta dari cursor persisted—bukan dari animasi atau browser state.

## Latar Belakang & Masalah

Sebelum cutover, API sudah menyimpan Activity Event dengan `event_id`, `schema_version`, `source_sequence`, owner cursor, dan Run projection. Pada kondisi tersebut WebSocket terautentikasi mengirim snapshot/delta melalui polling repository, sedangkan Redis Streams belum tersedia di Compose. Implementasi roadmap Phase 3 yang kini berjalan menambahkan event envelope bernomor versi, persistence, reconnect, dan synchronization lintas restart API/Redis/browser sebagaimana dirangkum di bagian status saat ini.

## Tujuan

- Satu Activity Event memiliki sumber, scope, ID, schema version, waktu, urutan/cursor, dan relasi Task/Run/Agent Instance yang dapat diverifikasi.
- Deduplicasi dan projection monotonic; event terlambat tidak menurunkan status terminal.
- Event disimpan sebelum dianggap berhasil dan sebelum Runner menerima acknowledgement.
- Redis Streams memberi fan-out ke WebSocket replicas; PostgreSQL tetap menjadi sumber snapshot dan replay.
- Browser menyinkronkan snapshot lalu delta berdasarkan cursor; freshness berbeda dari status socket.
- Payload hanya menampilkan Safe Tool-Action Summary; tidak menyimpan hidden reasoning, raw tool output, secret, atau isi file.

## Non-Tujuan (Out of Scope)

- Event sourcing untuk seluruh domain write model; event adalah evidence stream dan projection untuk monitoring.
- Menjadikan Redis satu-satunya penyimpanan historis/otoritatif.
- Mengirim event per frame animasi atau menurunkan Task/Run state dari Visual Agent State.
- Menampilkan output bebas yang dibuat model sebagai bukti kerja.

## Proposal / Desain

### Envelope dan persistence

Wire envelope mengikuti struktur PRD baru: `event_id`, `event_version`, `sequence`, `timestamp`, `type`, owner/project/task/run/agent correlation, dan payload terkontrol. Implementasi saat ini menamai versi persisted `schema_version` dan cursor sebagai `cursor`; adapter baru memetakan format lama ke wire contract baru. Legacy rows tetap dipertahankan; data migration additive/backfill saja, tanpa mengubah makna event historis.

P0 event taxonomy covers Run lifecycle, heartbeat/freshness, cancellation, terminal outcome, safe tool-action summaries, and approval transitions (`approval.requested`, `approval.approved`, `approval.rejected`). The FR-3D-005 visual-activity extension adds `run.working`, `run.tool_started`, and `run.reviewing`; these are normalized evidence, not Run lifecycle statuses. Agent, Task, workflow, and delegation event categories are deferred. Raw runtime callback payloads never leave the adapter. Safe action summaries are backend-derived from registered tool IDs, not model prose.

### Planned Phase 5 event extensions (P1; not implemented)

Phase 5 plans additive `run.waiting_children` evidence for a nonterminal parent Run waiting on a child Run or dependency, and `approval.expired` for approval TTL expiry, distinct from Owner rejection. Delivering these extensions will require Event schema, projection, WebSocket, and frontend changes. They are Phase 5 plans only: they do not reopen or change the completed Phase 3 P0 scope or its status.

```mermaid
sequenceDiagram
    participant R as Run container / Runner outbox
    participant API as API validator
    participant PG as PostgreSQL event ledger + projection + outbox
    participant Redis as Redis Streams
    participant WS as WebSocket gateway
    participant UI as Frontend state / 3D
    R->>API: Event + source identity + sequence
    API->>API: Validate scope, redact, deduplicate
    API->>PG: Append event, update projection and fan-out outbox
    PG-->>API: Commit
    API-->>R: Durable acknowledgement
    PG->>Redis: Publish committed event from outbox
    Redis->>WS: Deliver to subscribed gateway replicas
    WS->>UI: Delta with cursor
    UI->>PG: Snapshot / replay after cursor on reconnect
```

### Keandalan dan sinkronisasi

- Transaksi API menulis event ledger, projection terbaru, dan fan-out outbox Redis secara atomik. Publisher outbox melakukan retry; delivery Redis duplikat dianggap normal.
- Delivery Redis Streams adalah transport, bukan state kanonis. Jika Redis tidak tersedia, event committed tetap tersimpan di PostgreSQL/outbox; publication berlanjut setelah Redis pulih. Reconnect membaca snapshot PostgreSQL dan event setelah cursor.
- Setiap WebSocket subscription terautentikasi dan dibatasi pada owner. Otorisasi diperiksa saat subscribe dan selama sesi berlangsung. Kontrol origin/browser session tetap wajib.
- Snapshot mengembalikan cursor yang konsisten dengan projection. Client hanya menerapkan event setelah cursor tersebut, melakukan deduplikasi berdasarkan event ID/cursor, dan menampilkan last-seen/freshness. Gap replay atau source stale ditampilkan eksplisit; `Visual Agent State` stale membekukan work animation.
- Source sequence mengurutkan satu stream Runner/Run. Owner cursor mengurutkan event persisted untuk replay; timestamp saja bukan jaminan urutan yang benar.
- `event_version` adalah field wire baru dari PRD; `schema_version` yang tersimpan tetap dipertahankan atau diterjemahkan melalui migrasi additive. Adapter baru mengikuti taksonomi event ternormalisasi; tipe historis tetap dapat dibaca dan tidak ditulis ulang.

The visual-activity events remain `running` in the Run projection. `run.working` is emitted at an actual model-call start; `run.tool_started` precedes a workspace side effect; `run.reviewing` marks the next model call after tool output and does not claim human/code review. The post-call `run.tool_action` remains a backend-derived safe summary. Planning/tool-start/reviewing cues expire 30 seconds after `last_event_at` even if a later heartbeat keeps overall projection freshness current. No per-Agent Runtime Session/presence source currently proves `Offline`; missing or stale evidence remains `unknown`.

### Implementasi saat ini (2026-11-01)

- API menulis `Activity Event`, projection, dan row outbox PostgreSQL dalam transaksi yang sama; publisher hanya mengakui row setelah `XADD` berhasil. Redis Streams dipakai sebagai fan-out, dengan satu stream dan pembacaan `XREAD` independen per replica API, bukan consumer group.
- WebSocket memakai antrean lokal terbatas per koneksi; snapshot dan replay dari PostgreSQL memulihkan cursor saat subscribe/reconnect dan saat sequence gap. Frontend mengirim cursor terakhir, mendeduplikasi event, dan membedakan keadaan sinkronisasi dari status koneksi.
- Redis Streams is capped at approximately 100,000 entries. The user selected preservation of PostgreSQL Activity Event records during the pilot; no pilot event-deletion policy is introduced. The local benchmark target is 10 WebSocket clients, 100 events/second, and p95 delivery latency ≤1 second; this is a temporary profile, not a production capacity claim.
- Attempt 41 persisted Run events/result across API restart. In a separate live Redis outage drill, Redis was stopped for 6 seconds while read-only Run `3d4dc8e1-7552-4267-9f00-e37327c18368` was active. PostgreSQL retained 15 Run events (cursors 424–438) while Redis was down; six outbox rows were unpublished at the outage check. After Redis restart, the Run completed with 18 persisted events through cursor 441, stream length reached 441, pending outbox returned to zero, and API readiness was `ready`.
- WebSocket API revalidates the Owner session while forwarding queued events. After API restart, a live browser received `401` from `/api/v1/auth/me` and its WebSocket handshake closed with code `1006`; automatic retries previously continued every two seconds. The frontend now checks session validity without cache on abnormal handshake closure, emits `unavailable` and stops retrying on `401/403`, while transient failures still reconnect. Regression test reproduced the retry before the fix and passes afterward.
- Approval request, approval, and rejection transitions are persisted to the Activity Event ledger in the same PostgreSQL transaction as the approval write/decision. Event scope comes from the persisted Run snapshot; action details and digests are not copied into the event payload. PostgreSQL integration verifies event idempotency and outbox claim serialization.
- Earlier browser replay delivered 403 historical events to 10 authenticated WebSocket clients in 3 batches, with per-client p95 1.594 seconds. This historical replay is separate from the agreed new-event profile and does not establish its pass/fail result.
- Two isolated TCP fan-out measurements each connected 10 authenticated WebSocket clients and published 1,000 new synthetic envelopes at about 100 events/second. All clients received every event; aggregate p95 delivery was 599.32 ms and 132.99 ms across the two runs. Both used Redis database 15 and excluded PostgreSQL outbox and real Run execution; synthetic events remained outside production Redis database 0 and the UI.
- After Redis recovery, authenticated `/api/v1/auth/me` and Run Activity returned HTTP 200; the Run projection was `succeeded` at cursor 441. A new authenticated WebSocket replay from cursor 423 returned all 18 Run events through `run.succeeded` and snapshot cursor 441.
- After API process start at `2026-10-29T15:41:03Z`, the live browser returned HTTP 200 for `/api/v1/auth/me` and `/api/v1/activity/snapshot`; API logs show `/api/v1/ws` accepted, and `/office` rendered `Browser event feed: connected`. Because `OwnerSessionStore` is process-local and `sessions.issue` is called by login, this valid post-start session was issued after restart.
- The authenticated Owner decision route persists approvals/rejections and their Activity Events. `ApprovalRepository.create_request` has no current Run caller, so a real Run creating `approval.requested` is not claimed by this event-system closure.

### Event-to-presentation boundary

`Activity Event` is canonical operational evidence. `Visual Agent State` is a frontend projection derived from verified Task/Run events and scene layout. The 3D renderer cannot write an event or terminal state. A provider completion without a verified runtime lifecycle event cannot create a running/succeeded avatar state.

## Alternatif yang Dipertimbangkan

| Alternatif | Kelebihan | Kekurangan | Kenapa tidak dipilih |
|---|---|---|---|
| **PostgreSQL ledger + transactional outbox + Redis Streams fan-out (dipilih)** | Sumber kanonis durable, replay dapat dipulihkan, mendukung banyak replica, reconnect tidak bergantung pada retensi Redis. | Menambah service Redis/outbox yang harus dioperasikan dan dipantau. | Sesuai PRD; PostgreSQL tetap canonical dan gangguan Redis tidak menghilangkan event yang sudah committed. |
| Polling/NOTIFY PostgreSQL saja | Service lebih sedikit dan mudah dijalankan pada satu replica. | Latency/load polling; fan-out/reconnect terikat API; NOTIFY bukan event ledger. | Tidak memenuhi pilihan infrastruktur event PRD dan keputusan Redis Streams. |
| Redis Streams sebagai event store kanonis | Fan-out berlatensi rendah. | Retensi, trimming, persistence, backup, dan recovery projection terikat pada Redis. | Ditolak; histori dan audit harus durable di PostgreSQL. |

## Dampak & Risiko

- **Backward compatibility**: kontrak wire WebSocket/event dapat breaking; seluruh consumer web/Runner berpindah saat cutover. Event tersimpan lama tetap tersedia melalui reader mapping.
- **Migrasi data**: skema additive; pertahankan event ID/cursor/histori. Jangan hapus kolom versi lama sampai reader baru dan backfill tervalidasi.
- **Performa**: projection/owner cursor dan pagination event menghindari pembacaan seluruh histori; Redis fan-out mengurangi polling, tetapi backlog outbox/slow client perlu batas dan alert.
- **Keamanan**: validasi sumber, owner/project scope, redaction, WebSocket/origin terautentikasi, tanpa hidden reasoning/raw content; Redis memakai private network/ACL/TLS sesuai deployment.
- **Operasional**: perlu retry outbox, Redis stream retention, pemantauan consumer lag, replay gap metrics, drill restart/reconnect, dan backup PostgreSQL. Gangguan Redis menunda live fan-out, bukan menghapus event atau mengubah Run state.

## Rencana implementasi (historis; roadmap Phase 3 selesai)

PostgreSQL persistence/outbox, Redis fan-out, WebSocket replay, dan frontend cursor/reconnect telah dilaksanakan untuk scope roadmap Phase 3 (P0). Bukti serta batas yang tersisa dirangkum pada bagian **Status implementasi saat ini** dan `docs/progress.md`.

## Pertanyaan Terbuka

- [perlu konfirmasi: stream partitioning/consumer-group need at higher scale and backlog alert/limit] -- deferred; current pilot uses one stream and a ~100,000-entry cap
- [perlu konfirmasi: migration period for persisted `schema_version` to wire `event_version` and legacy-event support boundary]
- [perlu konfirmasi: author/reviewer identity] -- reviewer remains unassigned

## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §15, §23.6, §26.
- [GLOSSARY.md](../../GLOSSARY.md): Activity Event, Safe Tool-Action Summary, Visual Agent State.
- [Agent Runtime Design](2026-10-27-02-agent-runtime.md)
- [Virtual Office 3D Design](2026-10-27-04-virtual-office-3d.md)
