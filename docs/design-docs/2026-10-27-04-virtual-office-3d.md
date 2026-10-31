# 04 — Virtual Office 3D Design Doc

| | |
|---|---|
| **Status** | In-Review |
| **Author(s)** | Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-10-30 |
| **Service Terkait** | `apps/web`, React Three Fiber, Three.js, GLB assets, `apps/api` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Ringkasan (TL;DR)

Virtual Office menampilkan satu workstation per persisted `Agent Instance` aktif dan satu Rest Room berisi kursi untuk setiap instance. `Agent Availability` turunan API menempatkan agent idle di Rest Room, agent dengan Run aktif dan bukti fresh di workstation, serta waiting/unknown dalam pose netral; tujuh avatar GLB CC0 dipilih otomatis secara deterministik.

## Latar Belakang & Masalah

Aplikasi memakai scene React Three Fiber untuk `Agent Instance` persisted tanpa sample records. Workstation, Rest Room, area meeting, dan pergerakan hanyalah presentasi; lokasi tidak disimpan dan tidak memberi izin. Status ruang istirahat berasal dari read model availability yang owner-scoped, bukan inferensi UI atau status permanen pada `Agent Instance`.

## Tujuan

- Satu workstation per persisted `Agent Instance` aktif serta Rest Room terpisah dengan kursi unik untuk tiap instance; posisi avatar idle tepat di pusat kursinya dan jarak minimum antarpusat kursi 2.4m untuk menghindari tumpang-tindih saat roster membesar.
- `Agent Availability` tetap sinkron dengan Run dispatch unresolved dan projection Activity Event fresh: hanya `idle` masuk Rest Room; `working` berjalan ke workstation dan mengetik; `waiting`/`unknown` tetap di workstation tanpa pose mengetik.
- Menggunakan sebelas model humanoid Quaternius CC0 dari dua rig; pilihan model deterministik per roster ID, benturan indeks dihindari bila jumlah instance tidak melebihi katalog.
- Menjaga identitas `Agent Instance`, Run state, Visual Agent State, Agent Availability, dan presentasi scene terpisah; anchor 3D bukan lokasi workspace tersimpan.
- Menyediakan orbit, zoom, pan, focus-agent, reset, reduced motion, pemilihan HTML yang dapat diakses, serta fallback WebGL yang mempertahankan list/detail.
- Menyediakan filter Project/Agent Definition, deep link `#<Agent Instance.id>`, dan CTA Create Task yang mengisi agent serta Project yang tersedia; Run tetap tindakan eksplisit.
- Mengukur target PRD pada hardware/network profile yang direkam; angka performa belum lulus gate.

## Non-Tujuan (Out of Scope)

- Menyimulasikan status/aktivitas agent ketika tidak ada Activity Event nyata.
- Menjadikan kamar/meja/kedekatan avatar sebagai batas akses atau izin project.
- Model/rig custom khusus per `Agent Instance`, multiplayer manusia, physics/game simulation, editor office bebas, persisted workspace assignment, dynamic allocation, atau role-based areas. Waypoint saat ini hanya presentasi dan mengikuti availability terverifikasi.
- Direct chat, prompt chat, streamed response, dan chat history tidak disimulasikan karena API saat ini tidak menyediakan kontrak percakapan; UI menggunakan Task/Run API yang ada.

## Proposal / Desain

### Data dan scene boundary

