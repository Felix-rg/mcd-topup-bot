from datetime import datetime, timedelta
import sqlite3
import time

def db_execute(query, params=()):
    for _ in range(5):
        try:
            conn = sqlite3.connect("db.sqlite3", timeout=60)
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                time.sleep(0.5)
            else:
                raise

def db_query(query, params=()):
    conn = sqlite3.connect("db.sqlite3", timeout=60)
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_log(order_id, event, message):
    created_at = (datetime.utcnow() + timedelta(hours=7)).isoformat()
    db_execute("INSERT INTO logs (order_id, event, message, created_at) VALUES (?, ?, ?, ?)",
               (order_id, event, message, created_at))
def init_db():
    # ... kodingan tabel yang sudah ada ...
    
    # TAMBAHKAN BARIS INI:
    try:
        # Perintah untuk menambah kolom 'amount' secara paksa jika belum ada
        db_execute("ALTER TABLE topup ADD COLUMN amount INTEGER", ())
        print("✅ Kolom 'amount' berhasil ditambahkan ke database!")
    except Exception as e:
        # Kalau kolom sudah ada, dia akan error tapi kita abaikan saja (pass)
        pass