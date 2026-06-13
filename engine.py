import asyncio
import logging
from database import db_query, db_execute
from services.digiflazz_service import kirim_digiflazz, cek_status_digiflazz

async def polling_status_engine():
    # 1. KIRIM TRANSAKSI YANG BARU DIBAYAR (PROCESSING)
    new_orders = await db_query("""
        SELECT id, phone, nominal 
        FROM topup 
        WHERE topup_status='PROCESSING'
    """)

    for o in new_orders:
        order_id = o[0]
        phone = o[1]
        sku = o[2] 
        
        try:
            res = await kirim_digiflazz(sku, phone, order_id)
            
            # --- TAMBAHKAN LINE INI UNTUK DEBUGGING ---
            print(f"DEBUG RESPONS DIGIFLAZZ ({order_id}): {res}")
            # ------------------------------------------
            
            status = res.get("data", {}).get("status")
            
            if status in ["Pending", "Success"]:
                await db_execute(
                    "UPDATE topup SET topup_status='PENDING_PROVIDER' WHERE id=:id", 
                    {"id": order_id}
                )
                print(f"🔄 Order {order_id} diteruskan ke Digiflazz")
            else:
                # Ini yang terpicu sekarang
                await db_execute(
                    "UPDATE topup SET topup_status='FAILED' WHERE id=:id", 
                    {"id": order_id}
                )
                print(f"❌ Order {order_id} Gagal diteruskan ke Digiflazz")
        except Exception as e:
            logging.error(f"🚨 Error kirim_digiflazz {order_id}: {e}")

    # 2. CEK STATUS TRANSAKSI YANG SEDANG BERJALAN DI DIGIFLAZZ
    pending_orders = await db_query("""
        SELECT id, phone, nominal 
        FROM topup 
        WHERE topup_status='PENDING_PROVIDER'
    """)

    for r in pending_orders:
        order_id = r[0]
        phone = r[1]
        sku = r[2]
        
        try:
            status_df = await cek_status_digiflazz(sku, phone, order_id)
            data = status_df.get("data", {})
            status = data.get("status")
            sn = data.get("sn", "")

            if status == "Success":
                await db_execute(
                    "UPDATE topup SET topup_status='SUCCESS', sn=:sn WHERE id=:id", 
                    {"sn": sn, "id": order_id}
                )
                print(f"✅ Order {order_id} SUKSES! SN: {sn}")
            elif status == "Gagal":
                await db_execute(
                    "UPDATE topup SET topup_status='FAILED' WHERE id=:id", 
                    {"id": order_id}
                )
                print(f"❌ Order {order_id} GAGAL dari pusat!")
        except Exception as e:
            logging.error(f"🚨 Error cek_status {order_id}: {e}")

async def auto_engine_loop():
    while True:
        try:
            await polling_status_engine()
        except Exception as e:
            logging.error(f"ENGINE ERROR {e}")
        
        # Jeda 15 detik tanpa memblokir event loop utama
        await asyncio.sleep(15)