- Layout menempatkan workstation di ruang kerja, area meeting bersama di depannya, dan Rest Room terpisah di bagian belakang. Koordinat dihitung dari daftar aktif `Agent Instance` terurut ID; seat-center clearance minimum 2.4m dipertahankan dengan room growth dan tidak disimpan.
- Setiap instance mendapat workstation dan satu kursi Rest Room; jumlah baris dan kedalaman ruangan bertambah mengikuti roster. Posisi bukan `Office Scene Version`, assignment, koordinat Agent Instance, atau lokasi Run.
- API memasok `Agent Availability` owner-scoped dari dispatch `Run / Attempt` unresolved dan Activity Event projection. Tidak ada unresolved dispatch berarti idle, termasuk setelah Run terminal; instance asing/archived atau data hilang menghasilkan unknown.
- Availability mapping: `working` memerlukan dispatch `leased` dan projection `running` fresh; `waiting` mencakup queued/blocked, input/approval, atau cancellation pending; `unknown` mencakup stale, hilang, konflik, atau API unavailable. Hanya idle beristirahat; unknown tidak pernah diperlakukan sebagai idle.
- Idle bergerak melalui waypoint ke kursi Rest Room; working bergerak melalui waypoint ke workstation dan menjalankan pose duduk/mengetik. Waiting/unknown berada pada workstation tanpa pose mengetik. State dibaca ulang tiap 5 detik; kegagalan refresh mengosongkan map sehingga tampilan menjadi unknown.
- Project/Agent Definition chips memfilter daftar record aktual tanpa membuat Division atau zona visual terpisah. Pemilihan menggunakan `#<Agent Instance.id>`; ID tidak valid/arsip dibersihkan dari URL dengan pesan non-blocking. Owner session tetap diwajibkan oleh API.
- CTA Create Task membuka `/tasks/new` dengan Agent Instance terpilih dan Project saat ini jika ada. Task baru tersimpan hanya setelah submit; Run tetap memerlukan preflight dan tindakan eksplisit.
- Rig GLB rinci dibatasi 20 desktop/8 mobile; selebihnya marker tetap dapat dipilih. Tujuh GLB dipilih otomatis secara deterministik berdasarkan ID dan collision-resolved untuk roster hingga ukuran katalog; anchor marker juga tidak menyimpan assignment.
- Kursi, meja, komputer, ruangan, waypoint, kamera, dan warna aksen tidak mengubah policy, permission, atau lokasi workspace.

### Availability-to-animation

Animasi karakter hanya mengikuti `Agent Availability`, bukan label event tunggal. `working` memerlukan dispatch `leased` dan projection `running` fresh; hanya state ini yang mengetik. `idle` berarti tidak ada dispatch unresolved dan menempatkan avatar di Rest Room, termasuk setelah Run terminal. `waiting` dan `unknown` tetap di workstation dalam pose netral; status hilang/stale/kontradiktif/API error tidak pernah menghasilkan idle. Cancellation pending adalah waiting.

Availability endpoint di-refresh setiap lima detik. Jika panggilan gagal, availability lokal dibersihkan dan karakter kembali ke workstation netral sampai bukti baru tersedia. Run terminal yang sudah tidak memiliki dispatch unresolved tidak mencegah status idle. Inspector tetap menampilkan projection event terpisah dari availability; Safe Tool-Action Summary adalah bukti historis, bukan klaim aktivitas saat ini.

