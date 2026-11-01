# 04 — Virtual Office 3D Design Doc

| | |
|---|---|
| **Status** | In Review |
| **Author(s)** | Anjar |
| **Reviewer(s)** | [perlu konfirmasi: reviewer] |
| **Tanggal Dibuat** | 2026-10-27 |
| **Terakhir Diupdate** | 2026-11-01 |
| **Service Terkait** | `apps/web`, React Three Fiber, Three.js, GLB assets, `apps/api` |
| **Ticket / Issue** | N/A — tidak diberikan |

## Ringkasan (TL;DR)

Virtual Office menampilkan satu workstation per `Agent Instance` aktif, dibagi ke Office Area bernama dan Rest Room dengan kursi khusus per instance. `office_area` tersimpan sebagai penempatan visual; koordinat workstation, rute, dan kursi istirahat dihitung dari layout scene aktif. Availability tetap menentukan posisi kerja/istirahat; Office Area tidak memberi akses. Sebelas GLB CC0 dipilih otomatis secara deterministik.

## Latar Belakang & Masalah

Aplikasi memakai scene React Three Fiber untuk `Agent Instance` persisted tanpa sample records. Office Area Assignment menyimpan zona visual; koordinat workstation, kursi istirahat, dan rute tetap dihitung oleh scene layout. Tidak ada lokasi/avatar yang mengubah otorisasi project atau filesystem. Status berasal dari read model availability owner-scoped, bukan inferensi UI.

## Tujuan

- Satu workstation per persisted `Agent Instance` aktif serta Rest Room terpisah dengan kursi unik untuk tiap instance; posisi avatar idle tepat di pusat kursinya dan jarak minimum antarpusat kursi 2.4m untuk menghindari tumpang-tindih saat roster membesar.
- `Agent Availability` tetap sinkron dengan Run dispatch unresolved dan projection Activity Event fresh: hanya `idle` masuk Rest Room; `working` berjalan ke workstation dan mengetik; `waiting`/`unknown` tetap di workstation tanpa pose mengetik.
- Menggunakan sebelas model humanoid Quaternius CC0 dari dua rig; pilihan model deterministik per roster ID, benturan indeks dihindari bila jumlah instance tidak melebihi katalog.
- Menjaga identitas `Agent Instance`, `Office Area Assignment`, Run state, Visual Agent State, Agent Availability, dan presentasi scene terpisah; area visual tidak menyimpan path workspace dan tidak memberi akses.
- Menyediakan orbit, zoom, pan, focus-agent, reset, reduced motion, pemilihan HTML yang dapat diakses, serta fallback WebGL yang mempertahankan list/detail.
- Menyediakan filter Project/Agent Definition, deep link `#<Agent Instance.id>`, dan CTA Create Task yang mengisi agent serta Project yang tersedia; Run tetap tindakan eksplisit.
- Memenuhi target 30 FPS desktop pada profil referensi Chrome yang disetujui Owner, dengan benchmark 20 Agent Instances synthetic yang dibedakan antara GLB rinci dan low-poly. Hasil ini tidak menyatakan performa perangkat pilot atau mobile fisik.

## Non-Tujuan (Out of Scope)

- Menyimulasikan status/aktivitas agent ketika tidak ada Activity Event nyata.
- Menjadikan kamar/meja/kedekatan avatar sebagai batas akses atau izin project.
- Custom rigs per instance, multiplayer manusia, physics/game simulation, dan editor bebas untuk geometri scene.
- Direct chat, prompt chat, streamed response, dan chat history tidak disimulasikan karena API saat ini tidak menyediakan kontrak percakapan; UI menggunakan Task/Run API yang ada.

## Proposal / Desain

### Data dan scene boundary

