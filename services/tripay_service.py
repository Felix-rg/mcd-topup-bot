import requests
import hashlib
import hmac
import os
from dotenv import load_dotenv

# Load data dari file .env
load_dotenv()

# Ambil variabel dari .env
TRIPAY_API_KEY = os.getenv("TRIPAY_API_KEY")
TRIPAY_PRIVATE_KEY = os.getenv("TRIPAY_PRIVATE_KEY")
TRIPAY_MERCHANT_CODE = os.getenv("TRIPAY_MERCHANT_CODE")
# Gunakan nama yang sesuai dengan .env kamu
TRIPAY_URL = os.getenv("TRIPAY_BASE_URL") 

def create_signature(merchant_ref, amount):
    # Rumus Signature Tripay: MerchantCode + MerchantRef + Amount
    data = TRIPAY_MERCHANT_CODE + merchant_ref + str(amount)
    signature = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature

def create_invoice(order_id, amount, method, customer_name, customer_email, customer_phone):
    # Pastikan URL bersih dari double slash atau kurang slash
    base_url = TRIPAY_URL.rstrip('/')
    url = f"{base_url}/transaction/create"
    
    payload = {
        'method': method,
        'merchant_ref': order_id,
        'amount': amount,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'customer_phone': customer_phone,
        'order_items': [
            {
                'name': 'Topup Game/Pulsa',
                'price': amount,
                'quantity': 1
            }
        ],
        'signature': create_signature(order_id, amount)
    }

    headers = {'Authorization': f'Bearer {TRIPAY_API_KEY}'}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        # Jika Tripay kasih error 404/500 dalam bentuk HTML, ini akan ketahuan
        if response.status_code != 200:
            print(f"TRIPAY HTTP ERROR: {response.status_code}")
            print(response.text)
            return None
            
        data = response.json()
        if data.get('success'):
            return {
                        "checkout_url": data['data']['checkout_url'],
                        "qr_url": data['data'].get('qr_url'), # Khusus metode QRIS
                    }
        else:
            print(f"===== TRIPAY API ERROR =====")
            print(data)
            return None
    except Exception as e:
        print(f"SISTEM ERROR: {e}")
        return None