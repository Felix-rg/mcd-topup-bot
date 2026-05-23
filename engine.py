import time
import logging
import os
import shutil
from datetime import datetime, timedelta

from database import db_query, db_execute
from services.digiflazz_service import kirim_digiflazz, cek_status_digiflazz

def polling_status_engine():
    # 1. KIRIM TRANSAKSI YANG BARU DIBAYAR (PROCESSING)
    new_orders = db_query("""
        SELECT id, phone, nominal 
        FROM topup 
        WHERE topup_status='PROCESSING'
    """)

    for o in new_orders:
        order_id = o[0]
        phone = o[1]
        sku = o[2] # Ambil SKU langsung dari database
        
        try:
            res = kirim_digiflazz(sku, phone, order_id)
            status = res.get("data", {}).get("status")
            
            if status in ["Pending", "Success"]:
                db_execute("UPDATE topup SET topup_status='PENDING_PROVIDER' WHERE id=?", (order_id,))
            else:
                db_execute("UPDATE topup SET topup_status='FAILED' WHERE id=?", (order_id,))
        except Exception as e:
            logging.error(f"Error kirim_digiflazz {order_id}: {e}")

    # 2. CEK STATUS TRANSAKSI YANG SEDANG BERJALAN DI DIGIFLAZZ
    pending_orders = db_query("""
        SELECT id, phone, nominal 
        FROM topup 
        WHERE topup_status='PENDING_PROVIDER'
    """)

    for r in pending_orders:
        order_id = r[0]
        phone = r[1]
        sku = r[2]
        
        try:
            status_df = cek_status_digiflazz(sku, phone, order_id)
            data = status_df.get("data", {})
            status = data.get("status")
            sn = data.get("sn", "000000")

            if status == "Success":
                db_execute("UPDATE topup SET topup_status='SUCCESS', sn=? WHERE id=?", (sn, order_id))
            elif status == "Gagal":
                db_execute("UPDATE topup SET topup_status='FAILED' WHERE id=?", (order_id,))
        except Exception as e:
            logging.error(f"Error cek_status {order_id}: {e}")

def backup_database():
    try:
        if not os.path.exists("backups"):
            os.makedirs("backups")
        # Waktu WIB (+7 Jam)
        timestamp = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y%m%d_%H%M%S")
        backup_path = f"backups/db_{timestamp}.sqlite3"
        shutil.copy("db.sqlite3", backup_path)
    except Exception as e:
        logging.error(f"Backup error {e}")

def auto_engine_loop():
    while True:
        try:
            backup_database()
            polling_status_engine()
        except Exception as e:
            logging.error(f"ENGINE ERROR {e}")
        
        # Cek setiap 15 detik
        time.sleep(15)