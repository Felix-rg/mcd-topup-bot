import hashlib
import hmac
from typing import Any, Dict, Optional

import httpx

from app.config import (
    TRIPAY_API_KEY,
    TRIPAY_BASE_URL,
    TRIPAY_CALLBACK_URL,
    TRIPAY_MERCHANT_CODE,
    TRIPAY_PRIVATE_KEY,
    TRIPAY_RETURN_URL,
)


def create_signature(merchant_ref: str, amount: int) -> str:
    if not TRIPAY_PRIVATE_KEY or not TRIPAY_MERCHANT_CODE:
        return ""

    data = TRIPAY_MERCHANT_CODE + merchant_ref + str(amount)
    return hmac.new(TRIPAY_PRIVATE_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()


def build_invoice_payload(
    order_id: str,
    amount: int,
    method: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    callback_url: Optional[str] = None,
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_method = (method or "").strip().upper()
    resolved_callback_url = (callback_url or TRIPAY_CALLBACK_URL).rstrip("/")
    resolved_return_url = (return_url or TRIPAY_RETURN_URL).rstrip("/") + "/"

    return {
        "method": normalized_method,
        "merchant_ref": order_id,
        "amount": amount,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_phone": customer_phone,
        "order_items": [
            {
                "name": "LIXAFA PROJEK Top-up",
                "price": amount,
                "quantity": 1,
            }
        ],
        "callback_url": resolved_callback_url,
        "return_url": resolved_return_url,
        "signature": create_signature(order_id, amount),
    }


def _get_alternate_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api-sandbox"):
        return normalized[: -len("/api-sandbox")] + "/api"
    if normalized.endswith("/api"):
        return normalized[: -len("/api")] + "/api-sandbox"
    return normalized


async def create_invoice(
    order_id: str,
    amount: int,
    method: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    callback_url: Optional[str] = None,
    return_url: Optional[str] = None,
) -> Dict[str, Any]:
    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY or not TRIPAY_MERCHANT_CODE:
        return {
            "checkout_url": "",
            "qr_url": "",
            "error": "Konfigurasi Tripay belum lengkap",
        }

    payload = build_invoice_payload(
        order_id=order_id,
        amount=amount,
        method=method,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        callback_url=callback_url,
        return_url=return_url,
    )

    headers = {"Authorization": f"Bearer {TRIPAY_API_KEY}"}

    async def _post_invoice(base_url: str) -> Dict[str, Any]:
        url = f"{base_url.rstrip('/')}/transaction/create"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)

            try:
                data = response.json()
            except Exception:
                data = {}

            if response.status_code != 200:
                error_message = data.get("message") if isinstance(data, dict) else response.text
                print(f"TRIPAY HTTP ERROR: {response.status_code}")
                print(response.text)
                return {"checkout_url": "", "qr_url": "", "error": error_message or response.text}

            if data.get("success"):
                tripay_data = data.get("data", {}) or {}
                return {
                    "checkout_url": tripay_data.get("checkout_url", ""),
                    "qr_url": tripay_data.get("qr_url", ""),
                }

            print("===== TRIPAY API ERROR =====")
            print(data)
            error_message = data.get("message") if isinstance(data, dict) else "Tripay menolak request"
            return {"checkout_url": "", "qr_url": "", "error": error_message or data}

    try:
        result = await _post_invoice(TRIPAY_BASE_URL)
        if result.get("checkout_url"):
            return result

        error_text = str(result.get("error", ""))
        alternate_base_url = _get_alternate_base_url(TRIPAY_BASE_URL)

        if alternate_base_url != TRIPAY_BASE_URL and "Invalid API Key" in error_text:
            print(f"TRIPAY FALLBACK: mencoba endpoint alternatif {alternate_base_url}")
            alternate_result = await _post_invoice(alternate_base_url)
            if alternate_result.get("checkout_url"):
                return alternate_result

            return alternate_result

        return result
    except Exception as exc:
        print(f"SISTEM ERROR (Tripay): {exc}")
        return {"checkout_url": "", "qr_url": "", "error": str(exc)}