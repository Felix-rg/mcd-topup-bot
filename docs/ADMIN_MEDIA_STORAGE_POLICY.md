# Admin Media Storage Policy

Tanggal review: 2026-09-30
Status: kebijakan operasi untuk perilaku upload yang sudah ada; dokumen ini tidak memigrasikan atau menghapus media.

## 1. Tujuan dan ruang lingkup

Media yang diunggah dari dashboard admin adalah data CMS/runtime, bukan source code. Kebijakan ini berlaku untuk logo, gambar konten, promo, dan produk yang diunggah melalui aplikasi. Ia tidak mengubah aset statis yang sengaja versi-kontrol di luar area upload admin.

## 2. Perilaku aplikasi saat ini

- Endpoint `POST /admin/upload-image` memerlukan permission `settings:manage`.
- Folder input dibatasi ke `logos`, `content`, `promos`, dan `products`; nilai lain diarahkan ke `logos`.
- Ekstensi yang diterima adalah `.jpg`, `.jpeg`, `.png`, `.webp`, dan `.gif`.
- Ukuran maksimum adalah 5 MiB. Implementasi membaca isi file terlebih dahulu, kemudian menolak ukuran yang melampaui batas.
- Nama file asal tidak disimpan. Aplikasi membuat nama baru berupa UUID heksadesimal dan menyimpan file ke `web/uploads/admin/<folder>/`.
- Respons menyimpan/mengembalikan URL relatif `/web/uploads/admin/<folder>/<uuid>.<ext>`. Direktori `web` dipublikasikan oleh aplikasi sebagai static files di prefix `/web`.

Referensi URL media tersimpan bersama data CMS, termasuk pengaturan situs, halaman situs, produk (`image_url`/`logo_url`), dan promo (`image_url`). Backup media tanpa database, atau sebaliknya, dapat menghasilkan URL yang rusak.

## 3. Akses dan batas keamanan saat ini

Pembuatan nama UUID dan allowlist folder mencegah nama file pengguna menentukan path tujuan. Namun validasi saat ini berbasis ekstensi dan ukuran saja. Belum ada pemeriksaan MIME/magic bytes, decode gambar, pemindaian malware, normalisasi gambar, atau pembatasan dimensi/pixel.

Permission upload saat ini hanya `settings:manage`. Role yang hanya memiliki izin promo atau konten tidak otomatis dapat memakai endpoint upload generik; ini adalah batas permission yang perlu diputuskan owner bila workflow marketing memerlukannya.

File yang berhasil diunggah dapat diakses melalui static URL publik. Jangan unggah dokumen rahasia, identitas pribadi, atau file yang tidak memang ditujukan untuk ditampilkan publik.

## 4. Git dan artefak rilis

`.gitignore` mengecualikan `/web/uploads/admin/` agar upload baru tidak ikut commit secara tidak sengaja. Pada review ini ada 14 aset lama yang sudah terlacak Git di bawah direktori tersebut; aset tersebut tetap berada di riwayat/commit lokal sampai owner membuat keputusan eksplisit. Kebijakan ini tidak menghapus, memindahkan, atau menulis ulang aset tersebut.

Upload yang muncul di mesin kerja setelah aturan ignore dibuat adalah runtime data. Jangan memakai `git add .`; stage hanya file source/dokumen yang sudah ditinjau.

## 5. Staging

Staging harus memasang `web/uploads/admin/` pada volume persisten yang dapat ditulis oleh proses aplikasi. Filesystem container atau platform yang ephemeral akan menghilangkan upload saat deploy/restart. Sebelum deploy:

- buat direktori dan atur ownership proses aplikasi;
- restore snapshot media yang cocok dengan database staging bila staging memakai data salinan;
- verifikasi URL `/web/uploads/admin/...` dapat dibaca dari reverse proxy;
- uji satu upload gambar non-rahasia dan catat bahwa file bertahan setelah restart aplikasi.

## 6. Produksi dan penyimpanan tahan lama

Produksi harus menggunakan storage tahan lama yang dibackup, bukan filesystem ephemeral aplikasi. Volume persisten yang dikelola dengan backup reguler dapat menjadi tahap awal. Untuk scale-out/multi-instance, rencana yang direkomendasikan adalah object storage dengan prefix privat untuk penulisan aplikasi dan URL publik/terbatas khusus media yang boleh ditampilkan.

Migrasi ke object storage perlu dilakukan sebagai proyek terpisah: salin objek, verifikasi checksum dan URL database, gunakan periode dual-read atau redirect terukur, lalu hapus sumber lama hanya setelah backup dan persetujuan owner. Tidak ada migrasi object storage pada rilis ini.

## 7. Backup, restore, dan retensi

Backup harus menangkap database dan direktori media dalam jendela konsisten yang sama. Minimal simpan:

- dump database yang memuat URL media;
- arsip `web/uploads/admin/` atau backup volume/object storage;
- waktu snapshot dan identitas environment.

Saat restore, pulihkan database dan media dari snapshot yang sama, lalu sampling URL media dari halaman/promo/produk sebelum membuka trafik. Sistem saat ini tidak memiliki garbage collection atau endpoint delete media: mengganti atau menghapus referensi database tidak otomatis menghapus file lama. Penghapusan manual harus diawali backup dan pencarian semua referensi URL.

## 8. Limitasi dan tindak lanjut owner

- Tambahkan validasi MIME/decode gambar dan batas dimensi bila upload akan dibuka lebih luas.
- Putuskan apakah permission upload dipisah untuk operator marketing/promo.
- Putuskan nasib 14 aset admin lama yang masih terlacak Git sebelum push.
- Tetapkan retensi, proses penghapusan terotorisasi, dan monitoring kapasitas storage.
- Pastikan strategi backup/restore diuji di staging sebelum produksi.
