# ===== FILE: app.py =====
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import uuid
import datetime
import requests

from tripay import create_invoice
from config import PRICES, TRIPAY_CALLBACK_URL, INSTANCE_ID, TOKEN_ULTRAMSG  # fix import token

app = FastAPI(title="Mc'D TopUp API")

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
def tripay_callback(data: dict):
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

# ===== PRICE LOOKUP =====
def get_price(provider: str, nominal: str) -> int:
    return PRICES.get(provider, {}).get(nominal)
