# 04 — Virtual Office 3D Design Doc

| | |
|---|---|
| **Status** | In-Review |
| **Author(s)** | Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-10-29 |
| **Service Terkait** | `apps/web`, React Three Fiber, Three.js, GLB assets, `apps/api` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Ringkasan (TL;DR)

Virtual Office menampilkan satu representasi visual untuk setiap persisted `Agent Instance`: hingga 20 rig humanoid GLB pada desktop (8 pada tampilan mobile) dan marker statis untuk sisanya. Identitas berasal dari API, animasi hanya dari projection event yang fresh dan didukung, serta status yang tidak tersedia/stale tidak ditebak; scene dan kamera tidak mengubah status Run, permission, atau lokasi workspace.

## Latar Belakang & Masalah

Aplikasi sebelumnya memiliki scene React Three Fiber dengan Player Avatar lokal dan movement, tetapi belum menampilkan rig untuk Agent Instance. Cutover ini menghapus avatar/movement lokal dan menampilkan hanya Agent Instance persisted—tanpa sample records. Scene memakai grid presentasi stabil, renderer mobile yang disederhanakan, daftar/detail HTML, dan fallback WebGL; belum ada live Run terautentikasi yang terverifikasi menggerakkan scene.

## Tujuan

- Satu representasi scene untuk setiap persisted `Agent Instance`: maksimal 20 rig rinci pada desktop dan 8 pada mobile; sisanya marker statis, sementara daftar HTML tetap lengkap.
- Menyajikan identity, status, Task, dan Safe Tool-Action Summary hanya dari API/event persisted; tanpa sample data atau status hasil inferensi.
- Menggunakan satu rig GLB self-hosted berlisensi CC0 dengan variasi aksen deterministik; tidak membuat model baru per role.
- Menjaga identitas `Agent Instance`, Run state, Visual Agent State, dan presentasi scene terpisah; anchor 3D tidak menjadi lokasi workspace tersimpan.
- Menyediakan orbit, zoom, pan, focus-agent, reset, reduced motion, pemilihan HTML yang dapat diakses, serta fallback WebGL yang mempertahankan list/detail.
- Mengukur target PRD pada hardware/network profile yang direkam; angka performa belum lulus gate.

## Non-Tujuan (Out of Scope)

- Menyimulasikan status/aktivitas agent ketika tidak ada Activity Event nyata.
- Menjadikan kamar/meja/kedekatan avatar sebagai batas akses atau izin project.
- Model unik dan rig berbeda untuk setiap Agent Instance, multiplayer manusia, physics/game simulation, atau editor office bebas.
- Character navigation sebagai P0; pergerakan agent ke workstation dan dynamic allocation mengikuti prioritas P1 PRD.

## Proposal / Desain

### Data dan scene boundary

- Scene memakai grid deterministik berbasis ID sebagai presentasi saja. Tidak menyimpan Office Scene Version, workstation assignment, koordinat Agent Instance, atau lokasi Run.
- Agent List API memasok identitas persisted `Agent Instance`; event projection memasok status/freshness dan Safe Tool-Action Summary. Renderer tidak menyimpan state kerja kanonis dan mengabaikan instance archived.
- `Agent Instance.id` mengikat satu representasi visual; hingga 20 (desktop) atau 8 (mobile) menerima rig rinci, sisanya ditampilkan sebagai marker statis yang tetap dapat dipilih dari scene/list. `Run / Attempt` tetap record eksekusi terpisah.
- P0 menampilkan identity/status indicator dan selection/detail panel. P1 workspace allocation dan agent navigation berada di luar scope fase ini.
- Grid, focus kamera, warna aksen, serta anchor visual tidak mengubah policy, permission, atau lokasi workspace.

### Event-to-animation

Animasi hanya dipilih dari projection yang `fresh`: `running` kecuali event terakhir `run.cancellation_pending` menggunakan clip `CharacterArmature|Interact`; `succeeded` menggunakan clip satu-kali `CharacterArmature|Wave`. Projection stale, hilang, status lain, cancellation-pending, dan projection yang tidak didukung menggunakan pose netral. Badge status tetap menampilkan projection/freshness; missing projection ditulis “Status unavailable”, bukan `idle`. Ketika `prefers-reduced-motion` aktif, animasi clip tidak dijalankan.
Inspector menampilkan tipe/waktu Activity Event terakhir yang terverifikasi dan Safe Tool-Action Summary terakhir hanya dari projection persisted. Summary tersebut bersifat historis, bukan klaim aktivitas saat ini; nilai kosong ditampilkan sebagai belum ada bukti. Penambahan metadata ini tidak mengubah event-to-animation mapping.

Rig `Business Man.glb` berasal dari Quaternius Ultimate Modular Men Pack berlisensi CC0 dan di-host di `apps/web/public/assets/agents/`. Clip yang tersedia diperiksa; saat ini hanya `Interact` dan `Wave` yang dipakai. Aksen deterministik hanya mewarnai material dasi dan tidak mengklaim role. Browser fixtures terpisah digunakan untuk UI smoke saja; tidak pernah menjadi runtime evidence.

