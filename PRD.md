# PRD — Agent Office

**Platform orkestrasi agent AI dengan kantor 3D live untuk melihat pekerjaan nyata setiap agent**

| Metadata | Nilai |
| --- | --- |
| Versi | 1.0 |
| Tanggal | 26 Oktober 2026 — Asia/Jakarta |
| Status | Scope P0 dipersempit ke alur nyata: beberapa Agent Instance menjalankan Task terpisah pada satu workspace dengan satu endpoint, lalu status/aksi aman terlihat di kantor 3D dari event Engine/Runner. Belum diimplementasikan. Delegasi, provider breadth, dan skala pilot ditunda; batas isolasi, tool policy, secret/egress, dan freshness tetap wajib. |
| Platform awal | Aplikasi web privat, desktop sebagai pengalaman utama |
| Pengguna awal | Engineer yang mengoperasikan beberapa AI agent |
| Fokus | Mengetahui siapa mengerjakan apa, menemukan hambatan, memeriksa hasil, dan menambah agent dari system prompt; katalog Skill opsional P1 |

## 1. Ringkasan produk

Agent Office membantu pengguna memberi pekerjaan nyata kepada beberapa AI agent dan melihat aktivitas yang terverifikasi di kantor 3D live. Pengguna membuat Task, memilih Agent Instance, dan melihat Run, aksi tool yang diizinkan, hambatan, serta hasil. Kantor 3D menampilkan data kerja yang sama dengan daftar/detail; animasi tidak menentukan status.

Agent Definition adalah template reusable untuk system prompt dan role; integrasi Skill bersifat opsional P1. “Tambah agent” membuat Agent Instance persisten yang idle. Work dimulai hanya setelah Task/Run dimulai secara eksplisit.

Fokus pertama adalah satu alur utuh: pengguna memberi Task, Agent Office Execution Engine menjalankan Run nyata pada workspace terpilih memakai tools allowlist, Engine/Runner menghasilkan event aman, dan kantor 3D menampilkan statusnya. Beberapa Agent Instance dapat mengerjakan Task terpisah; child delegation dan integrasi banyak provider bukan syarat alur ini.

MVP fokus memerlukan Agent Office Execution Engine dan Runner yang dibatasi policy, satu konfigurasi Provider Adapter, event lifecycle minimum, dan visualisasi 3D event-backed. Tidak ada simulasi yang disebut pekerjaan nyata. Tool di dalam allowlist workspace tidak meminta approval per langkah; aksi destruktif atau di luar scope tetap dibatasi/di-approve.

## 2. Dasar kebutuhan, asumsi, dan tingkat keyakinan

Label keyakinan berikut menunjukkan kekuatan dasar informasi. Target di PRD merupakan usulan yang perlu diuji, bukan hasil pengukuran produk yang sudah tersedia.

| Dasar | Pernyataan | Status |
| --- | --- | --- |
| Permintaan pengguna | Kantor virtual dengan inspirasi Gather dan Kumospace, berisi AI agent | Terkonfirmasi — [High confidences], disebut langsung |
| Permintaan pengguna | Mengetahui pekerjaan agent dan menambah agent sesuai kebutuhan | Terkonfirmasi — [High confidences], disebut langsung |
| Materi pengguna | Memiliki system prompt, base agent Markdown, dan skill yang akan bertambah | Terkonfirmasi — [High confidences], contoh disertakan |
| Permintaan lanjutan pengguna | Kantor dapat dieksplor secara live seperti game simulasi/The Sims | Terkonfirmasi — [High confidences], disebut langsung pada revisi ini |
| Arah visual | Dunia 3D, karakter yang dapat dikendalikan, kamera diagonal/isometrik dan overview | Rekomendasi desain untuk memenuhi eksplorasi live; detail gaya aset tetap dapat disesuaikan |
| Batas MVP | Beberapa Agent Instance menjalankan Task terpisah melalui Agent Office Execution Engine; satu Provider Adapter/endpoint terkonfigurasi; event nyata menggerakkan visualisasi | Keputusan pengguna 26 Oktober 2026. Execution engine belum diimplementasikan; isolasi workspace, tool allowlist, secret/egress, dan status stale tetap gate minimum. |
| Cakupan provider | Satu endpoint OpenAI-compatible terkonfigurasi untuk alur awal; batas Provider Adapter tetap netral agar adapter lain dapat ditambahkan | Keputusan pengguna 26 Oktober 2026; kompatibilitas adapter/provider lain dan native API bukan gate visualisasi awal. |
| Batas execution | Agent Office memiliki execution loop, tools, policy, dan event lifecycle; tidak memakai coding-agent runtime tertentu sebagai dependency P0 | Probe OMP/Codex historis; tidak membuktikan Managed execution. |
| Sumber aktivitas | Agent Office Execution Engine/Runner menghasilkan lifecycle dan safe tool-action events; Provider Adapter hanya memasok inference | Persyaratan terkonfirmasi; belum diimplementasikan atau dibuktikan end-to-end. |
| Batas visual data | Agent Instance yang tersimpan boleh tampil sebagai identitas pada daftar/detail; avatar agent, lokasi, aktivitas, dan status kerja hanya berasal dari data scene/event kanonis. | Keputusan pengguna 26 Oktober 2026; tanpa data runtime, lokasi/status ditampilkan tidak tersedia dan tidak diganti fixtures. |

Dokumen ini tidak mengasumsikan bahwa seluruh agent dan skill pengguna sudah tersedia untuk diperiksa. Contoh `system-architect` menjadi bahan desain format impor dan perilaku produk.

## 3. Masalah yang diselesaikan

1. Aktivitas agent tersebar di terminal, sesi chat, log, dan repository sehingga pengguna sulit memperoleh gambaran tim.
2. Label “running” tidak menjelaskan apakah agent sedang membaca berkas, menjalankan tool, menunggu input, atau terhambat.
3. Prompt dan skill yang bertambah dapat menimbulkan konflik peran, ketergantungan yang hilang, serta perbedaan konfigurasi antar-run.
4. Tanpa status terverifikasi, pengguna tidak tahu agent mana sedang menjalankan Task, sedang memakai tool yang diizinkan, menunggu, atau sudah berhenti.
5. Animasi dapat terlihat aktif meski runtime terputus; kantor membutuhkan freshness dan bukti event.

**Pernyataan masalah:** ketika beberapa Agent Instance menjalankan pekerjaan terpisah, pengguna memerlukan satu tempat untuk melihat Task, status, dan aksi aman yang sedang terjadi tanpa memeriksa tiap sesi.

## 4. Tujuan dan batas produk

### 4.1 Tujuan

- Dalam satu tampilan, pengguna mengetahui agent aktif, task yang dikerjakan, dan hal yang membutuhkan tindakan.
- Pengguna menambahkan peran baru menggunakan prompt serta skill sendiri tanpa perubahan kode aplikasi.
- Setiap hasil dapat ditelusuri ke task, run, agent, versi konfigurasi, dan bukti yang tersedia.
- Pengguna dapat membedakan pekerjaan yang benar-benar sedang berjalan, pekerjaan yang menunggu, dan status yang belum dapat dipastikan.
- Pengguna dapat menjelajahi kantor secara live dengan karakter dan kamera yang responsif sambil memantau agent.
- Tampilan kantor membantu orientasi; daftar, pencarian, dan panel detail tetap efisien ketika jumlah agent bertambah.

### 4.2 Di luar MVP

- Video call, spatial audio, dan konferensi manusia seperti produk referensi.
- Simulasi kebutuhan karakter, ekonomi virtual, VR, atau editor bangunan/furnitur bebas. Eksplorasi kantor, gerak karakter, collision, dan interaksi objek termasuk MVP.
- Multiplayer manusia dengan sinkronisasi gerak, suara, atau dunia game persisten lintas banyak pemain.
- Agent yang otomatis membuat agent lain tanpa batas atau menaikkan hak aksesnya sendiri.
- Dukungan provider/runtime tidak otomatis dari nama atau protokol. Fokus P0 memakai satu endpoint terkonfigurasi; runtime execution, tool policy, egress, dan status tetap dimiliki Agent Office. Validasi adapter lain dan native API ditunda, bukan dianggap kompatibel.
- Integrasi GitHub, GitLab, dan VPS lengkap secara bersamaan; eksekusi deployment produksi otomatis.
- SaaS publik multi-tenant, billing pelanggan, marketplace skill, dan kolaborasi banyak operator.
- Jaminan kualitas kode hanya karena run selesai, atau penayangan reasoning internal tersembunyi model.

## 5. Referensi pengalaman

Referensi ditinjau melalui halaman resmi pada 21 Oktober 2026. Ini merupakan inspirasi interaksi, bukan klaim interoperabilitas atau rencana menyalin aset dan antarmuka.

