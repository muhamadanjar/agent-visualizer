# 02 — Agent Runtime Design Doc

| | |
|---|---|
| **Status** | In-Review |
| **Author(s)** | Muhamad Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-10-28 |
| **Service Terkait** | `apps/api`, `apps/dispatcher`, `apps/runner`, Docker Compose, `apps/web` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Ringkasan (TL;DR)

P0 menargetkan LangChain dalam container terisolasi per Run; Agent Office tetap mengendalikan Task/Run, permission, cancellation, dan Activity Event. Implementasi kini memiliki Runner supervisor, Runtime Launcher terpisah, worker container per Run, proxy endpoint-terpilih, worker startup acknowledgement sebelum `run.started`, dan validasi frame pada Runner serta Launcher. URL Model Endpoint berasal dari snapshot Run sehingga global `NINEROUTER_URL` tidak wajib. Full API → Runner → Launcher → provider → UI/3D execution dan live restart recovery belum terverifikasi; design doc dan roadmap Phase 2 tetap terbuka.

## Latar Belakang & Masalah

Runner membangun `create_agent` dari snapshot immutable. Sebelumnya agent berjalan di proses Runner yang long-lived, sehingga filesystem, credential, dan egress boundary per Run belum ditegakkan. PRD memerlukan runtime LangChain, adapter contract, worker yang tetap berjalan tanpa browser, execution limits, verified cancellation, persisted result/error, dan normalized events. Model Provider hanya memasok inference; respons provider bukan bukti Run dimulai/selesai.

## Tujuan

- Menjalankan Run tanpa ketergantungan pada browser connection.
- Menggunakan LangChain sebagai Runtime Adapter pertama dan mempertahankan batas terpisah untuk Provider Adapter.
- Mengisolasi filesystem, resource, credentials, dan egress per Run.
- Mengakui `Running` hanya setelah worker/runtime menerima dan memulai Run.
- Menghentikan Run secara terverifikasi; permintaan stop tidak langsung menjadi terminal `cancelled`.
- Mencatat capabilities adapter hanya bila telah dibuktikan; unsupported/unknown tidak diemulasi.

## Non-Tujuan (Out of Scope)

- Multi-provider native API dan runtime selain LangChain pada P0.
- Pause/resume, automatic agent spawning, atau retry atas Run dengan side effect yang statusnya tidak diketahui.
- Menjalankan shell, mengakses host Docker socket dari proses agent, atau egress internet umum.
- Menganggap LangChain tool-call callback sebagai izin; server-side policy tetap otoritatif.

## Proposal / Desain

### Batas komponen

- **Execution Manager (API saat ini)**: menyimpan/menyalurkan start dan cancel commands melalui claim API; menerapkan lease dan batas concurrency. Tidak menambah Dispatcher terpisah pada Phase 2.
- **Runner supervisor**: mengklaim command, meminta supervisor Docker terpisah mengelola container per Run, memonitor lease/exit, mengirim hasil dan event melalui API, serta merekonsiliasi container orphan.
- **Agent Office Execution Engine**: menjalankan LangChain sebagai runtime P0 dari snapshot immutable; capability hanya diumumkan setelah dibuktikan. `Runtime Adapter` tetap untuk integrasi runtime eksternal opsional sesuai `GLOSSARY.md`.
- **Provider Adapter**: menerjemahkan request/respons inference ke Model Endpoint. Ia tidak membuat Task/Run, memberi izin tools, atau menentukan visual state.
- **Tool executor**: hanya menyediakan tools hasil resolusi policy. Tool aman allowlisted berjalan; operasi sensitif memerlukan Approval spesifik atau ditolak. Final response disimpan terpisah sebagai `Run Result`, bukan `Activity Event`.

```mermaid
sequenceDiagram
    participant API as API / Execution Manager
    participant Runner as Runner Supervisor
    participant Box as Container per Run
    participant LC as LangChain Adapter
    participant EP as Model Endpoint
    API->>Runner: Start command + immutable snapshot
    Runner->>Runner: Validate owner, lease, limits, endpoint
    Runner->>Box: Create isolated container/workspace
    Box->>LC: Start Run with resolved tools
    LC->>EP: Inference request
    EP-->>LC: Model response / tool call
    LC->>Box: Invoke allowlisted tool
    Box-->>Runner: Safe Activity Event + result
    Runner-->>API: Persisted event outbox delivery
    API-->>Runner: Durable acknowledgement
```

Worker container per Run tidak mendapat akses DB, API/Runner credentials, Docker socket, atau host filesystem di luar workspace terpasang. Egress diarahkan melalui proxy pada network per Run yang mengizinkan Model Endpoint terpilih dan memvalidasi DNS/IP; endpoint 9Router lokal hanya diizinkan jika dipilih eksplisit. Batas worker saat ini: 1 CPU, RAM 2 GiB, 128 PIDs, maksimal 2 active Run per host, timeout maksimal 3.600 detik. Batas ini bukan benchmark production.

Launcher adalah supervisor Docker terpisah pada host; Docker host saat ini rootful (`runc`). Socket hanya tersedia bagi service supervisor tepercaya, tidak bagi Runner atau worker. Kompromi supervisor tetap berpotensi memberi kontrol host; akses dibatasi ke API bertipe dan opsi container allowlisted. Worker hanya menerima `NINEROUTER_KEY` yang diperlukan untuk Model Endpoint; key tidak masuk snapshot, event, atau hasil.

### Lifecycle dan cancellation

```mermaid
stateDiagram-v2
    [*] --> created
    created --> starting: dispatch
    starting --> running: runtime start acknowledged
    starting --> failed: start error verified
    running --> cancellation_pending: cancel requested
    cancellation_pending --> cancelled: worker confirms process stopped
    cancellation_pending --> unknown: no reliable stop evidence
    running --> succeeded: terminal success event persisted
    running --> failed: terminal failure event persisted
    running --> unknown: lease/worker outcome uncertain
```

