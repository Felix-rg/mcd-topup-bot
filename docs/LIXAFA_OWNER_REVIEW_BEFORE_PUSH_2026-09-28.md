# LIXAFA Owner Review Before Push

Identitas review: 2026-09-28
Finalisasi validasi: 2026-09-30
Scope: release stabilization, provider retry fencing, payment safety, hygiene, dan persiapan commit lokal. Tidak ada push, deploy, atau transaksi provider nyata pada review ini.

## 1. Executive Summary

Perubahan release sudah dipisah ke commit lokal yang dapat direview, termasuk penutupan race lease provider, kebijakan pembayaran terlambat setelah cancel, normalisasi manifest dependency, dan dokumentasi operasi. Regresi penuh, static validation, serta smoke API lokal lulus.

Namun branch belum boleh dipush. Ada kredensial hard-coded dalam skrip legacy root yang tidak terlacak, aset media CMS yang sudah telanjur terlacak perlu keputusan owner, dan staging masih memerlukan backup serta prosedur migrasi eksplisit. Tidak ada remote yang diubah.

## 2. Starting State

Baseline remote adalah `origin/main` pada `f22e89c`. Saat pekerjaan dilanjutkan, branch `main` sudah memiliki perubahan release lokal dan artefak runtime/legacy yang perlu dipisahkan. Baseline hygiene `bb563ed` telah menghapus artefak runtime yang sebelumnya terlacak; seluruh pekerjaan setelahnya tetap lokal.

Saat snapshot source/release diverifikasi sebelum dokumen ini direkam, `origin/main...HEAD` menunjukkan 0 commit di sisi remote dan 13 commit lokal di depan. Dokumen ini kemudian ditambahkan sebagai satu commit dokumentasi terpisah. Tidak ada fetch, merge, rebase, force operation, atau push yang dilakukan.

## 3. Diff Classification

Candidate source/release sebelum dokumen ini ditambahkan mencakup 108 file, 38.149 penambahan, dan 1.586 penghapusan. Kelompoknya adalah:

- fondasi aplikasi, konfigurasi, database async, dan compatibility shims;
- promosi, checkout, wallet, callback Tripay, webhook/polling Digiflazz, serta dashboard admin/storefront;
- 208 test regresi dan dokumentasi operasi;
- hygiene runtime/dependency;
- 14 aset CMS lama di `web/uploads/admin/` yang sudah berada dalam Git.

Tidak ada pemecahan hunk lintas-domain yang dilakukan; route, service, engine, dan test yang saling bergantung dipertahankan sebagai satu unit commit logis.

## 4. Legacy Script Review

Skrip aktif yang terlacak `scripts/reset_admin.py` kini membaca password dari environment dan tidak mencetaknya. `scripts/tripay.py` tidak lagi mencetak body respons provider mentah.

Empat file legacy tetap tidak terlacak dan tidak di-stage: `reset_admin.py`, `tripay.py`, `buatDB.py`, dan `scripts/start_ngrok.py`. Root `reset_admin.py` mengandung password hard-coded; nilainya tidak dicatat dalam laporan ini. Owner wajib merotasi/reset kredensial terkait lalu memutuskan arsip atau penghapusannya sebelum push. Root `tripay.py` juga merupakan duplikasi stale yang mencetak respons provider mentah.

## 5. requirements.txt Investigation

Versi parent `requirements.txt` berformat UTF-16LE sehingga Git menampilkan perubahan terhadap parent sebagai binary diff. Manifest saat ini sudah UTF-8 tanpa BOM/NUL, memiliki newline akhir, dan dikunci oleh `.gitattributes` sebagai text LF. Tambahan `aiosqlite==0.22.1` diperlukan untuk URL database `sqlite+aiosqlite` yang digunakan test/local runtime.

`pip check` lulus. Binary diff terhadap parent adalah konsekuensi format lama, bukan indikasi manifest saat ini korup. Installer environment staging tetap perlu divalidasi saat prosedur staging dijalankan.

## 6. Database/Migration Review

Bootstrap schema/data implisit sekarang dibatasi untuk `development` dan `test`. Pada `staging` dan `production`, startup/request path hanya memverifikasi koneksi database dan tidak menjalankan create/alter/seed/backfill secara diam-diam.

