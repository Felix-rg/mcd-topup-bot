import hashlib
import httpx
from config import DIGIFLAZZ_USERNAME, DIGIFLAZZ_KEY

async def kirim_digiflazz(sku, tujuan, ref_id):
    sign = hashlib.md5(
        (DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + ref_id).encode()
    ).hexdigest()

    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": sign
    }

    try:
        # Gunakan AsyncClient dengan timeout 15 detik
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post("https://api.digiflazz.com/v1/transaction", json=payload)
            return response.json()
    except Exception as e:
        print(f"🚨 DIGIFLAZZ ERROR (kirim): {e}")
        return {"data": {"message": f"Koneksi Gagal: {str(e)}", "rc": "99"}}

async def cek_status_digiflazz(sku, tujuan, ref_id):
    sign = hashlib.md5(
        (DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + ref_id).encode()
    ).hexdigest()

    payload = {
        "username": DIGIFLAZZ_USERNAME,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": sign
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post("https://api.digiflazz.com/v1/transaction", json=payload)
            return response.json()
    except Exception as e:
        print(f"🚨 DIGIFLAZZ ERROR (cek status): {e}")
        return {"data": {"message": f"Koneksi Gagal: {str(e)}"}}

async def get_digiflazz_products():
    url = "https://api.digiflazz.com/v1/price-list"
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + "pricelist").encode()).hexdigest()
    
    payload = {
        "cmd": "prepaid",
        "username": DIGIFLAZZ_USERNAME,
        "sign": sign
    }
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            
            products = data.get("data")
            if isinstance(products, list):
                return products
            else:
                error_msg = products.get("message") if isinstance(products, dict) else "Format data salah"
                print(f"🚨 DIGIFLAZZ ERROR (produk): {error_msg}")
                return error_msg
            
    except Exception as e:
        print(f"🚨 KONEKSI ERROR (produk): {e}")
        return f"Koneksi ke Digiflazz gagal: {str(e)}"