- Layout memisahkan enam Office Area yang dapat ditetapkan dari Rest Room dan Reception sebagai ruang fisik bersama. Setiap instance aktif mendapat satu workstation di area yang ditetapkan dan satu kursi istirahat khusus. Geometri zona dan ruangan bertambah mengikuti roster, dengan clearance antarpusat workstation agar tidak bertabrakan.
- Owner memilih Office Area pada Agent Instance. Jika tidak dipilih saat pembuatan, API menempatkan instance ke area dengan jumlah agent paling sedikit; urutan tie-break tetap. `office_area` menyimpan salah satu dari Architecture, Engineering, Review, Security, Meeting, atau Lounge; Reception tidak disimpan atau digunakan sebagai lokasi default instance.
- Reception dirender sebagai ruang masuk bersama dengan meja, papan nama, dan area tunggu; geometri berada dalam envelope ruang bersama yang elastis dan tidak menjadi Office Area ketujuh.
- API memasok `Agent Availability` owner-scoped dari dispatch `Run / Attempt` unresolved dan Activity Event projection. Tidak ada unresolved dispatch berarti idle, termasuk setelah Run terminal; instance asing/archived atau data hilang menghasilkan unknown.
- Availability mapping: `working` requires a `leased` dispatch and fresh `running` projection; `waiting` covers queued/blocked, input/approval, or cancellation pending; `offline` requires a `leased` dispatch, active projection, and persisted `session.heartbeat` older than 30 seconds with no later Activity Event; `unknown` covers missing/conflicting evidence, stale activity without a qualifying heartbeat, or API unavailable. Only idle rests; offline is inactive at the workstation; unknown never implies idle or offline.
- Idle agents use waypoints to reach the Rest Room; working agents use waypoints to reach a workstation and sit/type. Waiting, offline, and unknown remain at the workstation without typing; offline uses the inactive pose while unknown stays neutral. State refreshes every five seconds; refresh failure clears availability so agents become unknown.
- Filter Project/Agent Definition memilih record tersimpan; Office Area bernama adalah zona fisik untuk presentasi, bukan subdivisi Project atau permission. Seleksi menggunakan `#<Agent Instance.id>`; ID invalid/arsip dibersihkan dengan notice non-blocking. API tetap memerlukan Owner session.
- CTA Create Task membuka `/tasks/new` dengan Agent Instance terpilih dan Project saat ini jika ada. Task baru tersimpan hanya setelah submit; Run tetap memerlukan preflight dan tindakan eksplisit.
- Rig GLB rinci dibatasi maksimal 8 di semua viewport; seluruh Agent Instance lainnya tetap terlihat dan dapat dipilih melalui karakter humanoid low-poly berinstancing. Pemilihan GLB deterministik per ID dan collision-resolved untuk roster hingga ukuran katalog.
- Kursi, meja, komputer, Reception, ruangan, waypoint, kamera, dan warna aksen tidak mengubah policy, permission, atau lokasi workspace.

### Availability-to-animation

Penempatan scene (Rest Room/workstation), pergerakan, dan kelayakan pose kerja mengikuti `Agent Availability`. Di dalam state tersebut, `deriveOfficeVisualState(availability, projection)` hanya memakai projection fresh yang konsisten: `run.started` → planning; `run.working` → working; `run.tool_started` sebelum `workspace.call` → using tool; `run.reviewing` pada model call setelah hasil tool → reviewing; bukti approval saat waiting → waiting-approval; `run.succeeded`/`run.failed` fresh pada availability idle → completed/failed; offline yang dibuktikan heartbeat Runtime Session → inactive. Cue planning/tool-start/reviewing dibatasi 30 detik sejak `last_event_at`, terpisah dari freshness heartbeat; setelahnya pose kembali ke availability yang terverifikasi. `run.tool_action` tetap ringkasan aksi yang sudah selesai, bukan indikator tool sedang berjalan. Cue tidak mengubah availability atau lokasi. `unknown`, projection stale/hilang tanpa heartbeat Runtime Session kedaluwarsa yang valid, dan state kontradiktif tidak pernah ditafsirkan sebagai offline atau idle.

Availability endpoint di-refresh setiap lima detik. Jika panggilan gagal, availability lokal dibersihkan dan karakter kembali ke workstation netral sampai bukti baru tersedia. Run terminal tanpa dispatch unresolved tidak mencegah status idle. Inspector tetap menampilkan projection event terpisah dari availability; `Safe Tool-Action Summary` adalah bukti historis, bukan klaim aktivitas saat ini. `run.reviewing` hanya berarti model memproses hasil tool berikutnya, bukan code review. Offline berarti Runtime Session untuk dispatch `leased` kehilangan heartbeat persisted lebih dari 30 detik tanpa Activity Event yang lebih baru; idle tanpa Run tetap idle, bukan offline. State tidak menambah kolom pada `Agent Instance` atau menciptakan Runtime Session yang selalu hidup.

