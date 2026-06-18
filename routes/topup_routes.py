import hashlib
import uuid
import json
import hmac
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from database import db_query, db_execute
from database import engine as db_engine
from sqlalchemy import text
from services.tripay_service import create_invoice
from config import TRIPAY_PRIVATE_KEY, DIGIFLAZZ_SECRET
from services.digiflazz_service import kirim_digiflazz
from pydantic import BaseModel
from fastapi import HTTPException
from services.nickname_service import check_game_nickname

import os

router = APIRouter()

@router.post("/topup")
async def topup(data: dict):
    wa_pembeli = data.get("phone")
    target_id = data.get("target_id")
    sku = data.get("nominal")
    method = data.get("method")
    nickname = data.get("nickname", "-")

    # 1. Ambil harga dari database (POSTGRESQL SYNTAX)
    res = await db_query("SELECT price FROM products WHERE sku=:sku", {"sku": sku})
    if not res:
        raise HTTPException(400, "Produk tidak ditemukan")
    
    # ⚡ FIX: Convert the Decimal price to a float for calculation
    price_val = float(res[0][0]) 

    admin_fee = 0
    metode_pembayaran = str(method).upper()
    if metode_pembayaran == "QRIS":
        admin_fee = int(price_val * 0.007)
    elif metode_pembayaran in ["OVO", "DANA"]:
        admin_fee = int(price_val * 0.015)
    else:
        admin_fee = 4500

    total_bayar = int(price_val) + admin_fee
    order_id = str(uuid.uuid4())

    try:
        # POSTGRESQL SYNTAX (DICTIONARY PARAMETER)
        await db_execute(
            """INSERT INTO topup (id, phone, target_id, nickname, nominal, amount, payment_status) 
               VALUES (:id, :phone, :target_id, :nickname, :nominal, :amount, 'UNPAID')""",
            {
                "id": order_id, "phone": wa_pembeli, "target_id": target_id, 
                "nickname": nickname, "nominal": sku, "amount": total_bayar
            }
        )
    except Exception as e:
        print(f"DATABASE ERROR: {e}")
        raise HTTPException(500, f"Gagal simpan database: {str(e)}")

    try:
        # WAJIB AWAIT
        tripay_res = await create_invoice(
            order_id=order_id,
            amount=total_bayar,
            method=method,
            customer_name="Customer MCD",
            customer_email="customer@mcd.com",
            customer_phone=wa_pembeli
        )
        
        if not tripay_res or not tripay_res.get("checkout_url"):
            raise Exception("Gagal mendapatkan link pembayaran dari Tripay")

        invoice_url = tripay_res.get("checkout_url")
        qr_url = tripay_res.get("qr_url")

        # POSTGRESQL SYNTAX
        await db_execute(
            "UPDATE topup SET invoice_url=:url WHERE id=:id", 
            {"url": invoice_url, "id": order_id}
        )
        
        return {"id": order_id, "invoice_url": invoice_url, "qr_url": qr_url or ""}
    except Exception as e:
        print(f"TRIPAY ERROR: {e}")
        raise HTTPException(500, f"Error Tripay: {str(e)}")

@router.get("/topup/{identifier}")
async def cek_status_pesanan(identifier: str):
    """
    Mencari pesanan berdasarkan Order ID (id) atau Nomor HP (phone)
    """
    # Gunakan db_engine hasil alias, bukan engine
    async with db_engine.connect() as conn:
        # Gunakan fungsi text() dari SQLAlchemy dan parameter :identifier
        query = text("""
    SELECT id, 
           topup_status AS status, 
           invoice_url, 
           target_id, 
           nominal AS nominal_name,
           phone,
           payment_status,
           sn,
           note
    FROM topup 
    WHERE id = :identifier OR phone = :identifier
    ORDER BY created_at DESC 
    LIMIT 1
""")
        
        # Eksekusi query dengan parameter dictionary
        result = await conn.execute(query, {"identifier": identifier})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan. Pastikan Nomor HP atau Order ID benar.")

        return {
            "id": row[0],             # Akses by index (id)
            "status": row[1],         # Akses by index (status)
            "qr_url": row[2],         # Akses by index (qr_url)
            "invoice_url": row[3],    # Akses by index (invoice_url)
            "target_id": row[4],      # Akses by index (target_id)
            "game": row[5],           # Akses by index (game)
            "nominal_name": row[6]    # Akses by index (nominal_name)
        }

