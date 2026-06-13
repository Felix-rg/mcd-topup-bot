import asyncio
from database import db_execute, db_query, init_db
from utils import hash_password

async def main():
    print("⏳ Menghubungkan ke PostgreSQL dan memastikan tabel siap...")
    # Pastikan tabel admin sudah terbuat
    await init_db()
    
    username = "admin"
    password_mentah = "mcd123" # Silakan ganti sesuai keinginan Anda
    
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