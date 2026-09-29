# LIXAFA Order State Transition Matrix

Tanggal: 2026-07-12
Scope: Fase 1 Transaction Safety.

Terakhir direview: 2026-09-29. Sumber status diambil dari kode aktif `app/` dan mencakup kebijakan pembayaran Tripay yang diterima setelah pembatalan customer.

## 1. Status yang benar-benar digunakan

### payment_status pada `topup`

- `UNPAID`
- `PAID`
- `EXPIRED`
- `CANCELED`
- `FAILED`
- `REFUNDED`
- `REFUND`

Catatan: `REFUND` muncul sebagai status dari Tripay/callback, sedangkan aplikasi admin menggunakan `REFUNDED` untuk status akhir refund internal. Roadmap Fase 1 tidak menambah status baru.

`refund_status=PAYMENT_RECEIVED_AFTER_CANCEL` adalah marker review, bukan status pembayaran baru. Marker ini dipakai ketika callback atau rekonsiliasi Tripay yang tervalidasi membuktikan `PAID` sesudah customer cancel; `payment_status` tetap `CANCELED` dan `topup_status` tetap `FAILED` sampai admin menyelesaikan refund manual yang idempoten.

### topup_status pada `topup`

- `PENDING_PAYMENT`
- `PROCESSING`
- `PENDING_PROVIDER`
- `SUCCESS`
- `FAILED`

### refund_status pada `topup`

- kosong/null
- `REFUNDED`

### customer_wallet_deposits.status

- `PENDING`
- `CREDITED`
- `EXPIRED`
- `FAILED`
- `REFUND`

### wallet_ledger.entry_type

- `CREDIT`
- `DEBIT`

### promotion_redemptions.status

- `RESERVED`
- `REDEEMED`
- `RELEASED`

### support_tickets.status

- `OPEN`
- `IN_PROGRESS`
- `RESOLVED`
- `CLOSED`

### notification_outbox.status

- `QUEUED`

## 2. Prinsip state machine

- `topup_status=SUCCESS` adalah terminal untuk pengiriman provider.
- `payment_status=REFUNDED` adalah terminal untuk refund uang.
- `payment_status=PAID` tidak boleh turun menjadi `UNPAID`, `EXPIRED`, `CANCELED`, atau `FAILED`.
- `topup_status=SUCCESS` tidak boleh turun menjadi `PROCESSING`, `PENDING_PROVIDER`, atau `FAILED`.
- Order `SUCCESS` tidak boleh dikirim ulang ke provider.
- Event callback/webhook yang sama harus aman diproses ulang tanpa efek samping kedua.
- Event out-of-order tidak boleh menurunkan terminal state.
- Admin action mengikuti matrix yang sama, bukan sekadar menerima status valid.
- Efek eksternal seperti Digiflazz send harus didahului claim atomik.
- Wallet credit/debit harus atomic: saldo dan ledger commit bersama atau tidak sama sekali.

## 3. Matrix payment_status

| Dari | Ke | Event | Diizinkan | Guard | Efek samping | Terminal |
|---|---|---|---|---|---|---|
| kosong/null | `UNPAID` | Order dibuat dengan pembayaran eksternal | Ya | Order id unik | Buat invoice Tripay | Tidak |
| kosong/null | `PAID` | Order dibuat dengan Wallet atau gratis promo | Ya | Wallet debit/promo final atomic | Masuk queue provider | Tidak |
| `UNPAID` | `PAID` | Tripay callback/reconcile `PAID` | Ya | Signature valid, order masih `UNPAID`, event idempotent | `topup_status` menjadi `PROCESSING`, promo finalisasi, notifikasi sekali | Tidak |
| `UNPAID` | `EXPIRED` | Tripay callback/reconcile expiry | Ya | Order masih `UNPAID` | `topup_status` menjadi `FAILED`, promo release | Ya untuk pembayaran |
| `UNPAID` | `FAILED` | Tripay callback/reconcile failed | Ya | Order masih `UNPAID` | `topup_status` menjadi `FAILED`, promo release | Ya untuk pembayaran |
| `UNPAID` | `CANCELED` | Customer cancel | Ya | `topup_status` kosong/`PENDING_PAYMENT` | `topup_status` menjadi `FAILED`, promo release | Ya untuk pembayaran |
| `UNPAID` | `REFUND` | Tripay reports refund sebelum paid internal | Tidak normal | Abaikan kecuali provider reconciliation membuktikan dana sudah diterima | Log event | Ya/needs review |
| `PAID` | `REFUNDED` | Admin refund atau Tripay refund yang valid | Ya | Belum `REFUNDED`, refund idempotency key belum pernah dipakai | Wallet credit jika metode wallet, audit log | Ya untuk refund |
| `PAID` | `UNPAID` | Admin/callback lama | Tidak | Tolak/abaikan | Log illegal transition | Tidak |
| `PAID` | `EXPIRED` | Callback expired terlambat | Tidak | Tolak/abaikan karena paid lebih kuat | Log duplicate/out-of-order | Tidak |
| `PAID` | `FAILED` | Callback failed terlambat | Tidak | Tolak/abaikan karena paid lebih kuat | Log duplicate/out-of-order | Tidak |
| `PAID` | `CANCELED` | Customer/admin cancel | Tidak | Tolak | Log | Tidak |
| `REFUNDED` | `REFUNDED` | Refund request ulang | Ya, idempotent | Idempotency key sama atau order sudah refunded | Tidak credit kedua | Ya |
| `REFUNDED` | status lain | Admin/callback/reconcile | Tidak | Tolak/abaikan | Log | Ya |
| `EXPIRED` | status lain | Callback terlambat | Tidak, kecuali manual recovery khusus di luar Fase 1 | Tolak/abaikan | Log | Ya |
| `FAILED` | status lain | Callback terlambat/admin bebas | Tidak, kecuali manual recovery khusus dengan permission khusus | Tolak/abaikan | Log | Ya |
| `CANCELED` | `CANCELED` tetap + marker `PAYMENT_RECEIVED_AFTER_CANCEL` | Callback/reconcile Tripay `PAID` yang signature/reference/amount-nya valid sesudah customer cancel | Ya, review refund saja | Simpan reference dan payload; `topup_status` tetap `FAILED`; jangan finalisasi promo, kirim ke provider, atau kirim notifikasi fulfillment | Tidak; retry provider diblokir |
| `CANCELED` | status lain | Callback terlambat/admin bebas selain `PAID` valid di atas | Tidak | Tolak/abaikan | Log | Ya |

