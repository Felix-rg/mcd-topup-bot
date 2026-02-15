# ===== FILE: app.py =====
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import os
from pydantic import BaseModel
import sqlite3
import uuid
import datetime
import requests
import traceback
import time

from datetime import datetime

from tripay import create_invoice
from config import PRICES, TRIPAY_CALLBACK_URL, INSTANCE_ID, TOKEN_ULTRAMSG, BASE_URL  # fix import token
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
conn = sqlite3.connect("db.sqlite3", check_same_thread=False)
cursor = conn.cursor()
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
        return PRICES.get(provider, {}).get(nominal)

    order_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    amount = get_price(provider, nominal)


    invoice_url = create_invoice(order_id, req.phone, req.provider, req.nominal, req.method, amount)
    if invoice_url is None:
        raise HTTPException(status_code=500, detail="Gagal membuat invoice Tripay.")

    # Simpan ke database
    cursor.execute('''
        INSERT INTO topup (id, sender, phone, provider, nominal, status, invoice_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (order_id, sender, req.phone, req.provider, req.nominal, "pending", invoice_url, created_at))
    conn.commit()

    return TopUpResponse(
        id=order_id,
        status="pending",
        message=f"Silakan selesaikan pembayaran untuk top-up {req.phone}",
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

@app.post("/callback")
async def tripay_callback(req: Request):
    data = await req.json()

# Jika kamu mau log
    print("📩 CALLBACK DARI TRIPAY:", data)

    order_id = data.get("merchant_ref")
    status = data.get("status")

    if not order_id or not status:
        raise HTTPException(status_code=400, detail="Data callback tidak lengkap.")

    # Update status database
    cursor.execute("UPDATE topup SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()

    # Ambil nomor HP dari DB untuk kirim notifikasi WA

    result = cursor.execute("SELECT sender FROM topup WHERE id = ?", (order_id,)).fetchone()
    if result:
        sender = result[0]
        if status.lower() == "paid":
            order_data = cursor.execute("SELECT id, sender, phone, provider, nominal, status, sender FROM topup WHERE id = ?", (order_id,)).fetchone()
            if order_data:
                order_dict = {
                    "id": order_data[0],
                    "sender": order_data[1],
                     "phone": order_data[2],
                    "provider": order_data[3],
                    "nominal": order_data[4],
                    "status": order_data[5],
                    "method": data.get("payment_method") or "Tidak diketahui"
                }
                filename = f"receipt_{order_id}.png"
                receipt_path = f"receipts/{filename}"
                generate_receipt_image(order_id, order_dict, receipt_path)
                time.sleep(1) # kasih waktu 1 detik supaya file siap


                body = f"✅ Pembayaran untuk pesanan {order_id} berhasil.\nStruk akan dikirimkan dalam bentuk gambar."
                res = requests.post(
                    f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat", 
                    json={
                        "token": TOKEN_ULTRAMSG,
                        "to": sender,
                        "body": body
                    }
                )
                print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)

                    # Kirim gambar ke pengirim
                image_url = f"https://67b1-103-180-59-146.ngrok-free.app/receipts/{filename}"
                print("📁 STRUK DISIMPAN DI:", receipt_path)
                print("🔗 URL:", image_url)
                print("CEK FILE ADA : ", os.path.exists(receipt_path))

                res_upload = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/image",
                json={
                    "token": TOKEN_ULTRAMSG,
                    "to": sender,
                    "image": image_url,
                    "caption": f"✅ Pembayaran untuk pesanan {order_id} berhasil!\nBerikut struk pembayaran Anda:"
                }
            )
            print("📤 STRUK TERKIRIM:", res_upload.status_code, res_upload.text)

        elif status.lower() == "expired":
            body = f"⚠️ Pesanan {order_id} telah *kadaluarsa* karena belum dibayar dalam batas waktu."
            res = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={"token": TOKEN_ULTRAMSG, "to": sender, "body": body}
                )
            print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)
        elif status.lower() == "failed":
            body = f"❌ Pesanan {order_id} *gagal* diproses oleh sistem pembayaran."
            res = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={"token": TOKEN_ULTRAMSG, "to": sender, "body": body}
                )
            print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)
        elif status.lower() == "refund":
            body = f"💸 Dana untuk pesanan {order_id} telah *dikembalikan*. Silakan cek rekening/ewallet Anda."
            res = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={"token": TOKEN_ULTRAMSG, "to": sender, "body": body}
                )
            print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)
        else:
            body = f"ℹ️ Pembaruan status untuk pesanan {order_id}: {status}"
            res = requests.post(
                f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
                json={"token": TOKEN_ULTRAMSG, "to": sender, "body": body}
                )
            print("📤 ULTRAMSG CALLBACK RES:", res.status_code, res.text)
        print("🧪 MENCARI NOMOR HP UNTUK ORDER:", order_id)
        print("📞 HASIL:", result)

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

@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"Request : {request.method} {request.url}")
    response = await call_next(request)
    print(f"RESPONSE: {response.status_code}")
    return response
