# AGENTS.md

## Ringkasan proyek
Proyek ini adalah backend FastAPI untuk layanan top-up digital dengan integrasi provider Digiflazz dan Tripay. Fokus utama adalah alur order, pembayaran, webhook, dan polling status transaksi secara asynchronous.

Dokumentasi proyek utama: [README.md](README.md)

## Arsitektur penting
- Aplikasi utama berada di [app.py](app.py). Di sana router ditambahkan, middleware CORS diaktifkan, dan startup memanggil inisialisasi database serta loop background engine.
- Konfigurasi aplikasi berada di [config.py](config.py). Gunakan settings dari environment/.env, jangan hardcode secret atau URL penting.
- Koneksi database async menggunakan SQLAlchemy di [database.py](database.py). Untuk perubahan data, gunakan helper yang sudah ada (`db_execute` / `db_query`) agar gaya akses konsisten.
- Logika endpoint bisnis utama berada di [routes/topup_routes.py](routes/topup_routes.py). Untuk fitur baru, prefer menambahkan endpoint di router yang sesuai, bukan menulis logika di file lain.
- Integrasi eksternal ada di [services/digiflazz_service.py](services/digiflazz_service.py) dan [services/tripay_service.py](services/tripay_service.py). Jaga agar service tetap fokus pada komunikasi provider dan tidak mengandung logika bisnis UI.
- Background polling status transaksi ada di [engine.py](engine.py). Perubahan di area ini harus mempertahankan pola async dan tidak memblokir event loop.

## Konvensi kerja yang disarankan
- Tetap gunakan pola async/await untuk semua operasi I/O, termasuk HTTP, database, dan background task.
- Untuk perubahan database, gunakan query SQL yang kompatibel dengan PostgreSQL dan sesuaikan dengan struktur tabel yang ada.
- Jaga keamanan: jangan mengekspos secret, API key, atau token di kode. Pakai environment variables dan file .env.
- Saat menambah endpoint baru, pertimbangkan:
  1. router yang sesuai di folder [routes](routes)
  2. service terpisah di [services](services) jika ada integrasi eksternal
  3. dokumentasi atau respons yang konsisten dengan endpoint yang sudah ada
- Untuk perubahan pada alur pembayaran, pastikan webhook dan callback tetap aman dan divalidasi signature.

## Perintah lokal
Jalankan aplikasi lokal dengan:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Catatan penting
- Proyek saat ini masih menggunakan konfigurasi lokal dan ngrok untuk development.
- Saat bekerja pada deployment ke VPS, pertimbangkan reverse proxy, SSL, variable environment, dan proteksi akses database.
- Belum terlihat test suite terstruktur; verifikasi perubahan dengan menjalankan aplikasi dan menguji endpoint yang relevan.