## 4. Matrix topup_status

| Dari | Ke | Event | Diizinkan | Guard | Efek samping | Terminal |
|---|---|---|---|---|---|---|
| kosong/null | `PENDING_PAYMENT` | Order eksternal dibuat | Ya | `payment_status=UNPAID` | Menunggu Tripay | Tidak |
| kosong/null | `PROCESSING` | Order wallet/gratis dibuat | Ya | `payment_status=PAID` | Siap claim provider | Tidak |
| `PENDING_PAYMENT` | `PROCESSING` | Payment menjadi `PAID` | Ya | Payment masih `UNPAID` sebelum update | Queue provider | Tidak |
| `PENDING_PAYMENT` | `FAILED` | Payment expired/failed/canceled | Ya | Payment belum paid | Release promo | Ya |
| `PROCESSING` | `PROCESSING` | Provider claim aktif | Ya, internal | Claim/lease atomik berhasil | Set lease metadata | Tidak |
| `PROCESSING` | `PENDING_PROVIDER` | Digiflazz accepted pending | Ya | Order diklaim worker ini | Simpan provider payload | Tidak |
| `PROCESSING` | `SUCCESS` | Digiflazz success langsung | Ya | Order diklaim worker ini, belum terminal | Simpan SN valid, promo finalisasi, notifikasi sekali | Ya |
| `PROCESSING` | `FAILED` | Digiflazz failed final atau max retry | Ya | Order diklaim worker ini, belum terminal | Simpan error, release promo | Ya |
| `PENDING_PROVIDER` | `SUCCESS` | Polling/webhook success | Ya | Belum `SUCCESS`/`FAILED`, SN tidak ditimpa dengan kosong | Simpan SN, promo finalisasi, notifikasi sekali | Ya |
| `PENDING_PROVIDER` | `FAILED` | Polling/webhook failed | Ya | Belum `SUCCESS`, status provider jelas failed | Simpan error, release promo | Ya |
| `PENDING_PROVIDER` | `PENDING_PROVIDER` | Polling still pending | Ya | Rate limit status check | Update last_check/payload | Tidak |
| `FAILED` | `PROCESSING` | Admin retry | Ya terbatas | `payment_status=PAID`, reason wajib, bukan permanent forbidden, claim lama clear | Audit, retry count reset | Tidak |
| `FAILED` | status lain selain retry valid | Admin/callback/webhook | Tidak | Tolak/abaikan | Log | Ya |
| `SUCCESS` | `SUCCESS` | Webhook/callback duplicate success | Ya, idempotent | Jangan kirim notifikasi ulang, jangan overwrite SN valid dengan kosong | Log duplicate | Ya |
| `SUCCESS` | `FAILED` | Webhook failed terlambat/polling failed/admin | Tidak | Tolak/abaikan | Log out-of-order | Ya |
| `SUCCESS` | `PROCESSING` | Admin retry/manual | Tidak | Tolak | Audit illegal attempt | Ya |
| `SUCCESS` | `PENDING_PROVIDER` | Webhook pending terlambat/admin | Tidak | Tolak/abaikan | Log | Ya |

## 5. Callback Tripay

Callback Tripay harus:

