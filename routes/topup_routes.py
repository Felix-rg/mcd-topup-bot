import hashlib
import uuid
import json
import hmac
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from database import db_query, db_execute
from services.tripay_service import create_invoice
from config import TRIPAY_PRIVATE_KEY
from services.digiflazz_service import kirim_digiflazz
import os

router = APIRouter()

@router.post("/topup")
def topup(data: dict):
    wa_pembeli = data.get("phone")
    target_id = data.get("target_id")
    sku = data.get("nominal")
    method = data.get("method")
    nickname = data.get("nickname", "-") # Default "-" kalau kosong

    # 1. Ambil harga dari database
    res = db_query("SELECT price FROM products WHERE sku=?", (sku,))
    if not res:
        raise HTTPException(400, "Produk tidak ditemukan")
    price = res[0][0]

    # ==========================================
    # ⚡ PERBAIKAN: HITUNG BIAYA ADMIN TRIPAY
    # ==========================================
    admin_fee = 0
    metode_pembayaran = str(method).upper()

    if metode_pembayaran == "QRIS":
        admin_fee = int(price * 0.007)
    elif metode_pembayaran in ["OVO", "DANA"]:
        admin_fee = int(price * 0.015)
    else:
        admin_fee = 4500 # Default untuk VA

    total_bayar = int(price) + admin_fee
    # ==========================================

    # 2. Buat ID Transaksi (Order ID)
    order_id = str(uuid.uuid4())

    # 3. Simpan ke Database (PENTING: Gunakan total_bayar, bukan price)
    try:
        db_execute(
        """INSERT INTO topup (id, phone, target_id, nickname, nominal, amount, payment_status) 
           VALUES (?, ?, ?, ?, ?, ?, 'UNPAID')""",
        (order_id, wa_pembeli, target_id, nickname, sku, total_bayar)
    )
    except Exception as e:
        print(f"DATABASE ERROR: {e}")
        raise HTTPException(500, f"Gagal simpan database: {str(e)}")

    # 4. Kirim ke Tripay (PENTING: amount diisi total_bayar)
    try:
        tripay_res = create_invoice(
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

        # Update URL Invoice di database
        db_execute("UPDATE topup SET invoice_url=? WHERE id=?", (invoice_url, order_id))
        
        return {
            "id": order_id, 
            "invoice_url": invoice_url, 
            "qr_url": qr_url or "" 
        }

    except Exception as e:
        print(f"TRIPAY ERROR: {e}")
        raise HTTPException(500, f"Error Tripay: {str(e)}")

@router.get("/topup/{identifier}")
def check_status(identifier: str):
    try:
        # PERBAIKAN: Pakai rowid! Karena created_at sering bikin error 500 kalau kolomnya gak ada
        row = db_query("""
            SELECT payment_status, topup_status, invoice_url, nominal 
            FROM topup 
            WHERE id=? OR phone=? 
            ORDER BY rowid DESC LIMIT 1
        """, (identifier, identifier))
        
        if not row:
            raise HTTPException(404, "Transaksi tidak ditemukan")
            
        payment_status, topup_status, invoice_url, nominal = row[0]
        
        # Logika tampilan status
        display_status = payment_status
        if payment_status == "PAID" and topup_status == "SUCCESS":
            display_status = "SUCCESS"
        elif topup_status == "FAILED":
            display_status = "FAILED"
        elif payment_status == "PAID" and topup_status == "PROCESSING":
            display_status = "PROCESSING"
            
        return {
            "status": display_status,
            "invoice_url": invoice_url or "",
            "qr_url": "" # PERBAIKAN: Kosongkan fallback biar gambar ngga pecah
        }
    except Exception as e:
        print(f"🚨 ERROR CHECK STATUS: {e}")
        raise HTTPException(500, f"Error Server: {str(e)}")

@router.get("/api/products")
def get_public_products():
    rows = db_query("SELECT sku, provider, name, price FROM products WHERE active=1 ORDER BY provider, price")
    return [
        {
            "sku": r[0],
            "provider": r[1], 
            "name": r[2], 
            "price": r[3]
        } for r in rows
    ]

@router.post("/callback")
async def tripay_callback(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Callback-Signature")

    expected_sig = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if signature != expected_sig:
        return {"success": False, "message": "Invalid signature"}

    data = json.loads(raw_body)
    merchant_ref = data.get("merchant_ref")
    status = data.get("status")

    if status == "PAID":
        # 1. Cari data transaksinya dulu (biar tau SKU dan Nomor HP/ID targetnya)
        order = db_query("SELECT nominal, target_id, payment_status FROM topup WHERE id=?", (merchant_ref,))
        
        if order:
            sku, target_id, current_status = order[0]
            
            # Cegah double hit kalau Tripay ngirim callback 2 kali
            if current_status != "PAID":
                # 2. Update status jadi PAID di database
                db_execute(
                    "UPDATE topup SET payment_status='PAID', topup_status='PROCESSING' WHERE id=?",
                    (merchant_ref,)
                )
                
                # 3. OTOMATIS TEMBAK DIGIFLAZZ BEJIR!
                print(f"🚀 Tripay LUNAS! Nembak Digiflazz untuk Ref: {merchant_ref}")
                kirim_digiflazz(sku=sku, tujuan=target_id, ref_id=merchant_ref)

    return {"success": True}

# ==========================================
# ⚡ TAMBAHAN BARU: WEBHOOK DIGIFLAZZ
# ==========================================
DIGIFLAZZ_SECRET = "rahasiamcd 123" # Nanti ganti sesuai isi di panel Digiflazz lu

@router.post("/api/webhook/digiflazz")
async def digiflazz_webhook(
    request: Request,
    x_hub_signature: str = Header(None, alias="X-Hub-Signature")
):
    body = await request.body()
    my_signature = "sha1=" + hmac.new(
        DIGIFLAZZ_SECRET.encode(),
        body,
        hashlib.sha1
    ).hexdigest()
    
    if my_signature != x_hub_signature:
        raise HTTPException(status_code=403, detail="Signature tidak valid!")

    data = await request.json()
    payload = data.get("data", {})
    
    ref_id = payload.get("ref_id")
    status = payload.get("status")
    sn = payload.get("sn", "")
    
    if status == "Sukses":
        db_execute("UPDATE topup SET topup_status='SUCCESS', note=? WHERE id=?", (sn, ref_id))
        print(f"✅ TOPUP SUKSES! Ref: {ref_id} | SN: {sn}")
    elif status == "Gagal":
        pesan_error = payload.get("message", "Gagal dari provider")
        db_execute("UPDATE topup SET topup_status='FAILED', note=? WHERE id=?", (pesan_error, ref_id))
        print(f"❌ TOPUP GAGAL! Ref: {ref_id} | Error: {pesan_error}")

    return {"message": "Webhook Digiflazz diterima"}

@router.get("/callback")
def tripay_return():
    return RedirectResponse(url="/")

@router.post("/topup/{identifier}/cancel")
def cancel_transaction(identifier: str):
    try:
        # Ubah status di database jadi CANCELED
        db_execute(
            "UPDATE topup SET payment_status='CANCELED', topup_status='FAILED' WHERE id=?", 
            (identifier,)
        )
        return {"success": True, "message": "Transaksi berhasil dibatalkan"}
    except Exception as e:
        print(f"🚨 ERROR CANCEL: {e}")
        raise HTTPException(500, f"Gagal membatalkan transaksi: {str(e)}")