Tujuh model Ultimate Modular Men Pack dan empat karakter [Cube World Kit](https://quaternius.com/packs/cubeworldkit.html) berasal dari Quaternius (CC0) dan di-host di `apps/web/public/assets/agents/`. Cube World dikonversi dari glTF ke GLB dengan tekstur PNG same-origin agar sesuai CSP; rig dan nama clip-nya berbeda. `Walk` dan `Wave` dipetakan per rig, sedangkan duduk/mengetik dibuat prosedural. Reduced motion melewati pergerakan berulang. Browser fixtures hanya untuk smoke visual; bukan runtime evidence.

### Kamera, interaksi, dan fallback

OrbitControls mendukung orbit/pan/zoom, focus-agent, dan reset tanpa menggantikan navigasi keyboard. Pemilihan rig atau marker menghubungkan scene ke Agent Detail Panel HTML; list dapat dicari/filter/dipilih via keyboard. WebGL error boundary menampilkan fallback list/detail dan opsi retry. Mobile memakai maksimal 8 rig rinci, DPR/shadow lebih rendah; `prefers-reduced-motion` menghentikan berjalan, typing, dan gerak badan berulang.

### Performa

Pada profil referensi yang disetujui Owner (Chrome 156, 1440×900 CSS, DPR 1.25, 6 logical CPUs, 8 GiB reported), scene synthetic berisi 20 Agent Instances idle—maksimal 8 GLB rinci dan sisanya low-poly—mencapai 34.3 FPS selama 5.099 detik ketika document visible dan focused. Hasil lokal ini melewati target awal ≥30 FPS pada sample tersebut, tetapi bukan jaminan kapasitas pilot. Pada viewport 390×844 CSS, document width 375px tanpa horizontal overflow; ini bukan benchmark perangkat mobile fisik.

Diagnostik browser sebelumnya pada 7 instance tersimpan (6 idle, 1 unknown) tetap dicatat sebagai data historis, bukan hasil versi terkini: sebelum static-pose caching, sample 4 detik 48.5 FPS pada 1867×931 dan 47.2 FPS pada emulasi 390×844; sesudah perubahan, dua sample desktop 41.6/40.0 FPS dan mobile-viewport 59.6 FPS. Sample variatif ini tidak membuktikan peningkatan desktop atau performa mobile fisik.

## Alternatif yang Dipertimbangkan

| Alternatif | Kelebihan | Kekurangan | Kenapa tidak dipilih |
|---|---|---|---|
| **Tujuh rig GLB CC0 dengan animasi/pose bersama (dipilih)** | Tampilan agent bervariasi tanpa pipeline animasi berbeda; rig dan clip konsisten. | Asset bertambah ukuran download; benturan model perlu diselesaikan deterministik. | Memenuhi kebutuhan avatar berbeda per `Agent Instance` dengan aset self-hosted yang lisensinya terverifikasi. |
| Model/rig custom per role | Ekspresi visual/role lebih kuat. | Banyak aset, animasi, lisensi, ukuran download, dan optimasi berbeda. | Terlalu mahal untuk tahap ini dan menyulitkan perf target. |
| Geometri prosedural saja | Tidak memerlukan asset pipeline atau lisensi eksternal. | Tidak memenuhi target karakter humanoid beranimasi pada PRD baru. | Hanya fallback teknis sementara, bukan desain final. |

## Dampak & Risiko

- **Backward compatibility**: katalog avatar berisi sebelas model; penambahan model dapat mengubah pemetaan visual deterministik, tetapi API Agent Instance dan ID tetap. Status scene memakai API Agent Availability owner-scoped.
- **Migrasi data**: tidak ada skema lokasi scene/workstation/status yang ditambah atau diubah; anchors hanya presentasi.
- **Performa**: maksimal 8 GLB rinci; roster selebihnya memakai humanoid low-poly yang dapat dipilih. Sample synthetic 20-instance pada profil referensi Owner terukur 34.3 FPS selama 5.099 detik saat visible dan focused, melampaui target awal ≥30 FPS. Sample 38.1 FPS/5 detik yang lebih awal dilakukan saat tab tidak focused dan diperlakukan sebagai data historis. Fisik pilot/mobile belum dipilih atau diukur; cold render, draw calls, dan transfer asset uncached tetap belum terverifikasi.
- **Keamanan**: API membatasi availability ke owner session; client tidak dapat menyetel owner. Ruangan/anchor tidak memberi akses; GLB CC0 self-hosted tidak memuat credential atau data agent.
- **Operasional**: kegagalan refresh availability menurunkan tampilan menjadi neutral `unknown`; kegagalan GLB same-origin memakai fallback list/WebGL yang tersedia. Browser smoke bukan bukti Run nyata atau kapasitas produksi.

## Rencana Implementasi (opsional)

1. Verifikasi asset source/lisensi dan clip set — selesai: Quaternius Ultimate Modular Men Pack, tujuh model CC0 dengan rig yang sama.
2. Expose owner-scoped Agent Availability dari unresolved dispatch dan fresh Activity Event projection — implementasi ada; boundary tests dan isolated PostgreSQL integration pass.
3. Sediakan workstation, kursi unik Rest Room, pose duduk/mengetik, dan perpindahan waypoint — implementasi ada; regression test cover anchor-seat equality dan ≥2.4m seat-center clearance pada roster 21, authenticated desktop/mobile smoke pass. Office browser menampilkan Run nyata yang bekerja di workstation dan instance Offline persisted dalam pose inactive; test status override tetap hanya bukti visual tambahan, bukan runtime evidence.
4. Render tujuh GLB berbeda, deterministic per ID dan collision-resolved selama roster ≤ katalog — implementasi ada; tujuh asset requests HTTP 200 dan tujuh instance aktif mendapat model berbeda.
5. Camera, accessibility, responsive simplification, filter/deep link, Task shortcut, dan WebGL fallback — final authenticated browser smoke; WebGL fallback dan reduced-motion telah diverifikasi ulang; viewport 390×844 CSS tidak overflow dan menggunakan cap 8 detailed avatars.
6. Verifikasi performa/integrasi — FR-3D-009 responsive smoke: viewport 390×844 CSS menghasilkan document width 375px tanpa horizontal overflow; seluruh 20 record dapat dipilih; satu canvas dan sign Reception tampil; key membedakan enam Office Area assignable dari Reception. Pada profil reference Owner (1440×900 CSS, DPR 1.25, Chrome 156, 6 logical CPUs, 8 GiB reported), satu sample RAF focused/visible 20-agent synthetic menghasilkan 34.3 FPS selama 5.099 detik, di atas target awal ≥30 FPS. Sample ini bukan tes perangkat pilot fisik. Diagnostik asset sebelumnya merekam 8 GLB (2,080,571 encoded-body bytes), tetapi transferSize 2,400 bytes menunjukkan cached-resource timing dan tidak mengukur uncached transfer. Cold-load, draw calls, GPU identity, serta mobile/pilot fisik tetap tidak diverifikasi.

## Keputusan dan Batas Verifikasi

- Referensi performa yang disetujui Owner: Chrome 156, 1440×900 CSS, DPR 1.25; sample versi terkini focused/visible memberi 34.3 FPS selama 5.099 detik pada 20 instance synthetic (6 logical CPUs, 8 GiB reported). Ini bukti lokal pada fixture, bukan klaim kapasitas produksi atau pilot hardware; GPU identity masked.
- Offline→Inactive terbukti pada probe live Run `7ca1195b-5775-40ac-9a35-249c4292068f`: heartbeat persisted, dispatch leased, API mengembalikan `offline`, Office menampilkan `1 offline`; setelah Runner dipulihkan, Run sukses dan availability kembali idle. Actual controlled HTTP 503 failure and network offline→online restored the feed; synthetic fresh `run.failed` showed the scene’s `Failed` cue, and closing/reconnecting the fixture’s WebSocket displayed `disconnected` → `connected`. A live provider-failure avatar rendering and behavior during a real network partition were not claimed.
- Dua Runs live lintas Codex+NineRouter overlap dan sukses. Guard same-account Codex HTTP 429 tetap dipertahankan di Phase 6.
- FR-3D-007/008 telah diimplementasikan: assignment `office_area` persisten, area least-populated deterministic, workstation unik dan layout elastis. Migration `office_area_0020`, owner/archive boundary, legacy backfill, dan layout diuji; browser fixture menampilkan enam nama area.
- Keputusan Owner (2026-11-01): Reception adalah ruang fisik bersama, bukan Office Area ketujuh, lokasi default, atau assignment Agent Instance. PRD §14.1 dan FR-3D-007 kini konsisten: enam Office Area tetap menjadi pilihan persistensi; Reception hanya berada pada scene bersama.
- Independent code review menemukan geometri Reception awalnya hanya diuji lewat jalur balanced-count; regression test kini mencakup juga layout dari persisted Office Area array. Re-review: no findings. Design doc tetap `In Review` sampai human review sesuai status flow di `README.md`.
## Referensi

- [PRD — Dynamic AI Agent Virtual Office](../../PRD%20%E2%80%94%20Dynamic%20AI%20Agent%20Virtual%20Office.md), §13–16, §24, §26.
- [GLOSSARY.md](../../GLOSSARY.md): Agent Instance, Office Scene Version, Player Avatar / Presence, Visual Agent State, Activity Event.
- [Event System Design](2026-10-27-03-event-system.md)
- [Agent Registry Design](2026-10-27-01-agent-registry.md)
- Model asset source: [Ultimate Modular Men Pack — Poly Pizza](https://poly.pizza/bundle/Ultimate-Modular-Men-Pack-ZiH8muWqwQ), Quaternius, CC0; tujuh GLB di-host sendiri.