- Memvalidasi signature dengan format yang sama seperti provider.
- Memvalidasi JSON dan `merchant_ref`.
- Membuat event log.
- Memakai idempotency database, minimal berdasarkan provider `reference` atau hash payload + provider + event type.
- Update `UNPAID -> PAID` hanya jika row masih `UNPAID`.
- Tidak mengubah `PAID/REFUNDED/EXPIRED/CANCELED/FAILED` menjadi status yang lebih rendah.
- Callback duplicate `PAID` pada order yang sudah `PAID` atau `SUCCESS` harus return aman tanpa efek samping kedua.
- Callback/reconcile `PAID` yang valid untuk `CANCELED` dengan `topup_status=FAILED` tidak boleh fulfillment atau mengubahnya menjadi `PAID`; simpan marker `PAYMENT_RECEIVED_AFTER_CANCEL` dan arahkan ke refund manual.
- Callback `EXPIRED/FAILED` setelah `PAID` harus diabaikan dan dicatat.
- Callback `REFUND` tidak boleh membuat refund ganda.

## 6. Webhook Digiflazz

Webhook Digiflazz harus:

- Memvalidasi signature `X-Hub-Signature` tanpa mengubah format provider.
- Menghubungkan `ref_id` ke `topup.id`.
- Mengabaikan event tanpa order.
- Success hanya boleh menaikkan status dari `PROCESSING`/`PENDING_PROVIDER` ke `SUCCESS`.
- Failed hanya boleh mengubah `PROCESSING`/`PENDING_PROVIDER` ke `FAILED`.
- Pending tidak boleh menurunkan `SUCCESS`.
- SN valid yang sudah ada tidak boleh ditimpa oleh SN kosong/palsu.
- Notifikasi success/failed hanya dikirim saat transition benar-benar terjadi.
- Duplicate webhook harus dicatat, tetapi tanpa efek samping kedua.

## 7. Provider send claim

Sebelum kirim ke Digiflazz:

- Worker harus melakukan claim atomik terhadap order `PROCESSING`.
- Claim harus memiliki lease id/worker id dan timestamp.
- Order `SUCCESS` tidak dapat diklaim.
- Order `FAILED` tidak diklaim otomatis, kecuali admin retry mengubah ke `PROCESSING`.
- Lease aktif mencegah worker lain mengirim order yang sama.
- Lease kedaluwarsa boleh diklaim ulang jika worker mati sebelum efek eksternal.
- Setelah provider accepted/success/failed, lease dibersihkan atau status terminal/pending mengeluarkan order dari queue.

## 8. Refund

Refund harus:

- Diizinkan dari `payment_status=PAID`, atau dari marker `refund_status=PAYMENT_RECEIVED_AFTER_CANCEL` setelah admin memeriksa penerimaan dana.
- Jika order sudah `REFUNDED`, request ulang harus idempotent dan tidak credit wallet lagi.
- Untuk marker pembayaran setelah pembatalan, refund tidak boleh menghidupkan kembali fulfillment; `topup_status` tetap `FAILED`.
- Untuk pembayaran wallet, ledger `reference_type=refund` dan `reference_id=order_id` harus unik secara database.
- Refund harus punya reason dan actor admin.
- Status refund dan ledger harus commit dalam satu operasi aman atau dapat dipulihkan dengan idempotency.

## 9. Admin update

Admin update status harus:

- Memakai permission backend.
- Memiliki reason wajib untuk perubahan status sensitif.
- Menolak transisi ilegal:
  - `PAID -> UNPAID`
  - `PAID -> EXPIRED`
  - `SUCCESS -> PROCESSING`
  - `SUCCESS -> FAILED`
  - `REFUNDED -> PAID`
  - retry `SUCCESS`
  - retry provider untuk `PAYMENT_RECEIVED_AFTER_CANCEL`
  - refund kedua dengan efek credit kedua
- Menulis audit before/after/reason/actor.

## 10. Partial failure handling

| Kasus | Sumber kebenaran | Recovery |
|---|---|---|
| Invoice Tripay dibuat, DB update gagal | Tripay reference + order local | Reconcile Tripay by reference, admin sync |
| Callback PAID commit, promo/notifikasi gagal | `topup.payment_status=PAID` | Idempotent repair finalisasi promo, outbox retry |
| Provider send berhasil, DB gagal sebelum status pending | Digiflazz `ref_id=order_id` | Poll status by ref_id, claim lease expiry, no duplicate send tanpa claim |
| DB commit success, notifikasi gagal | DB topup/audit | Notification outbox retry |
| Wallet debit berhasil, order gagal dibuat | Wallet ledger | Compensation/refund ledger dengan idempotency key order/create failure |
| Refund order update berhasil, wallet credit gagal | `payment_status=REFUNDED` + ledger missing | Idempotent refund repair harus bisa credit sekali |
| Tripay `PAID` diterima setelah customer cancel | `refund_status=PAYMENT_RECEIVED_AFTER_CANCEL` + payload/reference | Blok fulfillment dan retry provider; admin review lalu tandai refund manual secara idempoten |
| Server restart saat callback/webhook | Event/provider reference | Reprocessing aman karena idempotency database |

## 11. Catatan implementasi

- Database default development/test saat audit adalah SQLite.
- Implementasi harus kompatibel SQLite dan PostgreSQL.
- SQLite tidak punya row-level lock seperti PostgreSQL; gunakan atomic conditional update, unique index, transaksi pendek, dan retry terbatas untuk database locked.
- Uang tetap memakai integer Rupiah pada logic baru. Kolom lama `NUMERIC` tetap dibaca secara kompatibel.