### Kamera, interaksi, dan fallback

OrbitControls mendukung orbit/pan/zoom, focus-agent, dan reset tanpa menggantikan navigasi keyboard. Pemilihan rig atau marker menghubungkan scene ke Agent Detail Panel HTML; list dapat dicari/filter/dipilih via keyboard. WebGL error boundary menampilkan fallback list/detail dan opsi retry. Mobile memakai maksimal 8 rig rinci, DPR/shadow lebih rendah; `prefers-reduced-motion` menonaktifkan animasi karakter. Local Player Avatar dan movement telah dihapus.

### Performa

Target PRD awal (render awal ≤5 detik, 20 rig, ≥30 FPS desktop) tetap provisional. Browser smoke fixture menampilkan 20 rig rinci dan overflow, tetapi bukan benchmark FPS/draw-call atau bukti live runtime. Ukur transfer/decode asset, draw calls, animation updates, shadows, serta interaksi pada hardware/DPR/network yang direkam; mobile simplification belum diuji pada perangkat pilot. Tidak ada klaim 30 FPS sebelum pengukuran di profil yang disepakati.

## Alternatif yang Dipertimbangkan

| Alternatif | Kelebihan | Kekurangan | Kenapa tidak dipilih |
|---|---|---|---|
| **Satu rig GLB dengan variasi material/aksesori (dipilih)** | Identitas konsisten, aset/animasi reusable, biaya render dan maintenance terkendali. | Asset license/rig/clip harus diperiksa; variasi role terbatas. | Sesuai keputusan pengguna dan target 20 karakter PRD. |
| Model/rig custom per role | Ekspresi visual/role lebih kuat. | Banyak aset, animasi, lisensi, ukuran download, dan optimasi berbeda. | Terlalu mahal untuk tahap ini dan menyulitkan perf target. |
| Geometri prosedural saja | Tidak memerlukan asset pipeline atau lisensi eksternal. | Tidak memenuhi target karakter humanoid beranimasi pada PRD baru. | Hanya fallback teknis sementara, bukan desain final. |

## Dampak & Risiko

- **Backward compatibility**: menghapus Player Avatar lokal dan movement mengubah perilaku office lama; ID/API Agent Instance tetap. Komponen scene/list/detail dipindahkan bersama, bukan lewat alias.
- **Migrasi data**: tidak ada skema lokasi scene/workstation yang ditambah atau diubah; anchor 3D hanya presentasi. Data lokal movement lama tidak digunakan oleh scene baru.
- **Performa**: jumlah rig rinci dibatasi 20 desktop/8 mobile dan overflow memakai marker instanced; benchmark pada perangkat pengguna, FPS, draw calls, serta transfer/decode tetap belum terukur.
- **Keamanan**: label hanya memakai field Agent Instance dan projection yang telah diotorisasi server; anchor/room tidak memberi akses. GLB CC0 di-self-host, tidak memuat credential/data agent.
- **Operasional**: asset same-origin harus tersedia; WebGL/asset failure mempertahankan list/detail fallback dan retry. Browser fixtures tidak disertakan pada production route.

## Rencana Implementasi (opsional)

1. Pilih dan verifikasi asset source/lisensi serta clip set — selesai: Quaternius Ultimate Modular Men Pack, `Business Man.glb`, CC0.
2. Render persisted Agent Instance menggunakan rig GLB, status projection/freshness, neutral fallback, dan static overflow markers — implementasi tersedia; authenticated Compose menampilkan 7 rig, live event path belum dibuktikan.
3. Tambahkan camera controls, accessibility, responsive simplification, dan WebGL fallback — implementasi tersedia; canvas dan asset telah diverifikasi di Compose, fallback failure path belum diuji ulang.
4. Ukur render awal, FPS, draw calls, transfer/decode, dan interaction pada profile target; verifikasi Runner→API→authenticated browser — tetap terbuka; fixture smoke bukan pengganti.

## Pertanyaan Terbuka

- [perlu konfirmasi: hardware/browser/DPR/network profile untuk acceptance render, mobile, dan benchmark FPS] -- terbuka
- [perlu konfirmasi: live Runner → API → authenticated browser evidence dan event taxonomy/freshness contract yang dipakai 3D] -- terbuka; bergantung pada Phase 2/3
- [perlu konfirmasi: kebijakan workspace/workstation assignment dan kapasitas lokasi visual] -- ditunda ke scope P1; tidak ada lokasi nyata saat ini
- [perlu konfirmasi: reviewer] -- terbuka

## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §13–16, §24, §26.
- [GLOSSARY.md](../../GLOSSARY.md): Agent Instance, Office Scene Version, Player Avatar / Presence, Visual Agent State, Activity Event.
- [Event System Design](2026-10-27-03-event-system.md)
- [Agent Registry Design](2026-10-27-01-agent-registry.md)
- Model asset: [Business Man — Poly Pizza](https://poly.pizza/bundle/Ultimate-Modular-Men-Pack-ZiH8muWqwQ), Quaternius Ultimate Modular Men Pack, CC0; asset file di-host sendiri.