Tujuh model Ultimate Modular Men Pack dan empat karakter [Cube World Kit](https://quaternius.com/packs/cubeworldkit.html) berasal dari Quaternius (CC0) dan di-host di `apps/web/public/assets/agents/`. Cube World dikonversi dari glTF ke GLB dengan tekstur PNG same-origin agar sesuai CSP; rig dan nama clip-nya berbeda. `Walk` dan `Wave` dipetakan per rig, sedangkan duduk/mengetik dibuat prosedural. Reduced motion melewati pergerakan berulang. Browser fixtures hanya untuk smoke visual; bukan runtime evidence.

### Kamera, interaksi, dan fallback

OrbitControls mendukung orbit/pan/zoom, focus-agent, dan reset tanpa menggantikan navigasi keyboard. Pemilihan rig atau marker menghubungkan scene ke Agent Detail Panel HTML; list dapat dicari/filter/dipilih via keyboard. WebGL error boundary menampilkan fallback list/detail dan opsi retry. Mobile memakai maksimal 8 rig rinci, DPR/shadow lebih rendah; `prefers-reduced-motion` menghentikan berjalan, typing, dan gerak badan berulang.

### Performa

Target PRD awal (render awal ≤5 detik, 20 rig, ≥30 FPS desktop) tetap provisional. Workstation furniture memakai instanced geometry per bagian; meja/chairs bersama juga memakai instancing untuk bagian berulang. Layout menyediakan shared strip tambahan tanpa mengurangi workstation, tetapi pengukuran FPS/draw-call, transfer/decode, serta perangkat pilot tetap belum dilakukan.

## Alternatif yang Dipertimbangkan

| Alternatif | Kelebihan | Kekurangan | Kenapa tidak dipilih |
|---|---|---|---|
| **Tujuh rig GLB CC0 dengan animasi/pose bersama (dipilih)** | Tampilan agent bervariasi tanpa pipeline animasi berbeda; rig dan clip konsisten. | Asset bertambah ukuran download; benturan model perlu diselesaikan deterministik. | Memenuhi kebutuhan avatar berbeda per `Agent Instance` dengan aset self-hosted yang lisensinya terverifikasi. |
| Model/rig custom per role | Ekspresi visual/role lebih kuat. | Banyak aset, animasi, lisensi, ukuran download, dan optimasi berbeda. | Terlalu mahal untuk tahap ini dan menyulitkan perf target. |
| Geometri prosedural saja | Tidak memerlukan asset pipeline atau lisensi eksternal. | Tidak memenuhi target karakter humanoid beranimasi pada PRD baru. | Hanya fallback teknis sementara, bukan desain final. |

## Dampak & Risiko

- **Backward compatibility**: katalog avatar berisi sebelas model; penambahan model dapat mengubah pemetaan visual deterministik, tetapi API Agent Instance dan ID tetap. Status scene memakai API Agent Availability owner-scoped.
- **Migrasi data**: tidak ada skema lokasi scene/workstation/status yang ditambah atau diubah; anchors hanya presentasi.
- **Performa**: GLB rinci dibatasi 20 desktop/8 mobile; sebelas model menambah transfer, decode, dan biaya GPU. Benchmark FPS/draw call, transfer/decode, dan perangkat pilot belum diukur.
- **Keamanan**: API membatasi availability ke owner session; client tidak dapat menyetel owner. Ruangan/anchor tidak memberi akses; GLB CC0 self-hosted tidak memuat credential atau data agent.
- **Operasional**: kegagalan refresh availability menurunkan tampilan menjadi neutral `unknown`; kegagalan GLB same-origin memakai fallback list/WebGL yang tersedia. Browser smoke bukan bukti Run nyata atau kapasitas produksi.

## Rencana Implementasi (opsional)

1. Verifikasi asset source/lisensi dan clip set — selesai: Quaternius Ultimate Modular Men Pack, tujuh model CC0 dengan rig yang sama.
2. Expose owner-scoped Agent Availability dari unresolved dispatch dan fresh Activity Event projection — implementasi ada; boundary tests dan isolated PostgreSQL integration pass.
3. Sediakan workstation, kursi unik Rest Room, pose duduk/mengetik, dan perpindahan waypoint — implementasi ada; regression test cover anchor-seat equality dan ≥2.4m seat-center clearance pada roster 21, authenticated desktop/mobile smoke pass. Browser-only status override memperlihatkan work/wait movement; belum menjadi bukti Run nyata.
4. Render tujuh GLB berbeda, deterministic per ID dan collision-resolved selama roster ≤ katalog — implementasi ada; tujuh asset requests HTTP 200 dan tujuh instance aktif mendapat model berbeda.
5. Camera, accessibility, responsive simplification, filter/deep link, Task shortcut, dan WebGL fallback — authenticated desktop/mobile smoke pass; fallback WebGL dan reduced-motion path belum diverifikasi ulang setelah rebuild.
6. Ukur render time, FPS, draw calls, transfer/decode, dan interaction pada desktop/mobile hardware profile — belum dilakukan; tetap gate Phase 4.

## Pertanyaan Terbuka

- [perlu konfirmasi: hardware/browser/DPR/network profile untuk acceptance render, mobile, dan benchmark FPS] — terbuka.
- Live Runner → API → browser: Run `beb9d554-b687-4109-8f05-0ba1e42794d8` menampilkan agent sementara sebagai `working` di workstation dengan safe action summary, lalu `idle` setelah sukses. Pose mengetik tertutup label pada screenshot; dua Run independen secara bersamaan di 3D dan failure/reconnect masih terbuka.
- Workspace assignment tersimpan dan dynamic workspace allocation tetap P1 yang belum diimplementasikan; waypoint current hanya presentasi mengikuti `Agent Availability`.
- [perlu konfirmasi: reviewer] — terbuka.

## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §13–16, §24, §26.
- [GLOSSARY.md](../../GLOSSARY.md): Agent Instance, Office Scene Version, Player Avatar / Presence, Visual Agent State, Activity Event.
- [Event System Design](2026-10-27-03-event-system.md)
- [Agent Registry Design](2026-10-27-01-agent-registry.md)
- Model asset source: [Ultimate Modular Men Pack — Poly Pizza](https://poly.pizza/bundle/Ultimate-Modular-Men-Pack-ZiH8muWqwQ), Quaternius, CC0; tujuh GLB di-host sendiri.
