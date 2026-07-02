import asyncio

from app.database import db_execute, db_query, init_db
from app.security import hash_password
from core.settings import settings


async def main() -> None:
    print("⏳ Menghubungkan ke PostgreSQL dan memastikan tabel siap...")
    await init_db()

    username = settings.default_admin_username or "admin"
    password_mentah = settings.default_admin_password
    if not password_mentah:
        raise RuntimeError("Setel DEFAULT_ADMIN_PASSWORD di environment sebelum menjalankan reset admin")
    
    print(f"🔐 Melakukan hashing password untuk username: {username}...")
    hashed_pw = hash_password(password_mentah)
    
    # Cek apakah username sudah ada
    existing = await db_query("SELECT id FROM admin WHERE username=:username", {"username": username})
    
    if existing:
        print(f"⚠️ Username '{username}' sudah ada di database PostgreSQL. Memperbarui password...")
        await db_execute(
            "UPDATE admin SET password=:password WHERE username=:username",
            {"password": hashed_pw, "username": username}
        )
    else:
        print(f"➕ Membuat user admin baru...")
        await db_execute(
            "INSERT INTO admin (username, password) VALUES (:username, :password)",
            {"username": username, "password": hashed_pw}
        )
        
    print(f"✅ SUKSES! Akun Admin berhasil dikonfigurasi di PostgreSQL.")
    print(f"👉 Username: {username}")
    print(f"👉 Password: {password_mentah}")

if __name__ == "__main__":
    asyncio.run(main())
