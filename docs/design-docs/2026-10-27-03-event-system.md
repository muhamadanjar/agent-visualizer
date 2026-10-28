# 03 — Event System Design Doc

| | |
|---|---|
| **Status** | In Review |
| **Author(s)** | Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-10-28 |
| **Service Terkait** | `apps/api`, `apps/runner`, Redis Streams, PostgreSQL, `apps/web` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Ringkasan (TL;DR)

PostgreSQL menjadi sumber kanonis Activity Event dan projection; Redis Streams hanya mendistribusikan event committed ke WebSocket gateway. 
Runner mengirim event idempotent dari outbox, API menyimpan sebelum acknowledge, 
dan reconnect membangun ulang snapshot/delta dari cursor persisted—bukan dari animasi atau browser state.

## Latar Belakang & Masalah

API saat ini sudah menyimpan Activity Event dengan `event_id`, `schema_version`, `source_sequence`, owner cursor, dan Run projection. 
WebSocket terautentikasi mengirim snapshot/delta dengan polling repository; 
Redis Streams belum ada di Compose. PRD baru mengusulkan event envelope bernomor versi, ordering, event persistence, reconnect, 
serta synchronization yang tetap benar bila API/Redis/browser restart.

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

Event categories: agent lifecycle, Task lifecycle, Run lifecycle, tool lifecycle, approval, workflow/delegation. Runtime-specific callback raw payload tidak keluar adapter. P0 map minimal lifecycle, heartbeat/freshness, cancellation acknowledgement, terminal outcome, dan tool action allowlisted. Ringkasan aksi dihasilkan backend dari tool ID terdaftar, bukan prompt/runtime prose.

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

### Implementasi saat ini (2026-10-28)

- API menulis `Activity Event`, projection, dan row outbox PostgreSQL dalam transaksi yang sama; publisher hanya mengakui row setelah `XADD` berhasil. Redis Streams dipakai sebagai fan-out, dengan satu stream dan pembacaan `XREAD` independen per replica API, bukan consumer group.
- WebSocket memakai antrean lokal terbatas per koneksi; snapshot dan replay dari PostgreSQL memulihkan cursor saat subscribe/reconnect dan saat sequence gap. Frontend mengirim cursor terakhir, mendeduplikasi event, dan membedakan keadaan sinkronisasi dari status koneksi.
- Batas stream saat ini sekitar 100.000 entry; retry outbox dan autentikasi/scope memiliki tes API/komponen serta migrasi diuji pada PostgreSQL terisolasi. Belum ada pembuktian Run produksi dari Runner sampai browser atau restart/recovery lintas service.
- Review menemukan bypass pemeriksaan kedaluwarsa sesi ketika item antrean WebSocket selalu siap; API kini mengautentikasi ulang sebelum meneruskan setiap event. Tes regresi mereproduksi masalah sebelum perbaikan dan lulus setelah perbaikan.
- Implementasi saat ini memetakan event Run lifecycle dan safe tool action yang sudah ada; taksonomi lengkap agent/task/approval/delegation pada proposal belum diklaim selesai. Retensi/backlog berdasarkan beban dan pengukuran latency tetap terbuka.

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

## Rencana Implementasi (opsional)

1. Finalisasi taxonomy event, wire envelope (`event_version`), scope/cursor, dan legacy mapping.
2. Tambahkan PostgreSQL fan-out outbox serta publisher/consumer Redis Streams; commit event sebelum publish/ack.
3. Migrasikan WebSocket API dan frontend pada breaking cutover; snapshot/delta berasal dari persisted cursor.
4. Uji duplicate, out-of-order, gangguan/pemulihan Redis, API restart, browser reconnect, auth expiry, dan stale event display.

## Pertanyaan Terbuka

- [perlu konfirmasi: kebutuhan stream partitioning/consumer group jika skala meningkat, retensi Redis, dan batas backlog]
- [perlu konfirmasi: target event rate, jumlah WebSocket clients, dan latency measurement profile]
- [perlu konfirmasi: masa migrasi `schema_version` ke wire `event_version` dan batas dukungan event legacy]
- [perlu konfirmasi: cakupan taksonomi Event P0 untuk Agent, Task, Approval, dan Delegation]
- [perlu konfirmasi: nama/email author dan reviewer]

## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §15, §23.6, §26.
- [GLOSSARY.md](../../GLOSSARY.md): Activity Event, Safe Tool-Action Summary, Visual Agent State.
- [Agent Runtime Design](2026-10-27-02-agent-runtime.md)
- [Virtual Office 3D Design](2026-10-27-04-virtual-office-3d.md)