`scripts/migrate_promotions.py` tetap merupakan jalur migrasi eksplisit. Sebelum staging, owner/operator harus mengambil backup yang dapat dipulihkan, meninjau SQL/migrasi yang relevan, menjalankan migrasi dengan otorisasi eksplisit, lalu memverifikasi schema/data. Integrasi PostgreSQL live tidak dijalankan pada review lokal ini.

## 7. Provider Safety Review

Normal send Digiflazz sekarang menandai order sebagai `SENT_UNKNOWN` sebelum efek eksternal dan semua write outcome/cleanup `topup` memerlukan `provider_claim_id` yang sama serta lease belum kedaluwarsa. Worker yang kehilangan lease tidak boleh mengubah order yang sudah dimenangkan callback atau worker baru.

Audit tambahan menemukan catatan `provider_attempts` stale dapat salah ditandai selesai. Perbaikan akhir membuatnya ditutup sebagai `DISCARDED`/`STALE_DISCARDED`, bukan hasil provider aktif. Test regresi mensimulasikan callback yang memenangkan order saat respons provider lama kembali.

## 8. Payment/Wallet/Refund Review

Transisi pembayaran tetap menjaga terminal success, debit wallet/refund idempoten, dan retry provider hanya tersedia bagi state yang aman. Callback duplikat tidak menambah notifikasi, debit, credit, atau fulfillment kedua. Admin tidak dapat retry order success atau order dengan penanda pembayaran setelah cancel.

Refund wallet memakai jalur ledger idempoten; refund ulang tidak mengkredit saldo dua kali. Tidak ada transaksi provider/wallet nyata yang dibuat selama validasi.

## 9. Late-Paid-After-Cancel Policy

Jika callback atau rekonsiliasi Tripay tervalidasi membuktikan `PAID` setelah customer cancel, aplikasi mempertahankan `payment_status=CANCELED` dan `topup_status=FAILED`. Ia menyimpan reference/payload audit dan menulis `refund_status=PAYMENT_RECEIVED_AFTER_CANCEL`.

State ini memblokir fulfillment, finalisasi promo, notifikasi fulfillment, dan retry provider. Admin dapat menyelesaikan refund manual secara idempoten; setelahnya status refund menjadi `REFUNDED` sementara topup tetap `FAILED`. Matrix sumber kebenaran telah diperbarui di [ORDER_STATE_TRANSITION_MATRIX.md](ORDER_STATE_TRANSITION_MATRIX.md).

## 10. Admin Media Policy

Kebijakan lengkap ada di [ADMIN_MEDIA_STORAGE_POLICY.md](ADMIN_MEDIA_STORAGE_POLICY.md). Upload admin memakai UUID, folder allowlist, batas 5 MiB, dan URL publik `/web/uploads/admin/...`; saat ini validasi hanya berbasis ekstensi/ukuran dan memerlukan permission `settings:manage`.

Staging harus memakai volume persisten. Produksi harus memakai storage tahan lama dan backup database+media yang konsisten. Tidak ada garbage collection atau migrasi object storage dalam release ini.

## 11. Logical Commit Structure

Commit dipisahkan menurut batas tanggung jawab: hygiene artefak, foundation/runtime database, dependency manifest, promosi, provider/payment reconciliation, checkout/customer/admin workflow, storefront, dashboard, hardening race provider, whitespace hygiene, keamanan output script, serta dokumentasi operasi.

Pemecahan ini memungkinkan owner membaca perubahan payment/provider tanpa harus menilai UI/dashboard dalam diff yang sama.

## 12. Commit Hashes

Daftar berikut adalah commit source/release yang dinilai sebelum commit dokumentasi laporan ini dibuat.

- `a5fb087` — NEW STRUCTURE
- `bb563ed` — chore(hygiene): remove tracked local runtime artifacts
- `0ae0349` — feat(core): establish app core and guarded database runtime
- `8dc5eb1` — build(requirements): normalize portable dependency manifest
- `6f21c59` — feat(promotions): add guarded eligibility engine
- `d0314d6` — feat(providers): add guarded payment and provider reconciliation
- `bebed76` — feat(checkout): integrate customer promo payment and admin workflows
- `40f0bdf` — feat(storefront): connect checkout UI to guarded APIs
- `008734f` — feat(admin): add operations and promotion dashboard
- `f8a0711` — fix(providers): discard stale lease worker outcomes
- `4f69d6f` — chore(hygiene): remove release diff trailing whitespace
- `0e2ceeb` — fix(scripts): avoid printing sensitive runtime data
- `49784df` — docs(operations): add media storage and state policy

