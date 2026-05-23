# ===== FILE: tripay.py =====
import hashlib
import hmac
import requests
from config import TRIPAY_API_KEY, TRIPAY_MERCHANT_CODE, TRIPAY_CALLBACK_URL, TRIPAY_BASE_URL, TRIPAY_PRIVATE_KEY

def create_signature(order_id, amount):
    signature_str = TRIPAY_MERCHANT_CODE + order_id + str(amount)
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        signature_str.encode(),
        hashlib.sha256
    ).hexdigest()

def create_invoice(order_id, phone, provider, nominal, method, amount):
    signature = create_signature(order_id, amount)

    payload = {
        "method": method,
        "merchant_ref": order_id,
        "amount": amount,
        "customer_name": "User Mbok Dinah",
        "customer_email": "user@mbokdinah.com",
        "customer_phone": phone,
        "order_items": [
            {
                "sku": f"{provider}-{nominal}",
                "name": f"TopUp {nominal} {provider}",
                "price": amount,
                "quantity": 1
            }
        ],
        "callback_url": TRIPAY_CALLBACK_URL,
        "return_url": "https://yourdomain.com/sukses",
        "signature": signature
    }

    headers = {
        "Authorization": f"Bearer {TRIPAY_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(TRIPAY_BASE_URL, json=payload, headers=headers)

    print("===== TRIPAY RESPONSE =====")
    print("Status code:", response.status_code)
    print("Response text:", response.text)
    print("===========================")

    try:
        result = response.json()
    except Exception as e:
        print("Gagal parsing JSON:", e)
        return None

    if response.status_code == 200 and result.get("success"):
        return result["data"]["checkout_url"]
    else:
        return None