Run hanya menjadi `canceled` setelah stop proses/container terverifikasi. Bila supervisor tidak menjawab, penghentian dibatasi 10 detik lalu dicoba paksa dan diverifikasi. Jika hasil tetap tidak diketahui, status menjadi `unknown`; Run tidak otomatis diulang. Setelah lease kedaluwarsa, supervisor menghentikan container orphan, memverifikasi stop, lalu mencatat `unknown`. Status/event memakai ejaan kanonis `canceled` dari implementasi saat ini. Existing Run aktif pada saat deployment perlu dihentikan atau ditunggu selesai sesuai operator cutover; desain tidak mengubah histori.

### Cutover dan API

LangChain menggantikan loop custom secara langsung. API contract boleh breaking; web dan Runner diperbarui serentak dan tidak ada compatibility shim. Migrasi database tetap additive dan mempertahankan Run lama sebagai histori; old Run tidak dieksekusi ulang dengan konfigurasi runtime baru. Rollback berarti mengembalikan deployment sebelum start Run baru, bukan menjalankan ulang attempt yang statusnya tidak pasti.

Dependency implementasi dipatok pada `langchain==1.4.7` dan `langchain-openai==1.7.3`. Runner (24 tests) dan Launcher (19 tests) membuktikan konstruksi agent, pemilihan model/route tanpa global `NINEROUTER_URL`, startup acknowledgement, batas worker-frame/lifecycle, event quarantine, orphan resource cleanup, cancellation verification branches, dan bounded result persistence pada level komponen. Disposable PostgreSQL 17 migration mencapai `runtime_0013`; Compose config dan kedua image berhasil dibangun. Tidak ada API-created Run yang melewati container/provider nyata; Docker egress denial dan Provider Router execution belum dibuktikan.

P0 capability implementation includes create/start, bounded tool calls, normalized lifecycle/safe-action events, result/error, timeout, cancellation acknowledgement, and no-browser dependency. Streaming and pause/resume are not claimed; remaining capabilities stay open until acceptance evidence is recorded.

## Alternatif yang Dipertimbangkan

| Alternatif | Kelebihan | Kekurangan | Kenapa tidak dipilih |
|---|---|---|---|
| **LangChain Adapter + container per Run + cutover langsung (dipilih)** | Mengikuti PRD baru; isolasi dan lifecycle satu Run jelas; tidak memelihara dua loop. | Perubahan dependency/worker besar; cutover langsung meningkatkan risiko deployment dan perlu validasi cancellation/egress. | Sesuai pilihan pengguna; mitigasi melalui snapshot, migrasi database additive, dan uji rollback. |
| Pertahankan loop custom sebagai runtime utama | Migrasi terkecil dan kompatibel dengan kode sekarang. | Tidak memenuhi LangChain sebagai runtime P0 dan membuat target PRD terus tertunda. | Ditolak oleh keputusan pengguna untuk mengganti langsung. |
| Jalankan LangChain di worker bersama tanpa container per Run | Lebih hemat resource dan sederhana saat ini. | Batas filesystem/network antar-Run lebih lemah; satu crash dapat mempengaruhi beberapa Run. | Tidak memenuhi keputusan isolasi per Run. |

## Dampak & Risiko

- **Backward compatibility**: API breaking cutover; semua caller yang dikenal harus deploy bersama. Persisted Run lama tetap dapat dibaca melalui projection/reader baru.
- **Migrasi data**: skema additive; old Run, snapshot, dan Activity Event dipertahankan. Jangan menerbitkan Run baru sampai snapshot dapat memasok konfigurasi LangChain yang tervalidasi.
- **Performa**: container per Run menambah startup latency dan resource overhead; concurrency/CPU/memory/timeouts harus dibatasi dan diuji pada target host.
- **Keamanan**: risiko tertinggi adalah container escape, secret exposure, SSRF/private-address egress, dan tool permission bypass. Deny-by-default, no privileged mode, per-Run filesystem, environment-secret allowlist, serta review keamanan menjadi gate.
- **Operasional**: butuh lifecycle cleanup, orphan-container reconciliation, health/heartbeat, logs ber-redaksi, dan backup. Rollback deployment tidak boleh menandai Run aktif selesai atau mengulang side effect.

## Rencana Implementasi (opsional)

1. Contracts and typed Runtime Launcher operations are implemented; verify remaining capability gates against acceptance.
2. Runner supervisor and authenticated Launcher create bounded isolated worker containers without placing the Docker socket in the agent process.
3. Direct cutover to per-Run LangChain workers, with the API and web callers migrated.
4. Remaining verification: API-created real Run, selected-endpoint success/denial, success/failure, allowlisted tool, timeout, verified/unknown cancellation, browser disconnect, process restart/reconciliation, and downstream persisted event/3D observation.

## Pertanyaan Terbuka

- [implementasi belum membuktikan: API-created real provider Run, endpoint-denial enforcement inside Docker, live cancellation/restart/reconciliation, concurrent independent Runs, dan persisted event/result acceptance di UI/3D]
- [perlu konfirmasi: nama/email author dan reviewer]

## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §5, §8–9, §25, §27.
- [GLOSSARY.md](../../GLOSSARY.md): Agent Office Execution Engine, Provider Adapter, Runtime Adapter, Run / Attempt, Activity Event, Safe Tool-Action Summary.
- [Agent Registry Design](2026-10-27-01-agent-registry.md)
- [Event System Design](2026-10-27-03-event-system.md)
- [phase-plan.md](../phase-plan.md)
