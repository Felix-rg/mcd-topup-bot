from database import db_execute, db_query
from utils import hash_password

def reset():
    username = "admin"
    password_baru = "admin"
    
    # Generate hash yang PASTI dimengerti oleh utils.py kamu
    hashed = hash_password(password_baru)
    
    # Hapus dulu data lama biar bersih
    db_execute("DELETE FROM admin WHERE username=?", (username,))
    
    # Masukkan yang baru
    db_execute(
        "INSERT INTO admin (username, password) VALUES (?, ?)",
        (username, hashed)
    )
    
    print("--- DEBUG INFO ---")
    print(f"Username: {username}")
    print(f"Password: {password_baru}")
    print(f"Hash yang disimpan: {hashed}")
    print("------------------")
    print("✅ Akun admin berhasil direset! Silakan coba login lagi.")

if __name__ == "__main__":
    reset()