## 13. Regression Result

Perintah regresi penuh `pytest -q` selesai dengan **208 passed** dalam 183,27 detik. Satu warning tersisa berasal dari deprecation argument `loop` pada asyncio Python 3.9 di test koneksi production; tidak ada test gagal.

Target test tambahan untuk payment safety, engine retry, database runtime, dan settings juga telah dijalankan selama implementasi serta lulus sebelum regresi penuh.

## 14. Static Validation

`compileall` untuk `app`, `scripts`, dan `tests` lulus. `node --check` lulus untuk `web/js/topup.js`, `web/js/admin.js`, dan `web/js/dashboard.js`; tidak ada file `web/js/script.js` pada tree saat ini sehingga tidak diklaim diuji. `pip check` menyatakan tidak ada dependency broken.

`git diff --check origin/main..HEAD` lulus setelah delapan trailing whitespace dari release diff dibersihkan.

## 15. Smoke Test Result

Server dijalankan hanya pada loopback dengan `APP_ENV=test`, SQLite temporary, dan base URL Digiflazz/Tripay diarahkan ke `127.0.0.1:9`. Delapan endpoint memberikan HTTP 200: `/`, `/admin/health`, `/api/products`, `/api/promos/active`, `/api/site-settings`, `/api/storefront/stats`, `/api/pages`, dan `/promo/latest`.

Tidak ada checkout, callback eksternal, pembayaran, atau request provider nyata. Server smoke dihentikan setelah verifikasi.

## 16. Secret Scan

Scan kandidat source/release 108 path tidak menemukan literal credential ber-confidence tinggi, opaque token terkutip, atau private key; nilai rahasia tidak dicetak. Satu-satunya bentuk URL ber-kredensial berada pada fixture test, bukan runtime/config produksi. Referensi identifier sensitif lain berada pada konfigurasi, service provider, route, script, dan fixture test yang memang diharapkan.

`.env.example` memuat key sensitif dalam keadaan blank atau placeholder; `DATABASE_URL` adalah contoh SQLite lokal, bukan URL remote ber-kredensial. Temuan credential hard-coded pada root `reset_admin.py` berada di file untracked, sehingga tidak ikut candidate commit—namun tetap blocker operasional sampai dirotasi/ditangani.

## 17. Remaining Uncommitted Files

Sebelum laporan ini dibuat terdapat 27 file untracked yang sengaja tidak di-stage: 23 dokumen historis di `docs/`, tiga skrip legacy root (`reset_admin.py`, `tripay.py`, `buatDB.py`), dan helper development `scripts/start_ngrok.py`.

Tidak ada source feature yang masih modified atau staged di luar commit di atas. Dokumen historis dan skrip legacy memerlukan keputusan owner terpisah; tidak boleh dimasukkan dengan `git add .`.

## 18. Remaining Risks

- Root `reset_admin.py` memiliki credential hard-coded dan harus dirotasi sebelum arsip/penghapusan.
- Ada 14 aset admin CMS terlacak (12.642.503 byte) dan 7 aset runtime ignored (12.322.873 byte); owner perlu memutuskan apakah blob terlacak memang artefak release.
- Migrasi PostgreSQL/staging belum dieksekusi terhadap database staging nyata.
- Order canceled tanpa pembayaran yang dikonfirmasi dapat terus dipertimbangkan oleh rekonsiliasi sampai ada state keputusan operasional yang lebih eksplisit; ini perlu dipantau setelah staging.
- Upload media belum memvalidasi MIME/decode/antivirus dan role marketing belum mempunyai permission upload khusus.

## 19. Push Readiness Checklist

- [x] Tidak ada push/deploy/transaksi provider nyata.
- [x] Commit source/release dipisah secara logis.
- [x] Full regression, static validation, smoke lokal, `pip check`, dan diff check lulus.
- [x] Kebijakan media dan matrix late-paid-after-cancel tersedia.
- [ ] Rotate/invalidasi credential legacy, lalu arsip atau hapus `reset_admin.py` dengan persetujuan owner.
- [ ] Putuskan status 14 aset CMS yang sudah terlacak dan 23 dokumen historis untracked.
- [ ] Siapkan backup/restore, storage media persisten, environment secret nyata, dan migrasi eksplisit untuk staging.
- [ ] Jalankan smoke/migrasi yang disetujui di staging sebelum membuka trafik.

## 20. Final Verdict

NOT READY TO PUSH
