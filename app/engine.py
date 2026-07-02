import asyncio
import logging

from app.core.settings import settings
from app.database import db_execute, db_query
from app.services.digiflazz_service import cek_status_digiflazz, kirim_digiflazz


MAX_PROVIDER_RETRY = 3


async def polling_status_engine() -> None:
    try:
        new_orders = await db_query("""
            SELECT id, phone, nominal, COALESCE(provider_retry_count, 0)
            FROM topup
            WHERE topup_status = 'PROCESSING'
        """)
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (new orders): {exc}")
        return

    for order in new_orders:
        order_id = order[0]
        phone = order[1]
        sku = order[2]
        provider_retry_count = int(order[3] or 0)

        try:
            response = await kirim_digiflazz(sku, phone, order_id)
            status = response.get("data", {}).get("status")
            if status in ["Pending", "Success"]:
                await db_execute(
                    "UPDATE topup SET topup_status='PENDING_PROVIDER', provider_retry_count=0, provider_last_error=NULL WHERE id=:id",
                    {"id": order_id},
                )
                print(f"🔄 Order {order_id} diteruskan ke Digiflazz")
            else:
                error_message = response.get("data", {}).get("message", "Gagal diteruskan ke Digiflazz")
                next_retry = provider_retry_count + 1
                if next_retry >= MAX_PROVIDER_RETRY:
                    await db_execute(
                        "UPDATE topup SET topup_status='FAILED', provider_retry_count=:retry, provider_last_error=:error WHERE id=:id",
                        {"id": order_id, "retry": next_retry, "error": error_message},
                    )
                    print(f"❌ Order {order_id} gagal permanen setelah retry Digiflazz: {error_message}")
                else:
                    await db_execute(
                        "UPDATE topup SET provider_retry_count=:retry, provider_last_error=:error WHERE id=:id",
                        {"id": order_id, "retry": next_retry, "error": error_message},
                    )
                    print(f"🔁 Order {order_id} belum berhasil ke Digiflazz, retry ke-{next_retry}/{MAX_PROVIDER_RETRY}")
        except Exception as exc:
            next_retry = provider_retry_count + 1
            if next_retry >= MAX_PROVIDER_RETRY:
                await db_execute(
                    "UPDATE topup SET topup_status='FAILED', provider_retry_count=:retry, provider_last_error=:error WHERE id=:id",
                    {"id": order_id, "retry": next_retry, "error": str(exc)},
                )
                logging.error(f"🚨 Error kirim_digiflazz {order_id} (final): {exc}")
            else:
                await db_execute(
                    "UPDATE topup SET provider_retry_count=:retry, provider_last_error=:error WHERE id=:id",
                    {"id": order_id, "retry": next_retry, "error": str(exc)},
                )
                logging.error(f"🚨 Error kirim_digiflazz {order_id} (retry {next_retry}/{MAX_PROVIDER_RETRY}): {exc}")

    try:
        pending_orders = await db_query("""
            SELECT id, phone, nominal
            FROM topup
            WHERE topup_status = 'PENDING_PROVIDER'
        """)
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (pending orders): {exc}")
        return

    for order in pending_orders:
        order_id = order[0]
        phone = order[1]
        sku = order[2]

        try:
            status_df = await cek_status_digiflazz(sku, phone, order_id)
            data = status_df.get("data", {})
            status = data.get("status")
            sn = data.get("sn", "")

            if status == "Success":
                await db_execute(
                    "UPDATE topup SET topup_status='SUCCESS', sn=:sn WHERE id=:id",
                    {"sn": sn, "id": order_id},
                )
                print(f"✅ Order {order_id} SUKSES! SN: {sn}")
            elif status == "Gagal":
                await db_execute(
                    "UPDATE topup SET provider_last_error=:error WHERE id=:id",
                    {"id": order_id, "error": "Status gagal dari pusat provider"},
                )
                print(f"⚠️ Order {order_id} masih menunggu retry setelah status gagal dari pusat!")
        except Exception as exc:
            logging.error(f"🚨 Error cek_status {order_id}: {exc}")


async def auto_engine_loop() -> None:
    while True:
        try:
            await polling_status_engine()
        except Exception as exc:
            logging.error(f"ENGINE ERROR {exc}")

        await asyncio.sleep(settings.engine_poll_interval_seconds)
