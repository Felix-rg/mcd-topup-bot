# LIXAFA PROJEK

Aplikasi FastAPI modern untuk layanan top-up pulsa, game, dan pembayaran digital dengan integrasi Tripay dan Digiflazz.

## Persyaratan
- Python 3.11+
- Virtual environment

## Cara jalan lokal
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Pengujian
```bash
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Deployment
- Gunakan file [Procfile](Procfile) untuk platform seperti Render/Heroku.
- File [runtime.txt](runtime.txt) menetapkan versi Python yang dipakai.
- Salin [.env.example](.env.example) menjadi .env dan isi variabel sensitif.

## Fokus yang diperbaiki
- Branding diubah dari McD TopUp Bot menjadi LIXAFA PROJEK
- Konfigurasi aplikasi disederhanakan agar lebih mudah dikembangkan
- Endpoint admin dan database dibuat lebih konsisten dan aman
