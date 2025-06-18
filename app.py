# ===== FILE: app.py =====
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import os
from pydantic import BaseModel
import sqlite3
import uuid
import datetime
import requests

from tripay import create_invoice
from config import PRICES, TRIPAY_CALLBACK_URL, INSTANCE_ID, TOKEN_ULTRAMSG  # fix import token

app = FastAPI(title="Mc'D TopUp API")
BASE_URL = os.getenv("BASE_URL","http://127.0.0.1:8000/topup")
# ===== DATABASE SETUP =====
conn = sqlite3.connect("db.sqlite3", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS topup (
    id TEXT PRIMARY KEY,
    phone TEXT,
    provider TEXT,
    nominal TEXT,
    status TEXT,
    invoice_url TEXT,
    created_at TEXT
)
''')
conn.commit()

# ===== DATA MODEL =====
class TopUpRequest(BaseModel):
    phone: str
    provider: str
    nominal: str
    method: str  # contoh: QRIS, BCA, DANA

class TopUpResponse(BaseModel):
    id: str
    status: str
    message: str
    invoice_url: str

# ===== API ENDPOINTS =====
@app.post("/topup", response_model=TopUpResponse)
def topup(req: TopUpRequest):
    provider = req.provider.lower()
    nominal = req.nominal.lower()
    ALLOWED_METHODS = {"QRIS", "OVO", "DANA", "ShopeePay"}

    if req.method.upper() not in ALLOWED_METHODS:
        raise HTTPException(status_code=400, detail="Metode pembayaran tidak didukung.")

    if provider not in PRICES:
        raise HTTPException(status_code=400, detail="Provider tidak dikenali. Gunakan salah satu dari: telkomsel, xl")

    if nominal not in PRICES[provider]:
        raise HTTPException(status_code=400, detail=f"Nominal tidak tersedia untuk {provider}. Pilihan: {', '.join(PRICES[provider].keys())}")
    
    def get_price(provider: str, nominal:str) -> int:
        return PRICES.get(provider, {}).get(nominal);\

    order_id = str(uuid.uuid4())
    created_at = datetime.datetime.utcnow().isoformat()
    amount = get_price(provider, nominal)


    invoice_url = create_invoice(order_id, req.phone, req.provider, req.nominal, req.method, amount)
    if invoice_url is None:
        raise HTTPException(status_code=500, detail="Gagal membuat invoice Tripay.")

    # Simpan ke database
    cursor.execute('''
        INSERT INTO topup (id, phone, provider, nominal, status, invoice_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (order_id, req.phone, req.provider, req.nominal, "pending", invoice_url, created_at))
    conn.commit()

    return TopUpResponse(
        id=order_id,
        status="pending",
        message=f"Silakan selesaikan pembayaran untuk top-up {req.phone}",
        invoice_url=invoice_url
    )

@app.post("/callback")
async def tripay_callback(req: Requests):
    data = await req.json()
    event = data.get("event")
    if event != "payment_status":
        return {"status": "ignored"}

    order_id = data.get("merchant_ref")
    status = data.get("status")

    if not order_id or not status:
        raise HTTPException(status_code=400, detail="Data callback tidak lengkap.")

    # Update status database
    cursor.execute("UPDATE topup SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()

    # Ambil nomor HP dari DB untuk kirim notifikasi WA
    if status.lower() == "paid":
        result = cursor.execute("SELECT phone FROM topup WHERE id = ?", (order_id,)).fetchone()
        if result:
            phone = result[0]
            res = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={
                    "token": TOKEN_ULTRAMSG,
                    "to": phone,
                    "body": f"✅ Pembayaran berhasil untuk pesanan {order_id}. Pulsa akan segera diproses."
                }
            )
            print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)

    return {"success": True}


@app.get("/topup/{order_id}", response_model=TopUpResponse)
def check_status(order_id: str):
    cursor.execute("SELECT phone, provider, nominal, status, invoice_url FROM topup WHERE id = ?", (order_id,))
    result = cursor.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Order ID tidak ditemukan.")

    phone, provider, nominal, status, invoice_url = result
    return TopUpResponse(
        id=order_id,
        status=status,
        message=f"Status pesanan untuk {phone} ({provider} - {nominal}) adalah: {status}.",
        invoice_url=invoice_url
    )

# ===== WHATSAPP WEBHOOK (ULTRAMSG) =====
def parse_message(text):
    try:
        parts = text.lower().strip().split()
        if parts[0] != "topup":
            return None
        return {
            "provider": parts[1],
            "nominal": parts[2],
            "phone": parts[4],
            "method": parts[6].upper()
        }
    except:
        return None

@app.post("/webhook")
async def whatsapp_webhook(req: Request):
    data = await req.json()
    if data.get("event_type") != "message_received":
        return {"status": "ignored"}

    msg_data = data.get("data", {})
    if msg_data.get("fromMe") or msg_data.get("self"):
        return {"status": "ignored"}

    sender = msg_data.get("from")
    message = msg_data.get("body")

    reply = "Format salah. Gunakan: topup telkomsel 10k ke 0812xxx via qris"
    parsed = parse_message(message)

    if parsed:
        payload = {
            "phone": parsed["phone"],
            "provider": parsed["provider"],
            "nominal": parsed["nominal"],
            "method": parsed["method"]
        }
        try:
            res = requests.post(f"{BASE_URL}/topup", json=payload, timeout=5)
            if res.ok:
                invoice = res.json().get("invoice_url", "-")
                reply = f"\u2705 Transaksi berhasil dibuat!\nSilakan bayar:\n{invoice}"
            else:
                detail = res.json().get("detail", "Tidak diketahui")
                reply = f"\u274C Gagal topup: {detail}"
        except:
            reply = "\u274C Gagal koneksi ke server."

    requests.post(
        f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
        json={
            "token": TOKEN_ULTRAMSG,
            "to": sender.replace("@c.us", ""),
            "body": reply
        }
    )

    return {"status": "sent"}
