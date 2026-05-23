import hashlib
import os
from dotenv import load_dotenv
import requests
from config import DIGIFLAZZ_USERNAME, DIGIFLAZZ_KEY

load_dotenv()

def kirim_digiflazz(sku, tujuan, ref_id):
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
        response = requests.post("https://api.digiflazz.com/v1/transaction", json=payload)
        return response.json()
    except Exception as e:
        return {"data": {"message": f"Koneksi Gagal: {str(e)}", "rc": "99"}}

def cek_status_digiflazz(sku, tujuan, ref_id):
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
        response = requests.post("https://api.digiflazz.com/v1/transaction", json=payload)
        return response.json()
    except Exception as e:
        return {"data": {"message": f"Koneksi Gagal: {str(e)}"}}

def get_digiflazz_products():
    url = "https://api.digiflazz.com/v1/price-list"
    # Sign untuk pricelist biasanya pakai kata 'pricelist'
    sign = hashlib.md5((DIGIFLAZZ_USERNAME + DIGIFLAZZ_KEY + "pricelist").encode()).hexdigest()
    
    payload = {
        "cmd": "prepaid",
        "username": DIGIFLAZZ_USERNAME,
        "sign": sign
    }
    
    try:
        response = requests.post(url, json=payload)
        data = response.json()
        
        # --- PAGAR PENGAMAN: Cek apakah 'data' beneran LIST ---
        products = data.get("data")
        
        if isinstance(products, list):
            return products
        else:
            # Kalau bukan list, berarti Digiflazz ngirim pesan error (dict)
            error_msg = products.get("message") if isinstance(products, dict) else "Format data salah"
            print(f"🚨 DIGIFLAZZ ERROR: {error_msg}")
            return error_msg # Balikin string error biar ditangkep admin_routes
            
    except Exception as e:
        print(f"🚨 KONEKSI ERROR: {e}")
        return f"Koneksi ke Digiflazz gagal: {str(e)}"