| Referensi | Pola yang diamati | Adaptasi yang diusulkan |
| --- | --- | --- |
| [Gather — Virtual Office](https://www.gather.town/virtual-office) | Kantor dengan meja, area tim, ruang pertemuan, dan indikator ketersediaan | Agent memiliki tempat yang mudah dikenali; ruang proyek mengelompokkan pekerjaan dan anggota |
| [Kumospace — Navigating Kumospace](https://www.kumospace.com/navigating-kumospace) | Navigasi ruang/lantai, peta, zoom, dan interaksi berbasis lokasi | Pengguna menelusuri kantor, menemukan agent, dan membuka konteks proyek dari ruang |

**Keputusan desain:** kedekatan avatar tidak otomatis membagikan konteks, memberikan akses repository, atau memicu percakapan AI. Kolaborasi harus memiliki task, pesan/handoff, dan izin yang tercatat. Ruang pada peta merupakan pengelompokan visual; batas akses ditentukan oleh kebijakan proyek.

## 6. Pengguna dan pekerjaan utama

### 6.1 Persona utama

Engineer atau pemilik proyek yang menggunakan beberapa agent untuk perencanaan, implementasi, review, dan dokumentasi. Pengguna sudah mempunyai definisi agent dan ingin mempertahankan kendali atas konfigurasi, biaya, serta akses proyek.

### 6.2 Jobs to be done

| Ketika… | Saya ingin… | Supaya… |
| --- | --- | --- |
| Memulai sesi kerja | Melihat agent aktif dan task yang tertunda | Saya tahu perlu melanjutkan atau menindaklanjuti apa |
| Menjalankan beberapa pekerjaan | Melihat siapa mengerjakan apa dan siapa menunggu | Saya dapat mengatur pembagian kerja |
| Menambahkan spesialisasi | Mengimpor prompt dan menghubungkan skill | Saya dapat menggunakan agent sendiri tanpa mengubah aplikasi |
| Agent meminta bantuan | Melihat pertanyaan, alasan hambatan, dan konteks | Saya dapat merespons dengan tepat |
| Agent menyatakan selesai | Membuka hasil, perubahan, dan hasil pemeriksaan | Saya dapat menerima atau mengembalikan pekerjaan |
| Sub-agent terbentuk | Melihat parent, tugas, batas akses, dan hasilnya | Saya tetap memahami struktur pekerjaan dan biayanya |
| Ingin mengecek kantor secara langsung | Menggerakkan karakter, memasuki ruang, dan membuka aktivitas agent di mejanya | Saya dapat mengeksplor keadaan tim dalam dunia 3D yang tetap terhubung dengan pekerjaan nyata |

## 7. Konsep dan model domain

Pembedaan istilah Agent Office Execution Engine, Agent Definition/Instance, Activity Event, Visual Agent State, Model Provider, Model Endpoint, Provider Adapter, dan Runtime Adapter opsional dirangkum di [GLOSSARY.md](GLOSSARY.md).

| Konsep | Definisi dan aturan |
| --- | --- |
| Workspace | Ruang pengelolaan privat milik pengguna; memiliki proyek, konfigurasi, dan kebijakan |
| Project | Konteks pekerjaan beserta resource, instruksi, dan batas aksesnya |
| Agent Definition | Identitas peran, instruksi, referensi skill, dan konfigurasi yang dapat dipakai ulang; memiliki versi |
| Skill Version | Paket instruksi dan resource berversi; ketergantungan tool/runtime dicatat eksplisit |
| Agent Instance | Identitas persisten di kantor yang dibuat dari Agent Definition; dapat idle tanpa Task/Run aktif |
| Runner Connection | Koneksi terautentikasi antara control plane dan Runner yang menjalankan Agent Office Execution Engine; health koneksi tidak membuktikan sebuah Run aktif |
| Runtime Session | Konteks eksekusi milik Agent Office Execution Engine; terpisah dari identitas Agent Instance dan satu Task Run |
| Task | Pekerjaan dengan tujuan, input, acceptance criteria, pemilik, dan dependensi |
| Run / Attempt | Satu upaya agent mengerjakan task; menyimpan snapshot konfigurasi dan hasil. Retry membuat attempt baru |
| Delegation | Hubungan eksplisit parent Run → child Task/Run yang memerlukan approval pengguna sebelum child dimulai |
| Activity Event | Event kanonis dari Agent Office Execution Engine/Runner atau tindakan pengguna; memiliki sumber, waktu, dan korelasi; provider completion saja bukan event kerja |
| Artifact | Hasil seperti dokumen, diff, patch, laporan, atau tautan perubahan, terhubung ke run |
| Approval | Keputusan pengguna atas tindakan spesifik; scope dan masa berlakunya jelas |
| Office Layout / Scene Version | Penempatan ruang, meja, collider, jalur berjalan, spawn point, dan anchor interaksi; memiliki versi, tidak menentukan permission |
| Player Avatar / Presence | Karakter pengguna dan pose/kameranya; terpisah dari AI agent dan runtime state |
| Visual Agent State | Pose, animasi, dan tujuan gerak agent, diturunkan dari state pekerjaan terverifikasi dan scene; tidak boleh menulis status Run atau memberi permission |
| Agent Office Execution Engine | Loop eksekusi milik Agent Office yang menjalankan Task/Run, menerapkan tool policy, memanggil Provider Adapter, serta menghasilkan event kanonis |
| Runtime Adapter | Integrasi opsional ke execution runtime eksternal; bukan jalur MVP dan tidak mengubah Provider Adapter, Task, atau Run |
| Model Provider | Layanan cloud/lokal yang memasok inference; berbeda dari Agent Office Execution Engine |
| Model Endpoint | Tujuan inference terkonfigurasi, dengan base URL dan model/route ID; dapat menunjuk provider atau gateway |
| Provider Router / Gateway | Endpoint perantara yang meneruskan/memilih upstream; upstream dan biaya hanya diketahui jika dilaporkan |

```mermaid
flowchart TD
    S["Versi skill"] --> D["Definisi agent berversi"]
    D --> I["Instance agent"]
    I --> R["Run / attempt"]
    T["Task"] --> R
    R --> E["Event aktivitas"]
    R --> A["Artefak hasil"]
    R -. "Delegasi eksplisit" .-> C["Child task dan run"]
```

**Contoh:** satu definisi `senior-backend-architect` dapat dipakai oleh dua instance yang bekerja di proyek berbeda. Masing-masing memiliki session dan run sendiri. Dalam MVP, satu instance menjalankan maksimal satu run aktif; task berikutnya masuk antrean. Definisi yang tersimpan tidak menghabiskan kapasitas eksekusi.

## 8. Ruang lingkup dan prioritas

P0 wajib untuk rilis MVP, P1 merupakan pengembangan setelah MVP, dan P2 merupakan eksplorasi lanjutan. Fitur yang bergantung pada runtime harus lulus pengujian adapter; kontrol yang belum didukung tidak boleh terlihat seolah dapat digunakan.

| Area | P0 — MVP | P1 | P2 |
| --- | --- | --- | --- |
| Kantor | Dunia 3D dari template, karakter pengguna, WASD/click-to-move, collision, kamera isometrik/follow/overview, minimap, interaksi agent/objek, detail dan daftar | Penyunting layout terbatas, beberapa lantai, eksplorasi touch lebih lengkap | Dunia virtual penuh dan editor dekorasi |
| Definisi agent | P0: buat/impor/edit prompt Markdown, tambah Instance, dan bekukan versi prompt per Run | P1: clone/arsip, katalog dan versi Skill, serta pengelolaan lanjutan | P2: registry bersama |
| Skill | P0: tidak menjadi prasyarat Run; agent dapat bekerja dari system prompt dan tool allowlist | P1: impor/versi/attach skill, compatibility preflight, dan audit penggunaannya | P2: marketplace |
| Runtime | P0: Agent Office Execution Engine di Runner, satu Provider Adapter terkonfigurasi, tool allowlist, lifecycle dan event minimum | P1: adapter/provider tambahan dan advanced runtime controls |
| Task | P0: assign Task ke Instance, start Run, status, aksi aman, dan hasil | P1: child delegation, reusable workflow, dan routing otomatis |
| Sub-agent | Tidak termasuk alur P0; Instance mengerjakan Task terpisah | P1: parent-child Task/Run, approval per child, dan batas delegasi |
| Repository | P0: satu repository/workspace privat dengan workspace terisolasi per Run | P1: GitHub/GitLab connector dan PR/MR workflow |
| Kendali | P0: tools allowlist tanpa approval per langkah; approval untuk aksi destruktif/di luar scope; batas run dan secret/egress | P1: banyak operator, role, dan kebijakan lanjutan |

**Batas hasil rilis:** prototipe yang seluruh event-nya simulasi boleh menjadi milestone desain, tetapi tidak dihitung sebagai MVP operasional.

## 9. Alur pengguna utama

### 9.1 Menyiapkan kantor dan mengimpor agent

1. Pengguna membuka workspace privat dan membuat proyek.
2. Pengguna memilih workspace/repository yang diizinkan dan mengonfigurasi Provider Adapter/model endpoint yang tersedia.
3. Pengguna mengimpor `system-architect.md` atau menempelkan system prompt.
4. Aplikasi menampilkan nama, deskripsi, system prompt, dan metadata yang dipertahankan; referensi Skill dapat ditandai belum didukung tanpa menghalangi Run P0.
5. Pengguna menyimpan versi Agent Definition berbasis prompt, membuat Agent Instance, lalu memilih home desk/room. Instance tetap idle sampai Task/Run dimulai eksplisit.
6. Pengguna membuat Task dan memulai Run melalui Agent Office Execution Engine setelah preflight kebijakan.

### 9.2 Menjalankan pekerjaan

1. Pengguna membuat task, misalnya “Rancang fitur notifikasi email dan WhatsApp”.
2. Pengguna mengisi konteks proyek, input, batas pekerjaan, serta acceptance criteria.
3. Pengguna memilih Agent Instance dan versi system prompt; katalog Skill bukan prasyarat P0.
4. Preflight memeriksa workspace/tool allowlist, kapasitas, dan kebijakan. Dependensi Skill diperiksa hanya bila integrasi P1 diaktifkan.
5. Setelah Execution Engine menerima dan memulai Run, avatar menampilkan state dari event tercatat. Panel menunjukkan lifecycle dan safe tool events, bukan hidden reasoning.
6. Jika agent menunggu input atau approval, Run Detail menampilkan alasan dan tindakan yang dibutuhkan; Attention Inbox penuh adalah P1.
7. Setelah run selesai, hasil tersedia untuk review. Pengguna menerima hasil atau membuat revisi yang terhubung ke hasil sebelumnya.

### 9.3 Delegasi dan kolaborasi

Parent dapat mengusulkan delegasi kepada Agent Definition/version yang terdaftar dan diizinkan. Sistem membuat child Task/Run hanya setelah pengguna menyetujui definition/version, tujuan, project scope, input, dan budget. Child disimpan dalam parentage/history dan tidak otomatis menjadi Agent Instance permanen.

Sebagai contoh workflow, architect menghasilkan desain; task backend dan frontend dimulai setelah dependensi desain terpenuhi; reviewer memeriksa hasil setelah kedua pekerjaan tersedia. Workflow ini merupakan contoh konfigurasi, bukan urutan wajib atau perilaku yang otomatis disebabkan oleh nama peran.

Jika child gagal, parent menerima status kegagalan dan referensi error. Parent tidak otomatis dinyatakan sukses. Child yang berjalan tanpa event parentage eksplisit ditampilkan sebagai run independen sampai hubungan terverifikasi.

### 9.4 Mengatasi status yang tidak pasti

Saat heartbeat terhenti, kantor mempertahankan status terakhir dengan label waktu dan peringatan koneksi. Pengguna dapat membuka detail untuk melihat event terakhir. Setelah tersambung kembali, aplikasi melakukan replay/rekonsiliasi. Retry tidak ditawarkan sebagai tindakan aman sebelum status run lama dapat dipastikan, karena run tersebut mungkin masih menulis perubahan.

### 9.5 Mengeksplor kantor secara live

1. Pengguna memilih **Masuk kantor** dan karakter muncul di spawn point yang valid.
2. Pengguna berjalan dengan WASD/tombol panah atau klik lokasi lantai; kamera mengikuti dari sudut diagonal.
3. Pengguna membuka minimap, mengganti kamera overview, atau memilih “fokus ke agent” untuk menemukan ruang tujuan.
4. Saat mendekati agent, meja, atau papan proyek, target interaksi diberi highlight. Klik atau tombol E membuka konteksnya.
5. Agent menampilkan task dan status nyata sambil pengguna bergerak. Input kamera/karakter tidak menunggu round trip server.
6. Dari panel agent, pengguna melihat hasil atau mengirim instruksi melalui alur task biasa. Berjalan mendekati agent tidak mengirim pesan model secara otomatis.
7. Jika koneksi putus, pengguna tetap dapat mengeksplor scene yang telah dimuat dengan indikator offline. Tindakan pekerjaan dinonaktifkan sampai koneksi dan status kembali terverifikasi.

## 10. Persyaratan fungsional

### FR-01 — Agent Definition dan Agent Instance · P0

- Pengguna dapat membuat, mengimpor, dan mengubah Agent Definition berbasis system prompt, lalu menambahkan Agent Instance idle.
- ID stabil, slug unik, dan peran bebas tanpa enum/perubahan kode aplikasi.
- Slug yang bertabrakan ditolak; definisi lama tidak ditimpa diam-diam.
- Membuat definisi atau Instance tidak otomatis memulai Run atau membelanjakan token.
- Kapasitas tersimpan dan Instance yang sedang menjalankan Run ditampilkan terpisah; batas eksekusi dapat dikonfigurasi.
- Clone, archive, dan alur historis definisi lengkap adalah P1; pengarsipan tidak menghentikan Run aktif.

### FR-02 — Impor prompt dan base agent · P0

- Mendukung teks Markdown bebas serta Markdown dengan YAML front matter seperti contoh `name` dan `description` pengguna.
- Front matter diparsing sebagai data tanpa menjalankan kode. Ukuran, tipe, encoding, path resource, dan field yang diwajibkan divalidasi.
- Markdown tanpa nama memunculkan formulir metadata; tidak ditolak hanya karena tidak mengikuti template aplikasi.
- Isi asli disimpan tanpa perubahan otomatis. Metadata tambahan disimpan terpisah atau dalam ekspor yang diberi label format aplikasi.
- Field asing tetap dipertahankan sebagai metadata yang belum dipetakan; aplikasi menjelaskan bila runtime mengabaikannya.
- Importer tidak mengikuti perintah yang tertulis dalam file sebagai instruksi untuk melakukan aksi saat impor.

### FR-03 — Pengelolaan skill · P1

- Skill dapat diimpor, diberi versi, diarsipkan, dan ditautkan ke definisi agent setelah alur visualisasi P0.
- Metadata minimum: ID, nama, deskripsi, instruksi, versi, resource, dan dependensi yang dinyatakan.
- P1 preflight menjelaskan dependensi/tool yang tidak kompatibel; skill yang membutuhkan kemampuan tak tersedia tidak boleh dijalankan.
- Menyimpan atau melampirkan skill tidak memberikan hak baru ke file, jaringan, secret, atau tool.
- Skrip dari paket skill tidak dijalankan hanya karena diunggah. Run P0 menggunakan system prompt, Task, workspace, dan tool allowlist.

### FR-04 — Versi dan konfigurasi efektif · P0

- Perubahan system prompt menghasilkan versi definisi baru; Run membekukan versi yang dipilih saat start.
- Run P0 memakai system prompt dan Task yang terpilih. Skill attachment, inventory instruksi efektif, konflik lanjutan, dan export/restore skill merupakan P1.
- Kebijakan aplikasi membatasi instruksi system prompt dan Task. Prompt tidak memberikan izin tool atau egress.

### FR-05 — Agent Office Execution Engine dan mode operasional · P0

Alur P0 menggunakan mode Managed yang dimiliki Agent Office. Integrasi Observe ke runtime eksternal bukan syarat alur fokus dan ditunda.

| Mode | Perilaku |
| --- | --- |
| Managed | Agent Office Execution Engine menjalankan Task/Run, tool loop terbatas policy, dan event lifecycle; Provider Adapter hanya memasok inference |
| Observe | Integrasi Runtime Adapter eksternal, bila kelak diprioritaskan, bukan bagian alur P0 |

- Engine berjalan di Runner terisolasi dan menyediakan start, event lifecycle, safe tool-action summary, terminal result, health/freshness, serta cancel request/acknowledgement.
- Tool yang cocok dengan allowlist workspace dapat berjalan tanpa approval per pemanggilan. Aksi destruktif, akses resource di luar scope, atau egress baru tidak diwarisi dari prompt dan tetap memerlukan policy/approval.
- Provider response tidak memulai Run, menetapkan status kerja, atau mengotorisasi tool. Run menjadi Running setelah Engine mengonfirmasi start.
- Hanya capability yang diuji yang diaktifkan. Pause/resume, child delegation, banyak provider, dan detail token bukan kemampuan P0.
- Runner terdaftar dan diautentikasi eksplisit; credential tidak dikirim ke browser, prompt, atau log umum.

### FR-06 — Kantor virtual dan navigasi · P0

- Kantor merupakan dunia 3D yang dirender dan dapat dijelajahi secara live, dengan kamera diagonal/isometrik sebagai default. Karakter pengguna berbeda dari avatar agent. Detail artistik diputuskan setelah uji keterbacaan.
- Desktop menyediakan pan, zoom, pencarian agent, filter proyek/status/peran, dan tindakan “fokus ke agent”.
- Klik avatar membuka detail. Pengguna tidak harus berjalan sebagai karakter untuk mengakses informasi.
- Ruang dan meja dapat diberi nama serta dipetakan ke proyek/tim. MVP memakai template dan penempatan sederhana.
- Avatar menampilkan Agent Instance, Task/Run, safe action terkini, dan freshness; sedikitnya dua Instance dengan Task terpisah dapat terlihat bersamaan.
- Animasi mengikuti event kanonis Engine/Runner. Avatar berawal di home desk dan berpindah ke anchor Task hanya setelah assignment/start terverifikasi. Saat data stale, animasi kerja berhenti dan posisi terakhir diberi label stale; karakter pengguna tetap dapat berjalan.
- Jika kantor penuh, agent tetap tersedia melalui daftar, pengelompokan, dan area tambahan dari template; kapasitas tidak bergantung pada jumlah meja hardcoded.
- Perubahan lokasi tidak memicu task, tidak membagikan memori, dan tidak mengubah akses proyek.

### FR-07 — Tampilan alternatif dan aksesibilitas · P0

- Explore Mode, Overview Mode, Agent List, Task Board, dan Run Detail memakai data pekerjaan kanonis yang sama. Posisi visual bukan sumber kebenaran status pekerjaan.
- Task Board mencakup Backlog, Ready, In progress, Blocked/Waiting, In review, dan Accepted; status run diperlihatkan terpisah.
- Tablet mendukung inspeksi dan kontrol inti; mobile memprioritaskan daftar dan detail Agent/Task/Run. Attention Inbox penuh adalah P1.
- Keyboard dapat membuka daftar, memilih agent/task, dan menjalankan tindakan tanpa mengoperasikan kanvas.
- Mendukung reduced motion, fokus yang terlihat, label pembaca layar pada kontrol HTML, serta fallback daftar ketika WebGL tidak tersedia.

### FR-08 — Identitas status dan bukti aktivitas · P0

- Status eksekusi, connection health, dan task acceptance disimpan terpisah.
- Activity Event membedakan Engine/Runner events, tindakan pengguna, dan ringkasan aplikasi/AI. Event mencatat sumber/waktu; safe tool events hanya menampilkan informasi yang diizinkan policy. Ringkasan menunjuk ke event pendukung; tanpa bukti, UI menyatakan aktivitas rinci tidak tersedia.
- Progress/ETA tidak ditampilkan tanpa dasar terukur. Hidden reasoning, secret, prompt internal, dan konten di luar scope tidak ditampilkan sebagai aktivitas.
- Status `generating response` hanya ditampilkan jika Engine mencatat inference call aktif; respons Provider Adapter sendiri tidak menetapkan Run state atau memberi izin tool.

### FR-09 — Penugasan dan antrean · P0

- Task memuat judul, tujuan, proyek, input/resource, acceptance criteria, agent yang dituju, dan batas eksekusi.
- Pengguna dapat menentukan task blocked by task lain. Siklus dependensi ditolak.
- Start memiliki request ID yang idempotent; klik ganda tidak membuat dua run.
- Task menunggu kapasitas ditampilkan sebagai queued beserta alasan. Task tidak dianggap running sebelum Agent Office Execution Engine mengakui dan memulai Run.
- MVP menjalankan satu run aktif per instance dan menggunakan antrean sederhana. Kebijakan antrean default FIFO; prioritas manual merupakan P1.
- Edit task setelah start berlaku pada task revision berikutnya; instruksi tambahan ke run aktif dicatat sebagai pesan terpisah dengan acknowledgement.

### FR-10 — Sub-agent dan handoff · P1

- Child delegation tidak termasuk alur P0; Agent Instance menjalankan Task yang ditugaskan secara independen.
- Jika diaktifkan pada P1, child mengacu pada Agent Definition/version, Task, input, project scope, parent Run, dan budget; pengguna menyetujui tiap child sebelum Task/Run dibuat.
- Child tunduk pada batas concurrency/depth/duration dan izin minimum; akses/secret parent tidak diwariskan otomatis.
- Child Run tidak menjadi Agent Instance permanen secara otomatis. Parent-child relation berasal dari record/event, bukan teks prompt atau kedekatan avatar.

### FR-11 — Input, kontrol, dan approval · P0

- Detail Run menampilkan status, pertanyaan yang menunggu jawaban, serta tindakan yang memerlukan approval berikut scope dan dampaknya.
- Tools dalam allowlist yang sudah disetujui untuk workspace/Run dapat berjalan tanpa approval per langkah.
- Aksi destruktif, resource di luar scope, atau egress yang belum disetujui tetap dibatasi; approval terikat pada action, parameter, dan resource. Perubahan payload yang material memerlukan approval baru.
- Prompt, skill attachment, dan keputusan Task-DAG pihak lain tidak memberi izin tool. Policy ditegakkan oleh Agent Office.
- Penolakan dan kedaluwarsa approval tercatat. Cancel tetap “cancel requested” sampai Engine mengonfirmasi; status yang tidak pasti tidak ditampilkan sebagai canceled.
- Retry membuat Run baru hanya setelah status attempt sebelumnya diketahui; side effect tidak diulang otomatis.

### FR-12 — Detail run, timeline, dan hasil · P0

Detail Run menampilkan identitas agent, Task/proyek, Runtime Session, versi system prompt, waktu mulai/akhir, safe activity terakhir, freshness, safe tool events, artefak, dan hasil pemeriksaan. Skill/version metadata tampil bila integrasi P1 kelak diaktifkan.

- Safe activity summary dan tautan hasil tersedia di detail Run P0; filter timeline, raw output, dan pencarian histori merupakan P1.
- Prompt sensitif, secret, dan isi berkas di luar scope tidak dimasukkan ke log publik atau panel umum.
- Menampilkan teks respons yang memang disediakan runtime dan ringkasan tindakan; tidak meminta atau menyimpan hidden chain-of-thought.
- Artefak menyimpan asal, waktu, versi/checksum bila relevan, dan hubungan ke run. Link yang kedaluwarsa atau artefak terhapus diberi status jelas.
- `Run succeeded` berarti runtime melaporkan eksekusi selesai; task baru `Accepted` setelah memenuhi kebijakan review, secara default oleh pengguna.
- Bukti test harus menyertakan check/command, exit status, waktu, dan log/report jika tersedia. Pernyataan agent bahwa test lulus tanpa bukti diberi label belum diverifikasi.

### FR-13 — Keterhubungan proyek dan repository · P0

- MVP mendukung satu sumber repository/workspace melalui runner yang dikonfigurasi. Connector GitHub/GitLab langsung merupakan P1.
- Instance yang bekerja paralel menggunakan workspace/worktree atau mekanisme isolasi setara. Dua run tidak menulis checkout yang sama tanpa kebijakan serialisasi eksplisit.
- UI menampilkan repository, branch/workspace, dan revisi input/output bila tersedia; metadata yang tidak diketahui tidak ditebak.
- Membaca/menulis lokal mengikuti policy proyek. Push, PR/MR, merge, dan deployment hanya dilakukan bila fitur tersedia serta diizinkan policy/approval yang berlaku.
- Konflik perubahan menjadi status blocked dengan file/artefak terkait; tidak diselesaikan dengan overwrite diam-diam.

### FR-14 — Attention Inbox dan notifikasi · P1

- Mengumpulkan waiting input, approval, blocked, failed, koneksi stale, dan pelampauan batas pada skala banyak proyek/agent.
- Item memiliki Task/Run sumber dan tindakan relevan; duplikat dikonsolidasikan dan resolved berdasarkan event/keputusan.
- Notifikasi in-app/email/messaging memerlukan prioritas terpisah; detail Run tetap menjadi jalur P0.

### FR-15 — Kuota dan visibilitas penggunaan · P0

- P0 menegakkan batas concurrency per Instance, durasi, inference turn/token, dan tool call per Run sebelum start.
- Kapasitas skala 50 Instance/10 Run aktif, batas child/depth, dan agregasi penggunaan parent-child merupakan target P1/pilot, bukan gate visualisasi P0.
- Token/biaya hanya ditampilkan jika tersedia; upstream/biaya yang tidak dilaporkan tampil sebagai tidak diketahui, bukan nol/gratis.
- Biaya estimasi dibedakan dari biaya aktual; hard spending cap tidak dijanjikan kecuali ditegakkan provider/router.

### FR-16 — Penyimpanan, audit, dan portabilitas · P0

- Definition, Task, Run, status aktivitas terbaru, keputusan policy/approval, dan artefak P0 bertahan setelah refresh/restart.
- P0 merekam sumber/waktu event yang diperlukan untuk menjelaskan status Run; payload sensitif tidak disalin ke log umum.
- Full audit history, configurable retention, bulk export, dan extended deletion workflows merupakan P1; credential/secret tidak pernah masuk export.

### FR-17 — Eksplorasi live seperti game simulasi · P0

- Tersedia **Explore Mode** dengan karakter pengguna, serta **Overview Mode** untuk melihat kantor dari atas. Perpindahan mode tidak memulai ulang session agent.
- Karakter dapat berjalan menggunakan WASD/tombol panah dan click-to-move. Input manual membatalkan rute klik yang sedang aktif.
- Click-to-move mencari jalur di area yang boleh dilalui. Dinding, meja, dan furnitur solid memiliki collider; karakter tidak menembusnya. Tujuan yang tidak terjangkau menampilkan alasan dan tidak membuat karakter berjalan tanpa akhir.
- Kamera mendukung follow, rotate, pan, zoom yang dibatasi, reset view, dan fokus ke agent. Roof/dinding yang menghalangi dapat disembunyikan secara visual tanpa menghilangkan collision.
- Minimap menampilkan ruang, posisi pengguna, agent, dan indikator perhatian. Memilih target dapat memfokuskan kamera atau menentukan tujuan berjalan; tindakan tersebut tidak mengubah task.
- Klik/tombol E pada target membuka panel: agent → aktivitas/task; meja → agent yang ditugaskan; papan proyek → task board; pintu → membuka akses visual ke ruang bila scene mengizinkannya.
- Interaksi berjalan dan duduk bersifat visual. Aksi seperti assign, cancel, approval, dan menjalankan task tetap memerlukan tombol/command eksplisit dengan policy yang sama seperti tampilan daftar.
- Saat input teks, dialog, atau task panel memiliki fokus, tombol WASD/E tidak menggerakkan karakter atau mengaktifkan objek di belakangnya. Escape menutup konteks teratas; pointer/camera tidak merebut input formulir.
- Posisi/kamera pengguna disimpan secara berkala atau ketika berhenti bergerak. Saat kembali, posisi divalidasi terhadap scene terbaru; bila tidak valid, gunakan spawn point aman.
- Desktop menjadi target utama eksplorasi penuh. Tablet menyediakan tap-to-move dan pan/pinch pada kemampuan yang diuji; mobile tetap menyediakan daftar/detail sebagai jalur inti.
- Menutup panel atau berpindah ruang tidak menghentikan agent. Kontrol kecepatan visual atau reduced motion hanya memengaruhi tampilan, tidak mempercepat atau mem-pause model.

### FR-18 — Pemisahan dunia visual dan pekerjaan realtime · P0

- Pisahkan tiga sumber state: layout kantor persisten, pose/animasi yang bersifat sementara, dan state pekerjaan agent yang persisten.
- Scene berjalan dengan loop rendering lokal; pembaruan status kerja diterima melalui event server. Setiap frame gerak tidak menjadi event audit, panggilan API, atau pemanggilan model.
- Transisi animasi memakai state machine presentasi: idle, walk, work, attention, dan stale. State machine ini tidak boleh menetapkan run succeeded/failed atau memberi permission.
- Gerakan agent ke meja/ruang memvisualisasikan assignment/start yang terverifikasi. Child handoff tidak termasuk alur P0; waktu perjalanan tidak menunda atau mengubah status Task/Run.
- Jika layout berubah, client memuat scene version baru dan menghitung ulang jalur. Referensi meja/ruang yang hilang dipetakan ke area default dengan pemberitahuan.
- Reconnect mengambil snapshot beserta cursor sebelum melanjutkan delta event. Panel, badge, task board, dan animasi diselaraskan pada revisi data yang sama.
- Ketika browser berada di background, rendering dikurangi/dihentikan sesuai kemampuan platform; eksekusi agent di runner tetap berjalan. Saat kembali, lakukan pengecekan freshness dan rekonsiliasi.
- Jika aset 3D atau physics gagal dimuat, aplikasi tetap menyediakan daftar, status, dan kontrol yang valid. Kegagalan scene tidak dinyatakan sebagai kegagalan agent.

### FR-19 — Koneksi model provider yang dapat dikonfigurasi · P0

- Alur P0 memakai satu endpoint OpenAI-compatible Chat Completions yang dipilih pengguna melalui Provider Adapter; adapter/provider/model/route disimpan sebagai konfigurasi non-secret.
Provider Adapter menerjemahkan inference request/response; ia tidak mengelola Task, tool execution, approval, lifecycle, atau event kerja.
- Kombinasi adapter/provider/model/fitur harus diuji sebelum dipakai. Alur awal tidak mengklaim dukungan universal atau native API.
- Batas request/token/turn per Run ditetapkan sebelum inference; biaya/upstream yang tidak dilaporkan ditampilkan sebagai tidak diketahui, bukan nol atau gratis.
- Credential dikelola di Runner/secret store; tidak masuk browser, prompt, log umum, atau export.
- Runner hanya mengizinkan egress ke endpoint terkonfigurasi. Custom URL tidak memberi izin ke host/internal service lain.


## 11. Status dan perilaku visual

### 11.1 Status eksekusi run

| Status | Makna | Tampilan |
| --- | --- | --- |
| Queued | Task diterima aplikasi, menunggu kapasitas/dependensi | Badge antrean dan alasan |
| Starting | Start dikirim, menunggu konfirmasi awal runtime | Indikator memulai; belum mengklaim bekerja |
| Running | Runtime mengonfirmasi run aktif | Avatar bekerja, ringkasan aktivitas berbukti |
| Waiting input | Runtime membutuhkan jawaban pengguna | Ikon pertanyaan dan Attention Inbox |
| Waiting approval | Aksi tertentu menunggu keputusan | Ikon approval dan rincian scope |
| Waiting child | Digunakan hanya bila child delegation P1 diaktifkan | Indikator child yang masih aktif |
| Blocked | Ada hambatan yang diketahui | Alasan dan tindakan pemulihan |
| Cancel requested | Permintaan berhenti belum dikonfirmasi | Label menunggu penghentian |
| Succeeded | Runtime melaporkan selesai | Link hasil, task default masuk In review |
| Failed | Runtime melaporkan gagal | Error, bukti, opsi retry yang valid |
| Canceled | Penghentian sudah dikonfirmasi | Hasil parsial dan side effect tercatat |

Status `Paused` hanya tersedia untuk adapter dengan semantik pause/resume yang telah diuji. Instance tanpa run aktif dapat berstatus idle jika sambungan sehat. Instance belum dihubungkan diberi label “belum terhubung”.

### 11.2 Kesehatan koneksi

Kesehatan koneksi adalah dimensi terpisah: connected, reconnecting, stale, atau disconnected. Contoh tampilan: **“Status terakhir: Running · data 48 detik lalu · koneksi stale.”** Kehilangan koneksi tidak mengubah run menjadi failed, idle, atau succeeded.

Target heartbeat awal: setiap 10 detik; stale setelah 30 detik tanpa sinyal yang valid. Nilai ini dapat disesuaikan terhadap runtime dan diuji pada adapter. Health transport saja tidak cukup membuktikan session tertentu masih berjalan.

Terminal state tidak kembali menjadi running karena event lama. Retry menggunakan run ID baru. Koreksi setelah rekonsiliasi dilakukan sebagai pembaruan beralasan yang dapat diaudit, bukan overwrite tersembunyi.

## 12. Arsitektur dan teknologi yang direkomendasikan

**Rekomendasi:** aplikasi web dengan renderer 3D di browser, backend modular monolith, PostgreSQL sebagai sumber data utama, dan Runner terpisah. Gunakan Next.js + React Three Fiber/Three.js untuk kantor dan FastAPI untuk API/event. Runner meng-host Agent Office Execution Engine; Provider Adapter terpisah memanggil model endpoint. Runtime Adapter eksternal bukan dependency MVP.

**[Medium Confidence]** untuk kecocokan keseluruhan arsitektur: didasarkan pada kebutuhan satu owner, 50 agent terdaftar/10 run aktif, dan stack Next.js/Python yang sudah dikenal pengguna. Performa serta operasi end-to-end belum diuji. Fakta kemampuan library pada tabel didukung dokumentasi primer; pemilihan komponennya merupakan rekomendasi desain.

### 12.1 Stack utama dan alasan pemilihan

| Lapisan | Rekomendasi | Penggunaan dan alasan |
| --- | --- | --- |
| Web application | Next.js App Router + React + TypeScript | Halaman kantor, daftar, konfigurasi, task dan hasil dalam satu aplikasi; cocok dengan stack pengguna. Modul 3D dimuat sebagai client component [R4] |
| Renderer 3D | Three.js melalui React Three Fiber | Scene, mesh, lighting, kamera, picking, dan animation loop dalam ekosistem React [R5] |
| Bantuan scene | @react-three/drei | Helper kamera, input keyboard, dan komponen scene; tetap perlu logika kontrol aplikasi [R6] |
| Gerak dan collision | Rapier melalui @react-three/rapier | Collider sederhana dan kinematic character controller; menghindari karakter menembus furnitur [R7–R8] |
| Pathfinding | Grid walkable + A* untuk satu lantai MVP | Cukup untuk kantor dari template; Rapier menangani collision, bukan mencari rute. Navmesh dipertimbangkan ketika layout bertingkat/kompleks |
| UI overlay | Komponen React/HTML + shadcn/ui | Panel task, formulir, menu interaksi, Inbox dan shortcut tetap terbaca serta dapat diakses di luar canvas [R9] |
| State web | TanStack Query + TanStack Store | Query untuk data server; Store untuk pilihan agent, mode kamera dan panel. Transform tiap frame disimpan pada object/ref scene, bukan query cache [R10–R11] |
| Backend | FastAPI + Pydantic | REST API, validasi, policy, event ingestion dan WebSocket; satu codebase modular [R12] |
| Data dan migrasi | PostgreSQL + SQLModel (SQLAlchemy async) + Alembic | CRUD dan pemetaan schema standar menggunakan SQLModel/SQLAlchemy async; Alembic mengelola perubahan schema berversi. Raw SQL tetap didukung untuk operasi khusus [R13–R15] |
| Realtime browser | WebSocket native melalui FastAPI | Snapshot/delta status dan notifikasi; REST untuk command penting dengan request ID dan hasil yang dapat di-query |
| Scheduler/dispatcher | Worker Python terpisah + tabel command/outbox PostgreSQL | Antrean awal yang durable; satu sumber transaksi tanpa broker tambahan. Lease, retry dan deduplikasi harus diimplementasikan serta diuji |
| Runner | Service pada host eksekusi | Menyiapkan isolated workspace, meng-host Agent Office Execution Engine, menegakkan policy/resource/egress, mengawasi proses, dan menyimpan event hingga diakui |
| Agent Office Execution Engine | Loop execution milik produk di Runner | Menjalankan Task/Run dan tool loop, meminta inference melalui Provider Adapter, menegakkan approval/policy, dan menghasilkan event lifecycle |
| Runtime Adapter | Extension boundary opsional untuk runtime eksternal setelah MVP | Bukan target produk atau syarat MVP; tidak menggantikan Engine atau Provider Adapter |
| Model provider | Provider Adapter dan koneksi endpoint yang dikonfigurasi | Phase 1 memprioritaskan subset OpenAI-compatible Chat Completions; provider/model/route, endpoint, credential reference, dan kemampuan diverifikasi per kombinasi |
| Artefak | Volume berkas privat pada pilot, melalui abstraction Artifact Store | Diff, log besar, dan dokumen di luar kolom event. Adapter S3-compatible dapat memakai layanan yang sudah ada setelah kompatibilitas diuji |
| Deployment | Docker Compose + reverse proxy Nginx dengan HTTPS | Layanan aplikasi, database, worker, dan volume dapat dikelola pada satu Linux host privat; runner dapat ditempatkan di host lain [R18] |
| Operasional | Structured logs + correlation ID serta ekspor metrik | Ukur lag event, error, antrean, reconnect, FPS dan waktu kontrol secara terpisah; backend tidak menyimpan trace reasoning tersembunyi |

Jangan menyalin nomor versi dari proyek lama tanpa memeriksa pasangan kompatibel. Dokumentasi R3F yang ditinjau memasangkan R3F 9 dengan React 19 dan R3F 8 dengan React 18; jalur alpha bukan baseline MVP. Next.js, React, R3F, Drei, Rapier, dan OMP dipin dalam lockfile/image setelah spike. [R5]

### 12.2 Topologi sistem

```mermaid
flowchart TD
    B["Browser: kantor 3D dan panel kerja"] -->|"HTTPS: task dan query"| A["FastAPI: API dan event gateway"]
    A -->|"WSS: status dan snapshot"| B
    A --> P[("PostgreSQL: task, run, event, command")]
    A --> F["Artifact Store privat"]
    P -->|"Claim command"| W["Dispatcher Python"]
    W -->|"Dispatch melalui gateway"| A
    R["Runner pada host eksekusi"] <-->|"WSS: command dan event"| A
    R --> X["Proses OMP terisolasi"]
    X --> G["Workspace proyek per run"]
    X --> M["Provider model dan tool yang diizinkan"]
```

Garis runner–gateway adalah koneksi dua arah yang dibuka oleh runner. Browser tidak terhubung langsung ke CLI atau shell, dan database tidak diakses langsung oleh proses agent. Reverse proxy mengarahkan route web ke Next.js serta route API/WebSocket ke FastAPI.

Backend tetap modular dalam satu codebase. Dispatcher dan runner merupakan proses terpisah karena siklus hidup pekerjaan panjang dan kebutuhan isolasinya, bukan karena produk memerlukan banyak microservices.

### 12.3 Tiga jenis state yang harus dipisahkan

| State | Pemilik dan penyimpanan | Cara update | Contoh |
| --- | --- | --- | --- |
| Dunia kantor | Server; scene/layout berversi di PostgreSQL, aset di file/object store | Snapshot saat masuk, reload/delta ketika layout berubah | Room, meja, collider, nav grid, titik interaksi |
| Gerak/presentasi | Client untuk MVP satu owner; checkpoint posisi pengguna disimpan terpisah | Loop lokal dan interpolasi; checkpoint berkala | Posisi karakter, camera, crossfade walk/idle, highlight |
| Pekerjaan AI | Runtime sebagai sumber observasi; backend memvalidasi dan menyimpan state/event | Event dan snapshot ber-cursor melalui WebSocket | Run aktif, tool call, waiting approval, artefak, hasil |

**Konsekuensi:** jalur jalan, FPS, dan posisi karakter tidak bergantung pada waktu respons model. Sebaliknya, animasi tidak boleh menetapkan status kerja. Local exploration dapat berlanjut saat offline dengan badge yang jelas; command task tidak diantrekan diam-diam dari client offline.

MVP tidak membutuhkan authoritative multiplayer game server karena hanya satu manusia menjelajah. Jika banyak manusia kelak berbagi ruang, tambahkan presence/room service dengan input validation, snapshot pose, interpolasi, dan batas frekuensi. State task tetap melalui API/policy yang sama.

### 12.4 Struktur frontend dan dunia 3D

| Modul | Tanggung jawab |
| --- | --- |
| OfficeScene | Memuat scene version, geometri, lighting dan kualitas render |
| PlayerController | WASD/touch/click-to-move, nav grid dan collision |
| CameraController | Isometric follow, orbit terbatas, overview, zoom dan reset |
| InteractionSystem | Raycast/proximity, target highlight dan menu konteks |
| AgentPresenter | Memetakan status server ke badge/animasi; tidak menjalankan tool |
| RealtimeClient | Snapshot, cursor, deduplikasi event, reconnect dan freshness |
| WorkPanels | Agent detail, task board, Inbox, hasil dan konfigurasi |

Canvas dimuat secara lazy pada client agar daftar dan kontrol inti dapat digunakan lebih dahulu. Next.js mendokumentasikan penggunaan dynamic import dengan SSR dinonaktifkan dari client component. [R4]

Rapier digunakan untuk capsule/primitive collider karakter dan static collider dinding/furnitur. A* pada grid menentukan rute; controller mengikuti rute sambil memeriksa collision. Karakter tidak memerlukan simulasi rigid-body realistis. Hindari dynamic physics pada seluruh meja dan dekorasi.

Posisi/rotasi diperbarui melalui frame loop dan ref/object Three.js. Hindari React state update untuk setiap frame; gunakan kembali geometry/material dan instancing untuk objek statis berulang. Ini sejalan dengan panduan performa R3F. [R19]

Aset kantor dan karakter menggunakan glTF/GLB dengan animasi dasar idle/walk/work, skeleton yang konsisten, dan collider terpisah dari mesh visual. GLTFLoader mendukung format glTF serta beberapa ekstensi kompresi; pilih kompresi hanya setelah menguji ukuran, decoder, dan waktu decode. [R20] Batasi dynamic lights/shadow, pakai kualitas render adaptif, dan muat variasi aset secara bertahap. Minimap dibuat dari layout, bukan render 3D penuh kedua jika tidak diperlukan.

### 12.5 Backend, data dan kontrak aplikasi

Modul backend yang disarankan: identity/access, projects, agent registry, skills/configuration, tasks/runs, approvals, runtime connections, event ingestion, office layouts, artifacts, dan audit.

| Kelompok data | Entitas/tabel awal yang direkomendasikan |
| --- | --- |
| Konfigurasi | agent_definitions, agent_definition_versions, skills, skill_versions, definition_skill_links |
| Pekerjaan | tasks, task_revisions, runs, delegations, approvals |
| Runtime | runtime_connections, runtime_sessions, agent_instances |
| Bukti dan kendali | activity_events, commands, outbox, artifacts, audit_entries |
| Kantor | office_scenes, scene_versions, rooms, desk_assignments, user_view_preferences |

Gunakan kolom relasional untuk identitas, scope, status, revision, parentage dan constraint; JSON/JSONB untuk payload event/layout yang skemanya bervariasi dengan schema_version. Tambahkan indeks query berdasarkan project, run dan waktu; unique constraint untuk event ID serta request ID sesuai scope. Skema ini merupakan rancangan awal yang perlu dinormalisasi dalam Design Doc.

Command penting menggunakan HTTP POST dengan idempotency key; response mengembalikan command ID dan status accepted/rejected. Client dapat meng-query command tersebut ketika koneksi event terputus. Kontrol start/cancel/approve tidak dianggap sukses hanya karena WebSocket mengirim frame.

Untuk pilot, satu proses gateway mengelola koneksi aktif; registry koneksi in-memory bukan penyimpanan run. Dokumentasi FastAPI mengingatkan bahwa contoh connection manager in-memory hanya berlaku pada proses hidup tunggal. Karena itu event/state persisten dan replay tetap wajib. [R12]

### 12.6 Scheduler, runner dan pemulihan

1. API memvalidasi scope dan kapasitas, lalu menyimpan task/run intent serta command/outbox dalam satu transaksi.
2. Dispatcher mengambil command siap jalan dengan lock singkat; PostgreSQL mendokumentasikan SKIP LOCKED untuk pola tabel antrean. Lock dilepas setelah pencatatan lease, tidak ditahan selama model bekerja. [R13]
3. Dispatcher mengirim command ke Runner yang terdaftar; identitas Runner, request ID, Run ID, lease, dan acknowledgement dicatat.
4. Runner menyiapkan workspace/snapshot, lalu Agent Office Execution Engine menjalankan Run, memanggil Provider Adapter untuk inference, dan mengeksekusi hanya tool yang diizinkan. Engine/Runner mencatat event kerja; provider response saja tidak menjadi event lifecycle.
5. Gateway memvalidasi, meredaksi, menyimpan event dan projection, lalu menyiarkannya ke browser. Event diakui setelah commit.
6. Runner menyimpan spool event lokal sebelum acknowledgement dan mengirim ulang ketika koneksi pulih. Ledger command/run dan lease membantu rekonsiliasi sesudah crash.
7. Lease kedaluwarsa tidak otomatis membuktikan proses lama sudah mati. Reconcile/fence runner lama sebelum menjalankan attempt pengganti yang dapat menimbulkan side effect; jika tidak dapat dipastikan, status tetap tidak pasti.

Antrean PostgreSQL membutuhkan implementasi reaper, lease renewal, observabilitas dan retry policy; SKIP LOCKED sendiri bukan sistem job lengkap. MVP mengutamakan at-least-once delivery dengan deduplikasi, bukan klaim exactly-once untuk tool eksternal.

Setiap run memakai worktree/workspace tersendiri. Isolasi Git tidak sama dengan pembatasan akses OS: jalankan proses dalam container atau batas OS yang sesuai dengan scope file, jaringan, resource dan secret. Runner supervisor boleh mengelola sandbox, tetapi credential supervisor dan Docker socket tidak diberikan kepada proses agent.

Probes OMP/Codex yang tercatat di `docs/m0-feasibility.md` adalah bukti historis saja, bukan kandidat runtime MVP. Agent Office Execution Engine dan Provider Adapter tetap batas yang terpisah; provider/gateway tidak memberi event, policy, atau otorisasi tool secara implisit.


### 12.7 Event minimum

Envelope event mencakup `event_id`, `schema_version`, `source_id`, `source_session_id`, `workspace_id`, `project_id`, `agent_instance_id`, `task_id`, `run_id`, `parent_run_id` bila relevan, `event_type`, `occurred_at`, `received_at`, serta sequence/cursor jika disediakan sumber. Field yang tidak tersedia dicatat eksplisit sebagai tidak diketahui.

| Kelompok | Contoh event yang dibutuhkan |
| --- | --- |
| Lifecycle | run.started, run.succeeded, run.failed, run.canceled |
| Aktivitas | step.started/completed, tool.started/completed, response/output.available |
| Interaksi | input.requested, input.received, approval.requested/resolved |
| Delegasi | delegation.created, child.started, child.completed/failed |
| Kesehatan | session.heartbeat, connection.lost, connection.restored |
| Hasil | artifact.created, check.completed, review.accepted/changes_requested |

Nama tersebut adalah **usulan protokol aplikasi**, bukan nama event resmi dari OMP, Pi, atau provider lain. Adapter bertanggung jawab memetakannya dengan bukti yang sesuai. Jika tidak bisa memetakan suatu aktivitas, aplikasi menampilkan keterbatasan.

### 12.8 Reliabilitas event dan command

- Event duplikat tidak menciptakan task/run/artefak ganda atau menambah biaya dua kali.
- Event terlambat dimasukkan ke histori dengan urutan sumber yang tersedia; tidak meregresikan terminal state.
- Event yang sudah diakui tersimpan harus bertahan setelah restart. Acknowledgement tidak diberikan sebelum penyimpanan durable.
- Reconnect menggunakan cursor/replay jika tersedia. Jika tidak ada replay, aplikasi meminta snapshot dan memberi tanda rentang histori yang hilang.
- Run yang tidak dapat direkonsiliasi tetap berstatus terakhir dengan indikator ketidakpastian. Aplikasi tidak melakukan retry otomatis.
- Command memiliki correlation/request ID dan hasil acknowledgement. Status UI membedakan dikirim, diterima, dan selesai diterapkan.
- Koneksi dari runner diautentikasi dan dibatasi scope; event dari proyek lain tidak diterima hanya berdasarkan ID yang dikirim payload.
- Integrasi tool, termasuk MCP bila kelak dipakai, tidak otomatis menyediakan lifecycle/session telemetry. Sumber telemetry harus dibuktikan terpisah.

### 12.9 Deployment awal dan evolusi

**Baseline pilot:** Docker Compose berisi reverse proxy, web Next.js, API FastAPI, dispatcher Python, dan PostgreSQL. Tambahkan volume artefak serta backup. Runner dapat berada pada host yang sama dengan isolasi proses yang memadai, atau pada mesin lain yang membuka koneksi outbound terautentikasi ke gateway. [R18]

API menangani session login dan otorisasi; browser menggunakan cookie HttpOnly/Secure sesuai deployment, proteksi CSRF pada mutation, serta validasi Origin pada WebSocket. Owner-only pada MVP tidak menghapus pemeriksaan scope di server. Credential provider berada pada runner/secret store dan tidak ikut snapshot yang dikirim ke browser.

Profil awal 4 vCPU/8 GB pada bagian NFR adalah target pengujian control plane, bukan sizing seluruh agent. Tambahkan kebutuhan build/test dan runtime pada host runner berdasarkan proyek nyata. Pilihan provider cloud atau model lokal juga memengaruhi kebutuhan CPU/GPU secara terpisah dari renderer 3D di perangkat pengguna.

| Kapan kebutuhan berubah | Langkah berikutnya |
| --- | --- |
| API/gateway perlu beberapa replica | Tambahkan fan-out event antarreplica dan shared presence; replay tetap dari database |
| Perlu fan-out/cache | Redis opsional; Pub/Sub memiliki delivery at-most-once sehingga tidak menjadi satu-satunya histori atau antrean task [R21] |
| Volume command/retry melampaui dispatcher sederhana | Evaluasi broker/queue worker terkelola berdasarkan metrik; pertahankan outbox, idempotensi dan kontrak runner |
| Artefak membesar atau beberapa host harus berbagi | Pindahkan Artifact Store ke S3-compatible/object storage dengan ACL dan lifecycle yang diuji |
| Banyak manusia menjelajah bersama | Tambahkan layanan room/presence; sinkronisasi pose terpisah dari event kerja |
| Banyak runtime | Tambahkan adapter dengan contract tests; frontend dan domain task/run tetap sama |

### 12.10 Trade-off pilihan teknologi

- **Next.js + R3F dipilih** karena produk menggabungkan kantor 3D dengan panel pekerjaan dan konfigurasi yang dominan web. Engine game penuh dapat dievaluasi jika kebutuhan bergeser ke simulasi kompleks, editor dunia luas, atau platform native; kebutuhan itu belum menjadi P0.
- **FastAPI dipilih** karena sesuai pengalaman Python pengguna dan memungkinkan domain/control plane serta runner Python. Backend TypeScript juga layak; berpindah bahasa tidak diperlukan hanya untuk memakai Three.js.
- **Rapier digunakan untuk movement/collision**, dengan pathfinding sederhana terpisah. Ini menjaga eksplorasi bisa diuji tanpa menambah simulasi fisika yang tidak dibutuhkan.
- **PostgreSQL menangani data dan antrean awal** untuk membatasi jumlah layanan. Beban operasional dispatcher khusus adalah trade-off yang harus diuji pada M0/M1.
- **Redis, broker tambahan, Kubernetes, dan framework orkestrasi AI baru tidak diwajibkan pada MVP.** Tambahkan berdasarkan keterbatasan terukur. Aplikasi awal membungkus runtime pengguna melalui adapter sehingga tidak harus membangun agent loop baru.
- **3D bersifat interaktif dan live**, tetapi tidak dijadikan penentu kebenaran pekerjaan. Pemisahan ini membuat UI tetap responsif dan hasil agent dapat diaudit.

## 13. Prompt pengguna: temuan dan perilaku produk

Contoh prompt Backend meminta peran arsitek sistem, rancangan scalable, detail backend, dan implementasi production-ready dalam satu instruksi. Contoh base agent `system-architect` justru membatasi desain backend rinci ke `senior-backend-architect`. Bila digabung tanpa aturan, keduanya berpotensi tumpang tindih.

Temuan ini merupakan penilaian terhadap teks yang diberikan — **[High confidences]** untuk adanya cakupan yang bertumpang tindih; apakah menimbulkan kegagalan eksekusi perlu diuji pada task nyata.

| Temuan | Persyaratan produk | Saran konfigurasi |
| --- | --- | --- |
| Peran sistem dan backend bercampur | Tampilkan role, scope, dan potensi konflik sebelum run | Gunakan definisi berbeda jika tanggung jawabnya memang berbeda |
| “Production-ready” tidak memiliki ukuran | Task wajib memiliki acceptance criteria yang dapat diperiksa | Tentukan kebutuhan auth, error handling, test, deployment, dan observability sesuai task |
| “Scalable” tanpa workload/SLO | Task dapat menyimpan asumsi, kendala, dan pertanyaan terbuka | Minta konteks skala; pilih desain paling sederhana yang memenuhi kebutuhan |
| Nama agent tujuan ditulis di prose | Pemetaan delegasi harus eksplisit | Jika `senior-backend-architect` belum tersedia, tampilkan referensi belum terselesaikan |
| Skill belum disertakan isinya | Jangan mengklaim kompatibilitas skill pengguna | Validasi per paket setelah diimpor |

**Aturan preservasi:** aplikasi tetap menerima dan menyimpan prompt asli. Saran pemisahan atau perbaikan bahasa hanya diterapkan jika pengguna memilih membuat versi baru.

Contoh front matter pengguna berikut sudah cukup sebagai input importer:

```yaml
name: system-architect
description: Design system boundaries, cross-service interactions, deployment topology, and operational qualities.
```

Role, working method, boundaries, dan output di body Markdown dipertahankan sebagai instruksi. Pengaturan model, permission, resource, dan mapping delegasi dikelola sebagai konfigurasi eksplisit. Aplikasi tidak menjanjikan dapat menyimpulkan seluruh aturan secara aman hanya dari prose.

## 14. Keamanan, privasi, dan batas wewenang

Kebutuhan ini berasal dari fungsi produk yang menjalankan agent pada proyek; implementasi rinci mengikuti deployment yang dipilih.

- Workspace privat secara default dan membutuhkan autentikasi. MVP hanya memiliki satu owner; model izin tetap mengecek proyek/resource pada server.
- Credential provider dan akses repository disimpan di server/runner melalui mekanisme secret, tidak di file agent, browser, atau event yang disiarkan.
- Prompt dan skill merupakan instruksi yang tidak dipercaya untuk pemberian hak. Akses file, jaringan, tool, dan tindakan eksternal dibatasi oleh runner/policy di luar instruksi model.
- Jika runtime tidak mampu menegakkan batas yang diwajibkan, adapter tidak boleh menjalankan Managed run pada scope tersebut; mode Observe dapat tetap tersedia.
- Resource lintas proyek ditolak tanpa grant eksplisit. Memindahkan avatar atau mengubah nama ruang tidak memberi grant.
- Log dan artefak menjalani redaksi secret yang diketahui serta pembatasan output sebelum ditampilkan. Deteksi otomatis tidak dianggap sempurna; cakupan pencatatan diminimalkan.
- Preview Markdown/HTML dan nama file diperlakukan sebagai konten tidak tepercaya. Path traversal dan akses artefak tanpa izin harus ditolak.
- UI menjelaskan data apa yang dikirim ke model/provider yang dipilih. Pengiriman hanya mencakup konteks yang dibutuhkan run.
- Custom endpoint divalidasi dan dibatasi pada allowlist egress runner; jangan meneruskan URL pilihan pengguna ke jaringan internal atau metadata host. Gateway/router dapat melakukan egress lanjutan di luar kontrol runner sehingga konfigurasi upstream, fallback, dan batas biaya harus dinyatakan terpisah.
- Persetujuan merge, publish, deployment, atau tindakan destruktif mengikuti policy yang telah dikonfigurasi; keputusan tercatat bersama scope tindakan.

## 15. Persyaratan nonfungsional dan target awal

Seluruh angka berikut adalah **target usulan**, belum merupakan benchmark. Profil uji dicatat agar hasil dapat diulang: desktop 16 GB RAM dengan GPU terintegrasi dan browser stabil yang didukung; viewport sekitar 1440 × 900; server aplikasi 4 vCPU/8 GB di luar runtime model; jaringan RTT maksimal 100 ms. Tablet/mobile diuji terpisah untuk alur daftar dan detail. Spesifikasi akhir dibekukan sebelum pengukuran penerimaan.

| ID | Area | Target dan cara ukur |
| --- | --- | --- |
| NFR-01 | Pembaruan status | p95 ≤2 detik dari event diterima server sampai status terlihat di client foreground pada profil uji |
| NFR-02 | Keterlambatan sumber | Waktu kejadian dan waktu penerimaan ditampilkan; delay dari runtime dilaporkan terpisah dari NFR-01 |
| NFR-03 | Pemuatan awal | Ringkasan agent/task dapat digunakan ≤3 detik p95; adegan 3D boleh dimuat bertahap |
| NFR-04 | Kelancaran kantor | Target ≥30 FPS pada profil uji selama karakter berjalan, kamera bergerak, 50 avatar terlihat dan 10 run mengirim event; kualitas rendah dan daftar tersedia |
| NFR-05 | Skala pilot | 50 instance terdaftar, 10 run mengeksekusi bersamaan, 1 owner, beberapa proyek dalam 1 workspace |
| NFR-06 | Beban event | Uji 20 event/detik berkelanjutan 30 menit dan burst 100/detik selama 10 detik; event lifecycle yang diakui tidak hilang, antrean dipantau |
| NFR-07 | Pemulihan | Setelah koneksi pulih, latest snapshot konsisten ≤10 detik untuk dataset uji 10.000 event; histori gap diberi label jika sumber tidak mendukung replay |
| NFR-08 | Durabilitas | Tidak kehilangan event yang telah diakui pada uji restart proses; backup harian dengan target RPO 24 jam/RTO 4 jam untuk pilot, diverifikasi melalui restore |
| NFR-09 | Aksesibilitas | Alur inti berfungsi melalui keyboard, status memiliki teks/ikon, reduced motion dan fallback daftar tersedia |
| NFR-10 | Observabilitas | Error ingestion, lag, antrean, reconnect, kegagalan command, dan adapter health dapat diinspeksi |
| NFR-11 | Integritas | Uji duplikasi/out-of-order tidak membuat run ganda, status mundur, atau biaya terhitung dua kali |
| NFR-12 | Keamanan akses | Uji akses lintas proyek, artefak tanpa izin, command tidak berwenang, dan input impor berbahaya ditolak |
| NFR-13 | Respons eksplorasi | Input-to-visible-motion p95 ≤100 ms pada client foreground; diukur lokal, tidak bergantung pada latensi API/model |
| NFR-14 | Pemuatan dunia | Scene awal dapat dijelajahi ≤8 detik p95 pada 20 Mbps/RTT ≤100 ms; target aset awal terkompresi ≤8 MiB, aset tambahan dimuat bertahap |
| NFR-15 | Pemisahan state | Gerakan/camera tidak membuat run, tool call, atau status kerja baru; penyimpanan pose maksimal 1 kali per 5 detik saat aktif, plus saat berhenti/keluar |
| NFR-16 | Ketahanan presentasi | Error scene, kehilangan WebGL context, dan tab background tidak menghentikan pekerjaan; recovery menampilkan snapshot server sebelum animasi kerja dilanjutkan |

Metrik performa kantor tidak mencakup waktu respons model. Tidak ada janji SLA publik pada pilot; availability diukur sebelum menetapkan SLA.

## 16. Kriteria penerimaan end-to-end

Kriteria berlabel `[P1]` tidak memblokir alur fokus M3. Acceptance P0 memerlukan Task nyata, dua Run independen, safe action events, dan visualisasi 3D event-backed; bukan child handoff atau dukungan banyak provider.

| ID | Skenario dan tindakan | Hasil yang wajib terlihat | Rujukan |
| --- | --- | --- | --- |
| AC-01 | Impor base agent `system-architect` yang diberikan pengguna | Nama/deskripsi terpetakan, body system prompt tetap utuh, sumber asli dapat diekspor | FR-01–02 |
| AC-02 | Impor prompt bebas tanpa YAML | Pengguna melengkapi metadata dan menyimpan definisi tanpa mengubah isi otomatis | FR-02 |
| AC-03 | Tambah peran baru dan Agent Instance tambahan | Tidak memerlukan perubahan kode; instance idle dapat tampil dan ditugaskan Task | FR-01, FR-06, FR-15 |
| [P1] AC-04 | Pasang skill yang memerlukan tool tidak tersedia | Preflight menjelaskan ketidakcocokan; run yang membutuhkan tool itu tidak dimulai | FR-03 |
| AC-05 | Ubah system prompt saat Run berjalan | Run lama tetap memakai versi prompt awal; Run baru memakai versi baru | FR-04 |
| AC-06 | Klik start dua kali untuk task yang sama | Hanya satu run dibuat; status Starting berubah setelah acknowledgement yang sesuai | FR-05, FR-09 |
| AC-07 | Jalankan task nyata yang membaca resource dan menghasilkan artefak | Avatar dan detail mengikuti event; hasil dapat dibuka dan ditelusuri ke run | FR-05, FR-08, FR-12 |
| [P1] AC-08 | Parent mengusulkan dua child terdaftar; pengguna menyetujui setiap child sebelum Task/Run dibuat | Hubungan terlihat; input/akses dibatasi; parent menerima hasil atau error child | FR-10 |
| [P1] AC-09 | Capai batas concurrency dan delegasi | Child/run tambahan antre atau ditolak; tidak ada oversubscription atau deadlock | FR-09, FR-10, FR-15 |
| AC-10 | Engine meminta approval untuk aksi destruktif/di luar scope; pengguna menyetujui atau menolak dari detail Run | Run tetap menunggu sampai keputusan tercatat; aksi tidak dijalankan saat ditolak | FR-11 |
| AC-11 | Putuskan heartbeat selama lebih dari 30 detik | Tampilan stale, waktu terakhir terlihat, animasi kerja agent berhenti; karakter pengguna tetap dapat berjalan; run tidak otomatis dianggap selesai/gagal | FR-06, FR-08, bagian 11 |
| AC-12 | Reconnect setelah event start/aksi/terminal duplikat atau terlambat | Status dan last safe action cocok dengan snapshot terbaru; terminal state tidak mundur dan Run tidak terduplikasi | FR-08, FR-18 |
| [P1] AC-13 | Kirim cancel pada parent yang memiliki child | Cancel pending sampai dikonfirmasi; status child terlihat; yang belum berhenti tetap menjadi perhatian | FR-11 |
| AC-14 | Retry run yang gagal; lalu coba retry run yang statusnya tidak pasti | Gagal: attempt baru tercatat. Tidak pasti: eksekusi ulang diblokir sampai direkonsiliasi atau diisolasi secara eksplisit | FR-11, bagian 12 |
| AC-15 | Dua agent mengubah proyek yang sama | Masing-masing memiliki workspace terisolasi atau aturan serialisasi; konflik menjadi blocked | FR-13 |
| AC-16 | Runtime mengirim succeeded tanpa bukti test | Run selesai; task tetap In review; test tidak dilabeli terverifikasi | FR-12 |
| AC-17 | Agent Office Execution Engine melaporkan pause tidak didukung untuk Run yang dipilih | Capability terlihat; kontrol pause tidak dapat mengirim command, dan status tetap berdasarkan event yang tersedia | FR-05 |
| AC-18 | Impor prompt yang memerintahkan akses lintas proyek atau membocorkan secret | Impor tidak mengeksekusi instruksi; runner menolak akses terlarang; secret uji tidak muncul di UI/log | FR-02/11, bagian 14 |
| AC-19 | Refresh browser dan restart application service | Definisi, task, hasil, dan event yang diakui tetap ada; status runtime direkonsiliasi | FR-16, NFR-08 |
| AC-20 | Buka aplikasi di mobile atau tanpa WebGL, serta gunakan keyboard | Agent, Task, Run, hasil, dan kontrol inti tetap dapat diakses melalui daftar/detail yang dapat dioperasikan; Inbox bukan syarat P0 | FR-07 |
| AC-21 | Sumber tidak menyediakan token, progress, ETA, atau detail safe action | Menampilkan tidak tersedia; tidak mengisi nol, persentase, estimasi, atau aktivitas fiktif | FR-08, FR-15 |
| [P1] AC-22 | Arsipkan definisi dengan histori dan run aktif | Run aktif tidak berhenti mendadak; histori tetap tersedia; definisi tidak dipakai untuk run baru | FR-01 |
| AC-23 | Berjalan dengan WASD dan click-to-move melintasi beberapa ruang | Karakter responsif, menemukan jalur valid, dan tidak menembus dinding/furnitur solid | FR-17, NFR-13 |
| AC-24 | Klik tujuan yang terhalang atau berada di luar lantai | Indikator tujuan tidak valid muncul; tidak ada loop berjalan, teleport salah, atau task baru | FR-17 |
| AC-25 | Rotate/zoom/follow/overview lalu mengetik di panel | Kamera bekerja; karakter berhenti menerima hotkey saat fokus berada di input; session agent tetap sama | FR-17 |
| AC-26 | Dekati agent/meja/papan proyek lalu berinteraksi | Panel yang sesuai terbuka; tidak ada pemanggilan model sebelum pengguna mengirim task/pesan eksplisit | FR-17, FR-11 |
| AC-27 | Agent menerima task saat avatarnya belum mencapai meja | Status run diperbarui berdasarkan runtime; perjalanan visual tidak menunda atau menyatakan pekerjaan selesai | FR-18 |
| AC-28 | Putus koneksi sambil pengguna berjalan | Eksplorasi lokal tetap berjalan; agent stale, aksi kerja dinonaktifkan; setelah reconnect data diselaraskan | FR-17/18 |
| AC-29 | Refresh setelah posisi tersimpan, lalu ubah layout sehingga posisi lama tertutup | Posisi valid dipulihkan; posisi tidak valid kembali ke spawn aman tanpa mengubah task | FR-17/18 |
| AC-30 | Hilangkan meja atau tutup jalur saat scene version berubah | Client menghitung ulang rute dan menggunakan fallback; agent run tetap dapat diinspeksi | FR-18 |
| AC-31 | Background tab selama 60 detik lalu kembali, dan uji kegagalan aset/WebGL | Pekerjaan berlanjut di runner; snapshot dipulihkan; daftar tersedia ketika scene gagal | FR-18, NFR-16 |
| [P1] AC-32 | Uji 50 avatar/10 Run dan navigasi bersamaan; restart dispatcher saat start dikirim | Target skala diukur terpisah; request sama tidak membuat Run kedua | NFR-01/04/13, bagian 12 |
| [P1] AC-33 | Jalankan task melalui sedikitnya dua Provider Adapter; uji provider/fitur yang belum tersedia | Setiap Run merekam adapter/provider/model/route; kemampuan unsupported gagal tertutup; secret dan egress tetap terlindungi | FR-19, bagian 14 |
| AC-34 | Jalankan Task berbeda pada sedikitnya dua Agent Instance; amati status selama kedua Run berjalan | Kedua Instance dan Task/Run terlihat bersamaan. Status dan safe action mengikuti event Engine/Runner yang sama dengan detail Run; avatar hanya berpindah setelah assignment terverifikasi. Saat event stale, animasi kerja berhenti dan status stale terlihat; reasoning tersembunyi tidak ditampilkan. | FR-05, FR-06, FR-08, FR-18 |

**Demo penerimaan wajib:** pengguna memberi dua Task terpisah kepada dua Agent Instance, memulai keduanya, lalu melihat status, safe action, hasil, serta freshness masing-masing melalui event Engine/Runner nyata di kantor 3D dan list/detail. Satu Run menghasilkan artefak dari workspace terpilih. Putus koneksi tidak menghasilkan status rekaan; informasi yang sama tetap tersedia melalui daftar/detail. Simulasi tidak menggantikan bukti eksekusi nyata.

## 17. Ukuran keberhasilan produk

Target awal berikut diuji pada pilot; belum merupakan bukti peningkatan produktivitas.

| Metrik | Target usulan | Metode |
| --- | --- | --- |
| Memahami keadaan tim | Pada ≥4 dari 5 uji skenario, pengguna menemukan agent aktif, task, dan hambatan dalam ≤15 detik | Tugas terarah dengan data pilot |
| Menambahkan agent | Impor definisi valid, attach skill yang kompatibel, dan buat instance dalam ≤5 menit setelah runtime tersedia | Pengukuran onboarding |
| Penelusuran hasil | 100% artefak yang dikelola aplikasi memiliki relasi ke run dan task | Pemeriksaan data pilot |
| Kejujuran status | 0 klaim “succeeded” hanya karena timeout/putus koneksi pada skenario uji | Injeksi gangguan dan pemeriksaan timeline |
| Kemudahan intervensi | Dari Inbox ke konteks pertanyaan/approval maksimal 2 interaksi | Uji usability desktop/mobile |
| Manfaat tampilan kantor | Pengguna menyelesaikan skenario observasi tanpa lebih banyak kesalahan dibanding tampilan daftar | Perbandingan tugas yang sama; jika tidak tercapai, perbaiki kantor dan pertahankan daftar |

Jumlah avatar bergerak, jumlah pesan AI, dan durasi animasi tidak dipakai sebagai ukuran produktivitas. Penerimaan hasil dan waktu mengidentifikasi hambatan lebih dekat dengan nilai produk.

## 18. Tahapan delivery dan keputusan rilis

| Tahap | Hasil | Syarat keluar |
| --- | --- | --- |
| M0 — Provider inference API dan 3D feasibility | Capability matrix dan bounded Chat Completions request; 3D scene movement/collision/camera evidence | Inference request/response terkorrelasi dan fitur unsupported/unknown tercatat; scene/performance diukur; tidak menyatakan Managed execution |
| M1 — Fondasi data | Definition/Skill/Instance, Task/Run, satu endpoint config, permission dasar | Task/Run persisten dan scope owner/workspace terverifikasi |
| M2 — Kantor yang dapat dijelajahi | Explore/Overview, movement, collision, layout, aksesibilitas dan fallback | Karakter/scene dapat dijelajahi; belum mengklaim agent live |
| M3 — Alur kerja agent terlihat di 3D | Satu endpoint, tools allowlist, beberapa Run independen, event minimum, safe action summary, status stale, hasil | Demo AC-34 memakai Engine/Runner nyata; minimal safety boundaries dan event freshness lulus |
| M4 — Perluasan | Delegasi child, provider adapter tambahan/native API, advanced recovery, skala pilot, connectors, dan multi-user | Diprioritaskan terpisah; tidak memblokir demo alur M3 |

Target kalender dan skala pilot belum ditetapkan. M3 tidak menunggu child delegation, validasi banyak provider, atau target 50-avatar/10-Run. M3 tetap memerlukan satu alur Engine/Runner nyata, workspace/tools allowlist, secret/egress boundaries, bounded inference, dan event freshness; jangan mengganti bukti tersebut dengan simulasi atau klaim provider gratis.

## 19. Risiko dan trade-off

| Prioritas | Risiko | Respons produk |
| --- | --- | --- |
| Tinggi | Telemetry runtime tidak lengkap atau berubah antarversi | Mulai satu adapter, capability matrix, protokol berversi, contract tests; tampilkan gap |
| Tinggi | Animasi membuat pengguna percaya agent produktif padahal data stale | Timestamp, health terpisah, bukti aktivitas, hentikan animasi kerja agent ketika stale; eksplorasi pengguna tetap tersedia |
| Tinggi | Prompt/skill memberi instruksi yang melampaui akses | Enforcement di runner, scope minimum, isolasi, approval sesuai policy |
| P1 | Delegasi berulang meningkatkan biaya dan jumlah proses | Child delegation ditunda dari P0; ketika diprioritaskan, batas child/depth/concurrency/duration dan budget bersama diberlakukan |
| Tinggi | Konflik perubahan atau pengulangan side effect saat retry | Isolasi workspace, korelasi attempt, rekonsiliasi sebelum retry |
| Sedang | Cakupan 3D menghabiskan waktu sebelum integrasi berguna | Template kecil, list view sejak awal, kualitas visual bertahap, gate pada telemetry |
| Sedang | Peran architect/backend tumpang tindih | Scope eksplisit, peringatan konflik, task acceptance criteria, versi konfigurasi |
| Sedang | Peta sulit digunakan ketika agent bertambah | Filter, pencarian, grouping, detail terpisah, fallback daftar |
| Sedang | Ringkasan AI tidak akurat | Ringkasan opsional, label sumber, tautan event, raw evidence tetap dapat dibuka |
| Sedang | Klaim “selesai” disamakan dengan hasil benar | Run state terpisah dari task acceptance, bukti review/test |

**Trade-off utama:** kantor 3D menjadi permukaan utama untuk memahami aktivitas, sementara daftar/detail menyediakan fallback dan bukti. Satu configured provider/tool allowlist cukup untuk memvalidasi alur awal; provider breadth, delegasi, dan skala pilot ditunda tanpa mengubah batas keselamatan P0.

## 20. Keputusan yang perlu ditetapkan sebelum implementasi

Dokumen ini sudah dapat digunakan untuk review kebutuhan. Pertanyaan berikut tidak menghalangi penyusunan PRD, tetapi harus ditutup sebelum area terkait dibangun.

| ID | Keputusan | Default yang diusulkan | Dampak jika berbeda |
| --- | --- | --- | --- |
| D-01 | Kontrak execution/runtime | Execution loop dan policy berada di Agent Office; Phase 1 tidak mengintegrasikan OMP/Codex atau coding-agent backend khusus. | Model inference API bukan runtime; tools, workspace, lifecycle, approvals, events, dan safety tetap harus dibangun serta dibuktikan Agent Office |
| D-02 | Target deployment | Rekomendasi: Docker Compose pada Linux privat; runner pada host terkontrol, dapat terpisah | VPS/NAS/laptop memengaruhi resource dan akses; runner tidak memerlukan endpoint publik |
| D-03 | Eksekusi dikelola Agent Office atau observasi runtime eksternal | Managed execution dimiliki Agent Office sebagai MVP; Observe melalui Runtime Adapter opsional setelah MVP | External runtime bukan dependency MVP; inference provider dan task execution adalah batas terpisah |
| D-04 | Detail pengalaman visual | Eksplorasi live ditetapkan; rekomendasi 3D isometrik, karakter sendiri, template kantor | Gaya aset, sudut kamera, dan perangkat minimum dibekukan setelah spike visual |
| D-05 | Bentuk paket skill yang tersedia | Markdown dan resource teks terlebih dahulu | Skrip, tool eksternal, dan format khusus membutuhkan validasi tambahan |
| D-06 | Cara memilih agent | Task ditugaskan eksplisit ke satu Instance; beberapa Instance boleh menjalankan Task independen; child delegation P1 | Routing/delegasi otomatis memperluas kebutuhan approval, budget, dan penjelasan keputusan |
| D-07 | Jumlah operator | Satu owner | Banyak manusia memerlukan role, konflik kontrol, dan audit lebih luas |
| D-08 | Sumber proyek pertama | Satu repository/workspace melalui runner | Integrasi GitHub/GitLab/VPS langsung membutuhkan scope serta credential terpisah |
| D-09 | Budget dan retensi | Tetapkan sebelum pilot; gunakan batas eksekusi dan retensi usulan sebagai awal | Menentukan biaya provider, storage, dan perilaku penghentian |
| D-10 | Baseline stack dan aset | Stack rekomendasi bagian 12; pin dependensi yang kompatibel dan aset yang boleh digunakan | Mengunci build reproducible, kapasitas renderer, dan kompatibilitas adapter |
| D-11 | Cakupan API model provider | Satu endpoint OpenAI-compatible melalui Provider Adapter untuk alur P0; adapter tambahan/native APIs P1 | Kompatibilitas hanya untuk endpoint/model/fitur yang diuji; tidak mengklaim semua provider otomatis kompatibel |
| D-12 | Batas izin tools | Tool allowlist untuk workspace/Run berjalan tanpa approval per langkah; destructive/out-of-scope action dan egress baru memerlukan policy/approval | Mengurangi interupsi tanpa memberi akses tak terbatas; Agent Office tetap mengikat approval pada action/resource yang diminta |

## 21. Definition of Done PRD dan tindak lanjut

PRD dianggap siap masuk implementasi setelah owner menyetujui tujuan, P0, D-01 sampai D-05, serta model izin dan sumber proyek awal. Engineering kemudian menyiapkan Design Doc yang menurunkan persyaratan ini menjadi desain adapter, kontrak event/command, skema data, isolasi runner, frontend, strategi recovery, dan rencana pengujian.

Keputusan desain yang mengurangi atau menambah P0 harus memperbarui PRD dan kriteria penerimaannya. Selama pilot, hasil pengujian dicatat sebagai bukti; label kompatibel, terverifikasi, dan siap produksi tidak diberikan hanya berdasarkan deskripsi prompt atau demonstrasi visual.

## 22. Sumber dan batas bukti

Dokumentasi primer berikut ditinjau pada 22 Oktober 2026. Referensi mendukung kemampuan dasar komponen; pilihan topologi, batas MVP, parameter performa dan keputusan deployment merupakan rekomendasi desain, belum benchmark implementasi.

| ID | Sumber | Penggunaan |
| --- | --- | --- |
| R1 | Permintaan pengguna, dua contoh prompt/base agent, dan permintaan lanjutan eksplorasi live | Kebutuhan produk dan bentuk impor |
| R2 | [Gather — Virtual Office](https://www.gather.town/virtual-office) | Inspirasi meja, ruang tim dan ketersediaan |
| R3 | [Kumospace — Navigating Kumospace](https://www.kumospace.com/navigating-kumospace) | Inspirasi navigasi, peta dan zoom |
| R4 | [Next.js — Lazy Loading](https://nextjs.org/docs/app/guides/lazy-loading) | Client component dan pemuatan scene bertahap |
| R5 | [React Three Fiber — Introduction](https://r3f.docs.pmnd.rs/getting-started/introduction) | React renderer untuk Three.js dan pasangan versi React |
| R6 | [Drei — Repository dan dokumentasi](https://github.com/pmndrs/drei) | Helper kamera dan kontrol |
| R7 | [Rapier — Character Controller](https://rapier.rs/docs/user_guides/javascript/character_controller/) | Movement dan collision karakter |
| R8 | [React Three Rapier — Repository](https://github.com/pmndrs/react-three-rapier) | Integrasi Rapier dan R3F |
| R9 | [shadcn/ui — Documentation](https://ui.shadcn.com/docs) | Komponen panel dan kontrol web |
| R10 | [TanStack Query — Overview](https://tanstack.com/query/latest/docs/framework/react/overview) | Pengelolaan data server pada client |
| R11 | [TanStack Store — Overview](https://tanstack.com/store/latest/docs/overview) | Pengelolaan state UI |
| R12 | [FastAPI — WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) | WebSocket dan batas contoh connection manager in-memory |
| R13 | [PostgreSQL — SELECT](https://www.postgresql.org/docs/current/sql-select.html) | Locking dan SKIP LOCKED pada tabel antrean |
| R14 | [SQLAlchemy — Asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) | Akses data async; rujukan versi 2.0, bukan klaim versi terbaru |
| R15 | [Alembic — Documentation](https://alembic.sqlalchemy.org/en/latest/) | Migrasi database |
| R16 | [Oh My Pi — RPC Protocol Reference](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md) | Jalur integrasi lintas proses, event, dialog dan lifecycle |
| R17 | [Oh My Pi — SDK](https://github.com/can1357/oh-my-pi/blob/main/docs/sdk.md) | Alternatif embedding dalam proses Bun |
| R18 | [Docker Compose — Documentation](https://docs.docker.com/compose/) | Pengelolaan layanan deployment pilot |
| R19 | [React Three Fiber — Performance Pitfalls](https://r3f.docs.pmnd.rs/advanced/pitfalls) | Frame loop, object reuse dan biaya pembaruan scene |
| R20 | [Three.js — GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html) | Pemuatan aset glTF dan ekstensi kompresi |
| R21 | [Redis — Pub/Sub](https://redis.io/docs/latest/develop/pubsub/) | Batas delivery fan-out dan perlunya histori durable terpisah |
| R22 | [Ollama — OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) | Bukti bahwa Ollama menyediakan subset OpenAI-compatible; tidak membuktikan kompatibilitas setiap model/fitur dengan OMP |
| R23 | [9Router](https://www.9router.com/) | Klaim vendor bahwa layanan menyediakan OpenAI-compatible routing endpoint; perlu verifikasi independen sebelum implementasi |
| R24 | [OmniRoute](https://www.omniroute.online/) | Klaim vendor bahwa layanan menerima OpenAI-compatible requests dan merutekan ke provider/upstream; perlu verifikasi independen sebelum implementasi |

Dokumentasi OMP pada branch main dapat berubah. Implementasi harus mengikat referensi ke versi/commit yang diuji. Ketersediaan surface RPC/SDK sudah terlihat dalam dokumentasi; adapter aplikasi, instalasi pengguna, policy enforcement dan delegasi end-to-end belum diverifikasi.

Tidak ada klaim integrasi dengan Gather/Kumospace atau penyamaan fitur dengan The Sims. Referensi permainan digunakan untuk pengalaman menjelajah, mengendalikan karakter, kamera, dan berinteraksi dengan lingkungan kantor. Aset, merek, serta detail visual dibuat sesuai kebutuhan produk.

## 23. Riwayat revisi

| Versi | Perubahan |
| --- | --- |
| 0.1 | PRD awal: definisi agent/skill, kantor virtual, telemetry, task, delegasi, hasil, policy, dan penerimaan |
| 0.2 | Eksplorasi live masuk P0; karakter, movement, camera, collision, minimap dan interaksi objek ditambahkan; arsitektur serta stack direkomendasikan; FR-17–18, NFR-13–16 dan AC-23–32 ditambahkan; referensi teknis diverifikasi melalui dokumentasi primer |
| 0.3 | Cakupan model provider dikonfirmasi: custom OpenAI-compatible endpoint untuk Ollama, 9Router, dan OmniRoute bila kompatibel; FR-19/AC-33 dan batas credential/egress ditambahkan. Uji M0 tetap OpenAI `gpt-5.2` dan tetap memerlukan cap serta egress terisolasi. |
| 0.4 | Cakupan provider diperluas: tidak ada prasyarat OpenAI-compatible; API native dan endpoint kompatibel diakses melalui Provider Adapter yang tersedia. FR-19, AC-33, arsitektur, dan verifikasi Phase 1 diperbarui; belum ada klaim dukungan provider yang belum diuji. |
| 0.6 | Orca CLI-only broker feasibility recorded. Its decision gates are not valid runtime tool approvals; FR-11 remains fail-closed pending a compatible interface and stable runtime invocation identity. |
| 0.7 | Orca CLI dibatasi untuk orkestrasi. Approval tool-call tetap berada di Agent Office; OMP call identity belum tersedia, sehingga approval masih fail-closed. |
| 0.8 | Phase 1 diarahkan dari integrasi coding-agent tertentu ke inference API OpenAI-compatible Chat Completions; probe OMP/Codex dipertahankan sebagai bukti historis, bukan target produk. Execution loop, tools, policy, lifecycle, dan safety tetap tanggung jawab Agent Office; dukungan provider/model/fitur harus diuji per kombinasi. |
| 0.9 | Tujuan dipertegas sebagai orkestrasi agent oleh Agent Office dengan kantor 3D live berbasis event. Definition menjadi template reusable, instance dibuat idle, Execution Engine memiliki loop/state, Provider Adapter hanya inference, child delegation memerlukan approval, dan Runtime Adapter eksternal bukan prasyarat MVP. Menambahkan AC-34 untuk handoff event-backed; 3D tetap sample-only. |
| 1.0 | Fokus P0 disederhanakan ke beberapa Agent Instance yang menjalankan Task terpisah pada satu workspace/endpoint; safe action/status event terlihat di 3D. Child delegation, katalog Skill, provider breadth, dan skala pilot ditunda; tool allowlist, workspace isolation, secret/egress boundaries, serta freshness tetap wajib. AC-34 menguji dua Run independen. |
| 1.1 | Kantor menampilkan Agent Instance persisten tanpa fixture agent/task; layout kantor tetap template statis. Avatar, lokasi, dan aktivitas kerja hanya ditampilkan dari data runtime/event yang terverifikasi, dan tetap tidak tersedia sampai feed nyata tersambung. |

