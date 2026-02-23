# ===== FILE: app.py =====

from fastapi import FastAPI, HTTPException, Header, Request, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from tripay import create_invoice
from config import PRICES, TRIPAY_CALLBACK_URL, INSTANCE_ID, TOKEN_ULTRAMSG, BASE_URL  # fix import token
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from config import DIGIFLAZZ_USERNAME, DIGIFLAZZ_KEY
from datetime import datetime

import hashlib
import os
import sqlite3
import uuid
import requests
import traceback
import time


app = FastAPI(title="Mc'D TopUp API")


@app.get("/")
def home():
    return FileResponse("web/index.html")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/receipts", StaticFiles(directory="receipts"), name="receipts")

# ===== DATABASE SETUP =====
def get_db():
    conn = sqlite3.connect("db.sqlite3", timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
    return conn, cursor

conn, cursor = get_db()
cursor.execute('''
CREATE TABLE IF NOT EXISTS topup (
    id TEXT PRIMARY KEY,
    sender TEXT,
    phone TEXT,
    provider TEXT,
    nominal TEXT,
    status TEXT,
    invoice_url TEXT,
    created_at TEXT
)
''')
conn.commit()
conn.close()

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
def topup(req: TopUpRequest, sender: str = None):
    conn, cursor = get_db()

    provider = req.provider.lower()
    nominal = req.nominal.lower()

    ALLOWED_METHODS = {"QRIS", "OVO", "DANA", "ShopeePay","INDOMARET"}

    if req.method.upper() not in ALLOWED_METHODS:
        raise HTTPException(status_code=400, detail="Metode pembayaran tidak didukung.")

    if provider not in PRICES:
        raise HTTPException(status_code=400, detail="Provider tidak dikenali.")

    if nominal not in PRICES[provider]:
        raise HTTPException(status_code=400, detail="Nominal tidak tersedia.")

    amount = PRICES[provider][nominal]

    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()

    invoice_url = create_invoice(
        order_id,
        req.phone,
        req.provider,
        req.nominal,
        req.method,
        amount
    )

    if invoice_url is None:
        raise HTTPException(status_code=500, detail="Gagal membuat invoice Tripay.")

    # SIMPAN ORDER BARU
    cursor.execute("""
        INSERT INTO topup (
            id, sender, phone, provider, nominal,
            payment_status, topup_status,
            invoice_url, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_id,
        sender,
        req.phone,
        req.provider,
        req.nominal,
        "UNPAID",
        "WAITING_PAYMENT",
        invoice_url,
        created_at
    ))

    conn.commit()
    conn.close()

    return TopUpResponse(
        id=order_id,
        status="UNPAID",
        message="Invoice berhasil dibuat. Silakan bayar.",
        invoice_url=invoice_url
    )

# GENERATE IMAGE STRUK
def generate_receipt_image(order_id, order_data, filepath):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (400, 350), color=(255, 255, 255))
    d = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 14)  # pastikan font tersedia
    except:
        font = ImageFont.load_default()

    lines = [
        "Mc'D (Mbok Dinah TopUp)",
        "=======================",
        f"Order ID   : {order_data['id']}",
        f"Provider   : {order_data['provider']}",
        f"Nominal    : {order_data['nominal']}",
        f"No. Tujuan : {order_data['phone']}",
        f"Metode     : {order_data['method']}",
        f"Status     : {order_data['status']}",
        f"Waktu      : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        "=======================",
        "Terima kasih telah menggunakan layanan kami!"
    ]

    y = 10
    for line in lines:
        d.text((10, y), line, fill=(0, 0, 0), font=font)
        y += 25

    img.save(filepath)


def kirim_digiflazz(sku, tujuan, ref_id):
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + ref_id).encode()).hexdigest()

    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": sign
    }

    response = requests.post("https://api.digiflazz.com/v1/transaction", json=payload)
    return response.json()


def cek_status_digiflazz(ref_id):
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + ref_id).encode()).hexdigest()

    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": "",
        "customer_no": "",
        "ref_id": ref_id,
        "sign": sign
    }

    response = requests.post("https://api.digiflazz.com/v1/transaction", json=payload)
    return response.json()

def proses_topup_setelah_bayar(order_id, payment_method):
    print("🚀 BACKGROUND PROSES TOPUP:", order_id)
    conn, cursor = get_db()

    order_data = cursor.execute(
        "SELECT id, sender, phone, provider, nominal FROM topup WHERE id = ?",
        (order_id,)
    ).fetchone()

    if not order_data:
        print("ORDER TIDAK ADA DI BACKGROUND")
        return

    sender = order_data[1] if order_data[1] else order_data[2]
    tujuan = order_data[2]
    provider = order_data[3].lower()
    nominal = order_data[4].lower()

    sku_map = {
        ("telkomsel", "5k"): "s5",
        ("telkomsel", "10k"): "s10",
        ("telkomsel", "20k"): "s20",
        ("xl", "5k"): "x5",
        ("xl", "10k"): "x10"
    }

    sku = sku_map.get((provider, nominal))

    if not sku:
        print("SKU TIDAK DITEMUKAN")
        return

    # kirim transaksi ke digiflazz
    result_df = kirim_digiflazz(sku, tujuan, order_id)
    print("DIGIFLAZZ RESPONSE:", result_df)

    # polling status
    for i in range(20):
        time.sleep(6)

        status_df = cek_status_digiflazz(order_id)
        data_df = status_df.get("data", {})
        status_transaksi = data_df.get("status")
        sn = data_df.get("sn")

        print("CEK STATUS DIGIFLAZZ:", status_transaksi)

        if status_transaksi == "Sukses":
            print("TOPUP SUKSES, SN:", sn)
            conn, cursor = get_db()
            cursor.execute(
                "UPDATE topup SET topup_status='success', sn=? WHERE id=?",
                (sn, order_id)
            )
            conn.commit()
            conn.close()

            body = f"Topup berhasil\nNomor: {tujuan}\nSN: {sn}"

            requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={
                    "token": TOKEN_ULTRAMSG,
                    "to": sender,
                    "body": body
                }
            )
            return

        if status_transaksi == "Gagal":
            print("TOPUP GAGAL")

            cursor.execute(
                "UPDATE topup SET topup_status='failed' WHERE id=?",
                (order_id,)
            )
            conn.commit()

            requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={
                    "token": TOKEN_ULTRAMSG,
                    "to": sender,
                    "body": "Topup gagal diproses"
                }
            )

            conn, cursor = get_db()
            cursor.execute(
                "UPDATE topup SET topup_status='pending_provider' WHERE id=?",
                (order_id,)
            )
            conn.commit()
            conn.close()
            return

@app.post("/callback")
async def tripay_callback(req: Request, background_tasks: BackgroundTasks):
    conn, cursor = get_db()
    data = await req.json()
    print("📩 CALLBACK TRIPAY:", data)

    order_id = data.get("merchant_ref")
    status = data.get("status")
    payment_method = data.get("payment_method")

    if not order_id or not status:
        raise HTTPException(status_code=400, detail="Data callback tidak lengkap.")

    row = cursor.execute(
        "SELECT status FROM topup WHERE id=?",
        (order_id,)
    ).fetchone()

    if not row:
        print("ORDER TIDAK ADA")
        return {"success": False}

    old_status = row[0]
    if old_status and old_status.lower() == "paid":
        print("CALLBACK DUPLIKAT")
        return {"success": True}

    cursor.execute(
    "UPDATE topup SET payment_status=? WHERE id=?",
    (status.lower(), order_id)
)
    conn.commit()

    # ambil sender
    row = cursor.execute(
        "SELECT sender, phone FROM topup WHERE id=?",
        (order_id,)
    ).fetchone()

    sender = row[0] if row and row[0] else row[1]

    if status.lower() == "paid":
        # notif bayar berhasil
        cursor.execute(
        "UPDATE topup SET payment_status=? WHERE id=?",
        ("PAID", order_id)
        )
        conn.commit() 

        requests.post(
            f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
            json={
                "token": TOKEN_ULTRAMSG,
                "to": sender,
                "body": "Pembayaran diterima. Topup sedang diproses..."
            }
        )

        # proses digiflazz di background
        background_tasks.add_task(
            proses_topup_setelah_bayar,
            order_id,
            payment_method
        )

    elif status.lower() == "expired":
        cursor.execute(
        "UPDATE topup SET payment_status=? WHERE id=?",
        ("EXPIRED", order_id)
        )
        conn.commit() 
        requests.post(
            f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
            json={
                "token": TOKEN_ULTRAMSG,
                "to": sender,
                "body": "Pesanan expired"
            }
        )

    elif status.lower() == "failed":
        cursor.execute(
        "UPDATE topup SET payment_status=? WHERE id=?",
        ("FAILED", order_id)
        )
        conn.commit() 
        requests.post(
            f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
            json={
                "token": TOKEN_ULTRAMSG,
                "to": sender,
                "body": "Pembayaran gagal"
            }
        )
    conn.close()
    return {"success": True}


@app.get("/topup/{order_id}", response_model=TopUpResponse)
def check_status(order_id: str):
    conn, cursor = get_db()

    cursor.execute("""
        SELECT phone, provider, nominal,
               payment_status, topup_status, invoice_url
        FROM topup
        WHERE id = ?
    """, (order_id,))

    result = cursor.fetchone()
    conn.close()

    if not result:
        raise HTTPException(status_code=404, detail="Order ID tidak ditemukan.")

    phone, provider, nominal, payment_status, topup_status, invoice_url = result

    return TopUpResponse(
        id=order_id,
        status=f"{payment_status} | {topup_status}",
        message=f"Pembayaran: {payment_status}, Topup: {topup_status}",
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
    conn, cursor = get_db()

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
            # Panggil fungsi topup langsung
            encoded = jsonable_encoder(TopUpRequest(**payload))
            response_data = topup(TopUpRequest(**encoded), sender=sender.replace("@c.us", ""))  # panggil fungsi langsung
            invoice = response_data.invoice_url

            # Ambil data dari payload
            order_id = response_data.id
            created_at = datetime.utcnow().isoformat()

            cursor.execute('''
                UPDATE topup SET sender = ? WHERE id = ?
            ''', (sender.replace("@c.us", ""), order_id))
            conn.commit()
            conn.close()

            reply = f"✅ Transaksi berhasil dibuat!\nSilakan bayar:\n{invoice}"

        except Exception as e:
            import traceback
            traceback.print_exc()
            reply = f"❌ Gagal proses topup: {str(e)}"


        wa_response = requests.post(
            f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
        json={
            "token": TOKEN_ULTRAMSG,
            "to": sender.replace("@c.us", ""),
            "body": reply
            }
        )
        print("📤 ULTRAMSG RESPONSE:", wa_response.status_code, wa_response.text)

        return JSONResponse(content={"status": "sent"})


    requests.post(
        f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
        json={
            "token": TOKEN_ULTRAMSG,
            "to": sender.replace("@c.us", ""),
            "body": reply
        }
    )
    


    return {"status": "sent"}



@app.get("/admin")
def admin_page():
    return FileResponse("web/admin.html")

@app.get("/admin-dashboard")
def admin_dashboard():
    return FileResponse("web/admin-dashboard.html")

class AdminLogin(BaseModel):
    username: str
    password: str

@app.post("/admin/login")
def admin_login(data: AdminLogin):
    conn, cursor = get_db()

    row = cursor.execute("""
        SELECT id FROM admin 
        WHERE username=? AND password=?
    """, (data.username, data.password)).fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Login gagal")

    return {"message": "Login berhasil", "token": "ADMIN-LOGIN-OK"}

@app.get("/admin/api/orders")
def admin_orders():
    conn, cursor = get_db()
    cursor.execute("SELECT phone, nominal, status FROM topup ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()

    return [
        {"phone": r[0], "nominal": r[1], "status": r[2]}
        for r in rows
    ]


@app.get("/admin/api/pending")
def admin_pending():
    conn, cursor = get_db()
    cursor.execute("SELECT phone, nominal, status FROM topup WHERE status='pending'")
    rows = cursor.fetchall()
    conn.close()

    return [
        {"phone": r[0], "nominal": r[1], "status": r[2]}
        for r in rows
    ]


@app.get("/admin/api/success")
def admin_success():
    conn, cursor = get_db()
    cursor.execute("SELECT phone, nominal, status FROM topup WHERE status='success'")
    rows = cursor.fetchall()
    conn.close()

    return [
        {"phone": r[0], "nominal": r[1], "status": r[2]}
        for r in rows
    ]

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"Request : {request.method} {request.url}")
    response = await call_next(request)
    print(f"RESPONSE: {response.status_code}")
    return response
