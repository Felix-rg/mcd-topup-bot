import hashlib
import httpx

from app.config import DIGIFLAZZ_API_KEY, DIGIFLAZZ_BASE_URL, DIGIFLAZZ_USERNAME


async def _call_digiflazz(payload: dict, endpoint: str, timeout: float = 15.0):
    if not DIGIFLAZZ_USERNAME or not DIGIFLAZZ_API_KEY:
        return {"data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"}}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{DIGIFLAZZ_BASE_URL}/{endpoint}", json=payload)
            return response.json()
    except Exception as exc:
        print(f"🚨 DIGIFLAZZ ERROR ({endpoint}): {exc}")
        return {"data": {"message": f"Koneksi Gagal: {str(exc)}", "rc": "99"}}


async def kirim_digiflazz(sku, tujuan, ref_id):
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_API_KEY + ref_id).encode()).hexdigest()
    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": sign,
    }
    return await _call_digiflazz(payload, "transaction")


async def cek_status_digiflazz(sku, tujuan, ref_id):
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_API_KEY + ref_id).encode()).hexdigest()
    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": sign,
    }
    return await _call_digiflazz(payload, "transaction")


async def get_digiflazz_products():
    url = f"{DIGIFLAZZ_BASE_URL}/price-list"
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_API_KEY + "pricelist").encode()).hexdigest()

    payload = {
        "cmd": "prepaid",
        "username": DIGIFLAZZ_USERNAME,
        "sign": sign,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload)
            data = response.json()

            products = data.get("data")
            if isinstance(products, list):
                return products
            if isinstance(products, dict):
                error_msg = products.get("message", "Format data salah")
                print(f"🚨 DIGIFLAZZ ERROR (produk): {error_msg}")
                return error_msg
            return "Format data salah"
    except Exception as exc:
        print(f"🚨 KONEKSI ERROR (produk): {exc}")
        return f"Koneksi ke Digiflazz gagal: {str(exc)}"