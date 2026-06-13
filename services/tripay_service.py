import httpx
import hashlib
import hmac
from config import TRIPAY_API_KEY, TRIPAY_PRIVATE_KEY, TRIPAY_MERCHANT_CODE, TRIPAY_BASE_URL

def create_signature(merchant_ref, amount):
    data = TRIPAY_MERCHANT_CODE + merchant_ref + str(amount)
    signature = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature

async def create_invoice(order_id, amount, method, customer_name, customer_email, customer_phone):
    base_url = TRIPAY_BASE_URL.rstrip('/')
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                print(f"TRIPAY HTTP ERROR: {response.status_code}")
                print(response.text)
                return None
                
            data = response.json()
            if data.get('success'):
                return {
                    "checkout_url": data['data']['checkout_url'],
                    "qr_url": data['data'].get('qr_url'),
                }
            else:
                print(f"===== TRIPAY API ERROR =====")
                print(data)
                return None
    except Exception as e:
        print(f"SISTEM ERROR (Tripay): {e}")
        return None