# Buat schema model untuk request checker
class NicknameRequest(BaseModel):
    game_code: str
    user_id: str
    zone_id: str = ""

# Endpoint API baru
@router.post("/check-nickname")
async def api_check_nickname(req: NicknameRequest):
    nickname = await check_game_nickname(req.game_code, req.user_id, req.zone_id)
    
    if not nickname:
        # Mengembalikan error 400 jika ID tidak valid
        raise HTTPException(status_code=400, detail="ID Game tidak ditemukan atau salah ketik.")
    
    return {"status": "success", "nickname": nickname}

@router.get("/api/products")
async def get_public_products():
    rows = await db_query("SELECT sku, provider, name, price FROM products WHERE active=1 ORDER BY provider, price")
    return [{"sku": r[0], "provider": r[1], "name": r[2], "price": float(r[3])} for r in rows]

@router.post("/callback")
async def tripay_callback(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Callback-Signature")

    expected_sig = hmac.new(TRIPAY_PRIVATE_KEY.encode(), raw_body, hashlib.sha256).hexdigest()

    if signature != expected_sig:
        return {"success": False, "message": "Invalid signature"}

    data = json.loads(raw_body)
    merchant_ref = data.get("merchant_ref")
    status = data.get("status")

    if status == "PAID":
        order = await db_query("SELECT nominal, target_id, payment_status FROM topup WHERE id=:id", {"id": merchant_ref})
        
        if order:
            sku, target_id, current_status = order[0]
            if current_status != "PAID":
                await db_execute(
                    "UPDATE topup SET payment_status='PAID', topup_status='PROCESSING' WHERE id=:id",
                    {"id": merchant_ref}
                )
                print(f"🚀 Tripay LUNAS! Nembak Digiflazz untuk Ref: {merchant_ref}")
                # WAJIB AWAIT
                await kirim_digiflazz(sku=sku, tujuan=target_id, ref_id=merchant_ref)

    return {"success": True}

@router.post("/api/webhook/digiflazz")
async def digiflazz_webhook(request: Request, x_hub_signature: str = Header(None, alias="X-Hub-Signature")):
    body = await request.body()
    my_signature = "sha1=" + hmac.new(DIGIFLAZZ_SECRET.encode(), body, hashlib.sha1).hexdigest()
    
    if my_signature != x_hub_signature:
        raise HTTPException(status_code=403, detail="Signature tidak valid!")

    data = await request.json()
    payload = data.get("data", {})
    
    ref_id = payload.get("ref_id")
    status = payload.get("status")
    sn = payload.get("sn", "")
    
    if status == "Sukses":
        await db_execute(
            "UPDATE topup SET topup_status='SUCCESS', sn=:sn, note=:note WHERE id=:id", 
            {"sn": sn, "note": "Sukses", "id": ref_id}
        )
        print(f"✅ TOPUP SUKSES! Ref: {ref_id} | SN: {sn}")
    elif status == "Gagal":
        pesan_error = payload.get("message", "Gagal dari provider")
        await db_execute(
            "UPDATE topup SET topup_status='FAILED', note=:note WHERE id=:id", 
            {"note": pesan_error, "id": ref_id}
        )
        print(f"❌ TOPUP GAGAL! Ref: {ref_id} | Error: {pesan_error}")

    return {"message": "Webhook Digiflazz diterima"}

@router.get("/callback")
def tripay_return():
    return RedirectResponse(url="/")

@router.post("/topup/{identifier}/cancel")
async def cancel_transaction(identifier: str):
    try:
        await db_execute(
            "UPDATE topup SET payment_status='CANCELED', topup_status='FAILED' WHERE id=:id", 
            {"id": identifier}
        )
        return {"success": True, "message": "Transaksi berhasil dibatalkan"}
    except Exception as e:
        print(f"🚨 ERROR CANCEL: {e}")
        raise HTTPException(500, f"Gagal membatalkan transaksi: {str(e)}")