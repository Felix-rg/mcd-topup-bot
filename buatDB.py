import sqlite3
import os

DB_NAME = "db.sqlite3"

def db_execute(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    conn.close()

def db_query(query, params=()):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows

def init_db():
    # HAPUS DB LAMA (Opsional: Aktifkan jika ingin benar-benar bersih)
    # if os.path.exists(DB_NAME):
    #    os.remove(DB_NAME)
    
    # Tabel Produk
    db_execute("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            provider TEXT,
            name TEXT,
            price INTEGER,
            cost_price INTEGER,
            active INTEGER DEFAULT 1
        )
    """)

    # Tabel Topup (Struktur Lengkap UniPin Style)
    db_execute("""
        CREATE TABLE IF NOT EXISTS topup (
            id TEXT PRIMARY KEY,
            phone TEXT,             -- Nomor WA Pembeli (untuk Tripay)
            target_id TEXT,         -- ID Game / No Tujuan (untuk Digiflazz)
            nickname TEXT,          -- Nama Akun Game
            nominal TEXT,           -- SKU Produk
            amount INTEGER,         -- Harga Jual (Penting untuk Profit)
            invoice_url TEXT,       -- Link Pembayaran Tripay
            qr_url TEXT,            -- Link Gambar QRIS
            payment_status TEXT DEFAULT 'UNPAID',
            topup_status TEXT DEFAULT 'PENDING',
            sn TEXT,                -- Serial Number dari Provider
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ Database baru berhasil diselaraskan!")

if __name__ == "__main__":
    init_db()