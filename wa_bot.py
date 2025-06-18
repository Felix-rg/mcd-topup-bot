import requests
from fastapi import FastAPI, Request
from config import INSTANCE_ID, TOKEN_ULTRAMSG
app = FastAPI()

API_TOPUP = "http://127.0.0.1:8000/topup"  # Ganti dengan URL API kamu jika online

# Format pesan yang didukung: topup telkomsel 10k ke 08123456789 via QRIS
def parse_message(text):
    try:
        parts = text.lower().strip().split()
        if parts[0] != "topup":
            return None
        provider = parts[1]
        nominal = parts[2]
        phone = parts[4]
        method = parts[6].upper()
        return {
            "provider": provider,
            "nominal": nominal,
            "phone": phone,
            "method": method
        }
    except:
        return None

@app.post("/webhook")  # Hapus duplikat yang kedua!
async def whatsapp_webhook(req: Request):
    data = await req.json()
    print("📥 DATA MASUK:", data)

    if data.get("event_type") != "message_received":
        return {"status": "ignored"}

    msg_data = data.get("data", {})
    if msg_data.get("fromMe") or msg_data.get("self"):
        return {"status": "ignored"}

    sender = msg_data.get("from")
    message = msg_data.get("body")

    if not sender or not message:
        return {"status": "ignored"}

    reply = "Format salah. Gunakan: topup telkomsel 10k ke 0812xxx via qris"

    parsed = parse_message(message)
    print("🔍 PARSED:", parsed)

    if parsed:
        payload = {
            "phone": parsed["phone"],
            "provider": parsed["provider"],
            "nominal": parsed["nominal"],
            "method": parsed["method"]
        }

        print("📡 Kirim ke API_TOPUP:", payload)
        try:
            response = requests.post(API_TOPUP, json=payload, timeout=5)
            print("💬 API_TOPUP RESPONSE:", response.status_code, response.text)

            if response.ok:
                invoice = response.json().get("invoice_url", "-")
                reply = f"✅ Transaksi berhasil dibuat!\nSilakan bayar:\n{invoice}"
            else:
                detail = response.json().get("detail", "Tidak diketahui")
                reply = f"❌ Gagal topup: {detail}"
        except requests.exceptions.Timeout:
            reply = "⏳ Timeout saat menghubungi server topup."
        except Exception as e:
            reply = f"❌ Error saat konek ke server: {str(e)}"

    print("📨 Kirim balasan ke:", sender.replace("@c.us", ""))
    print("📨 Isi pesan:", reply)
    response_wa = requests.post(
        f"https://api.ultramsg.com/{INSTANCE_ID}/messages/chat",
        json={
            "token": ULTRAMSG_TOKEN,
            "to": sender.replace("@c.us", ""),
            "body": reply
        }
    )
    print("📤 ULTRAMSG RES:", response_wa.status_code, response_wa.text)

    return {"status": "sent"}
