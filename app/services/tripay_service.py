import hashlib
import hmac
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
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
from app.services.provider_settings import get_tripay_config


logger = logging.getLogger(__name__)


TRIPAY_INVOICE_OUTCOME_SUCCESS = "SUCCESS"
TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE = "DEFINITIVE_FAILURE"
TRIPAY_INVOICE_OUTCOME_NOT_SENT = "NOT_SENT"
TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN = "SENT_UNKNOWN"


def classify_tripay_invoice_response(response: Any) -> str:
    """Classify invoice creation without treating an ambiguous POST as failed.

    A timeout or an unreadable response can happen after Tripay accepts the
    merchant reference.  Those cases must retain the local pending order and
    be reconciled by that same merchant reference rather than being retried as
    a fresh invoice.
    """

    if not isinstance(response, dict):
        return TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN
    if response.get("checkout_url"):
        return TRIPAY_INVOICE_OUTCOME_SUCCESS

    adapter_outcome = str(response.get("_invoice_outcome") or "").strip().upper()
    if adapter_outcome in {
        TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
        TRIPAY_INVOICE_OUTCOME_NOT_SENT,
        TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN,
    }:
        return adapter_outcome
    return TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN


@dataclass(frozen=True)
class TripayFeeBreakdown:
    customer_fee: int = 0
    merchant_fee: int = 0
    total_fee: int = 0
    minimum_fee: int = 0
    maximum_fee: Optional[int] = None
    flat_fee: int = 0
    percent_fee: int = 0
    valid: bool = False
    source: str = "missing"


def _decimal_number(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, (bool, dict, list, tuple, set)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def _rupiah(value: Any) -> Optional[int]:
    number = _decimal_number(value)
    if number is None:
        return None
    return int(number.to_integral_value(rounding=ROUND_CEILING))


def _fee_payload_item(payload: Any) -> Dict[str, Any]:
    current = payload
    if isinstance(current, dict) and "data" in current:
        current = current.get("data")
    if isinstance(current, dict) and set(current).isdisjoint(
        {
            "fee",
            "fee_customer",
            "fee_merchant",
            "customer_fee",
            "merchant_fee",
            "total_fee",
            "minimum_fee",
            "maximum_fee",
            "flat_fee",
            "percent_fee",
        }
    ):
        nested = current.get("data") or current.get("channels")
        if nested is not None:
            current = nested
    if isinstance(current, list):
        current = next((item for item in current if isinstance(item, dict)), {})
    return current if isinstance(current, dict) else {}


def _fee_rule(rule: Any, amount: int) -> tuple[int, int, int, bool]:
    if not isinstance(rule, dict):
        return 0, 0, 0, False

    flat_number = _rupiah(rule.get("flat"))
    percent_rate = _decimal_number(rule.get("percent"))
    recognized = flat_number is not None or percent_rate is not None
    flat_fee = flat_number or 0
    percent_fee = 0
    if percent_rate is not None and amount > 0:
        percent_fee = int(
            (Decimal(amount) * percent_rate / Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
        )
    return flat_fee + percent_fee, flat_fee, percent_fee, recognized


def parse_tripay_fee(payload: Any, *, amount: Any = None) -> TripayFeeBreakdown:
    """Normalize Tripay fee response variants without floating-point Rupiah math."""

    item = _fee_payload_item(payload)
    if not item:
        return TripayFeeBreakdown()

    resolved_amount = _rupiah(amount)
    if resolved_amount is None:
        resolved_amount = _rupiah(item.get("amount")) or 0

    minimum_fee = _rupiah(item.get("minimum_fee")) or 0
    maximum_fee = _rupiah(item.get("maximum_fee"))
    total_value = item.get("total_fee")

    nested_customer = None
    nested_merchant = None
    if isinstance(total_value, dict):
        nested_customer = _rupiah(total_value.get("customer"))
        nested_merchant = _rupiah(total_value.get("merchant"))

    customer_scalar = _rupiah(item.get("fee_customer"))
    if customer_scalar is None:
        customer_scalar = _rupiah(item.get("customer_fee"))
    merchant_scalar = _rupiah(item.get("fee_merchant"))
    if merchant_scalar is None:
        merchant_scalar = _rupiah(item.get("merchant_fee"))

    customer_rule = item.get("fee_customer") if isinstance(item.get("fee_customer"), dict) else None
    merchant_rule = item.get("fee_merchant") if isinstance(item.get("fee_merchant"), dict) else None
    generic_rule = item.get("fee") if isinstance(item.get("fee"), dict) else None
    if customer_rule is None and generic_rule is None and (
        item.get("flat_fee") is not None or item.get("percent_fee") is not None
    ):
        generic_rule = {"flat": item.get("flat_fee"), "percent": item.get("percent_fee")}

    customer_from_rule, flat_fee, percent_fee, customer_rule_valid = _fee_rule(
        customer_rule or generic_rule,
        resolved_amount,
    )
    merchant_from_rule, _, _, merchant_rule_valid = _fee_rule(merchant_rule, resolved_amount)

    customer_fee = 0
    customer_recognized = False
    source = "missing"
    if nested_customer is not None:
        customer_fee = nested_customer
        customer_recognized = True
        source = "total_fee.customer"
    elif customer_scalar is not None:
        customer_fee = customer_scalar
        customer_recognized = True
        source = "fee_customer"
    elif customer_rule_valid:
        customer_fee = customer_from_rule
        customer_recognized = True
        source = "fee_customer_rule"

    merchant_fee = 0
    merchant_recognized = False
    if nested_merchant is not None:
        merchant_fee = nested_merchant
        merchant_recognized = True
    elif merchant_scalar is not None:
        merchant_fee = merchant_scalar
        merchant_recognized = True
    elif merchant_rule_valid:
        merchant_fee = merchant_from_rule
        merchant_recognized = True

    legacy_total = _rupiah(total_value)
    if not customer_recognized and legacy_total is not None:
        customer_fee = (
            max(0, legacy_total - merchant_fee)
            if merchant_recognized
            else legacy_total
        )
        customer_recognized = True
        source = "total_fee"

    if customer_recognized and resolved_amount > 0:
        if minimum_fee and customer_fee < minimum_fee:
            customer_fee = minimum_fee
            source = f"{source}+minimum_fee"
        if maximum_fee is not None and customer_fee > maximum_fee:
            customer_fee = maximum_fee
            source = f"{source}+maximum_fee"

    valid = customer_recognized or merchant_recognized
    total_fee = customer_fee + merchant_fee if valid else 0
    return TripayFeeBreakdown(
        customer_fee=customer_fee,
        merchant_fee=merchant_fee,
        total_fee=total_fee,
        minimum_fee=minimum_fee,
        maximum_fee=maximum_fee,
        flat_fee=flat_fee,
        percent_fee=percent_fee,
        valid=valid,
        source=source,
    )


def _hmac_sha256(private_key: str, value: str) -> str:
    if not private_key:
        return ""
    return hmac.new(private_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_signature(
    merchant_ref: str,
    amount: int,
    *,
    private_key: Optional[str] = None,
    merchant_code: Optional[str] = None,
) -> str:
    resolved_private_key = private_key if private_key is not None else TRIPAY_PRIVATE_KEY
    resolved_merchant_code = merchant_code if merchant_code is not None else TRIPAY_MERCHANT_CODE

    if not resolved_private_key or not resolved_merchant_code:
        return ""

    data = resolved_merchant_code + merchant_ref + str(amount)
    return _hmac_sha256(resolved_private_key, data)


def create_open_payment_signature(
    method: str,
    merchant_ref: str,
    *,
    private_key: Optional[str] = None,
    merchant_code: Optional[str] = None,
) -> str:
    resolved_private_key = private_key if private_key is not None else TRIPAY_PRIVATE_KEY
    resolved_merchant_code = merchant_code if merchant_code is not None else TRIPAY_MERCHANT_CODE
    if not resolved_private_key or not resolved_merchant_code:
        return ""
    return _hmac_sha256(resolved_private_key, resolved_merchant_code + method + merchant_ref)


def create_ewallet_signature(
    wallet_type: str,
    mobile_phone: str,
    *,
    private_key: Optional[str] = None,
    merchant_code: Optional[str] = None,
) -> str:
    resolved_private_key = private_key if private_key is not None else TRIPAY_PRIVATE_KEY
    resolved_merchant_code = merchant_code if merchant_code is not None else TRIPAY_MERCHANT_CODE
    if not resolved_private_key or not resolved_merchant_code:
        return ""
    return _hmac_sha256(resolved_private_key, resolved_merchant_code + wallet_type + mobile_phone)


def build_invoice_payload(
    order_id: str,
    amount: int,
    method: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    callback_url: Optional[str] = None,
    return_url: Optional[str] = None,
    expired_time: Optional[int] = None,
    order_items: Optional[list[Dict[str, Any]]] = None,
    private_key: Optional[str] = None,
    merchant_code: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_method = (method or "").strip().upper()
    resolved_callback_url = (callback_url or TRIPAY_CALLBACK_URL).rstrip("/")
    resolved_return_url = (return_url or TRIPAY_RETURN_URL).rstrip("/") + "/"

    payload = {
        "method": normalized_method,
        "merchant_ref": order_id,
        "amount": amount,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_phone": customer_phone,
        "order_items": order_items or [
            {
                "name": "LIXAFA PROJEK Top-up",
                "price": amount,
                "quantity": 1,
            }
        ],
        "callback_url": resolved_callback_url,
        "return_url": resolved_return_url,
        "signature": create_signature(
            order_id,
            amount,
            private_key=private_key,
            merchant_code=merchant_code,
        ),
    }
    if expired_time:
        payload["expired_time"] = int(expired_time)
    return payload


def _get_alternate_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api-sandbox"):
        return normalized[: -len("/api-sandbox")] + "/api"
    if normalized.endswith("/api"):
        return normalized[: -len("/api")] + "/api-sandbox"
    return normalized


async def _runtime_config() -> Dict[str, str]:
    config = await get_tripay_config()
    return {
        "api_key": config.get("api_key") or TRIPAY_API_KEY,
        "private_key": config.get("private_key") or TRIPAY_PRIVATE_KEY,
        "merchant_code": config.get("merchant_code") or TRIPAY_MERCHANT_CODE,
        "base_url": (config.get("base_url") or TRIPAY_BASE_URL).rstrip("/"),
    }


async def _tripay_request(
    method: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    form_payload: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
    allow_open_payment: bool = False,
) -> Dict[str, Any]:
    config = await _runtime_config()
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not api_key:
        return {"success": False, "message": "Konfigurasi Tripay belum lengkap", "data": None}

    endpoint_path = endpoint.lstrip("/")
    if allow_open_payment and base_url.endswith("/api-sandbox"):
        # Open Payment and E-Wallet APIs are documented for production only.
        base_url = base_url[: -len("/api-sandbox")] + "/api"

    url = f"{base_url.rstrip('/')}/{endpoint_path}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                response = await client.get(url, params=params or {}, headers=headers)
            else:
                if form_payload is not None:
                    response = await client.post(url, data=form_payload, headers=headers)
                else:
                    response = await client.post(url, json=json_payload or {}, headers=headers)

        try:
            data = response.json()
        except Exception:
            logger.warning(
                "Tripay mengembalikan respons non-JSON endpoint=%s status=%s",
                endpoint,
                response.status_code,
            )
            data = {
                "success": False,
                "message": "Layanan pembayaran sedang tidak tersedia",
                "data": None,
            }

        if response.status_code >= 400 and isinstance(data, dict):
            data["success"] = False
            data.setdefault("message", "Layanan pembayaran sedang tidak tersedia")
        return data
    except Exception as exc:
        logger.warning(
            "Tripay request gagal endpoint=%s error_type=%s",
            endpoint,
            type(exc).__name__,
        )
        return {
            "success": False,
            "message": "Layanan pembayaran sedang tidak tersedia",
            "data": None,
        }


async def get_payment_channels() -> Dict[str, Any]:
    return await _tripay_request("GET", "merchant/payment-channel", timeout=12.0)


async def calculate_fee(method: str, amount: int) -> Dict[str, Any]:
    return await _tripay_request(
        "GET",
        "merchant/fee-calculator",
        params={"code": (method or "").strip().upper(), "amount": int(amount)},
        timeout=10.0,
    )


async def get_payment_instruction(
    method: str,
    *,
    pay_code: Optional[str] = None,
    amount: Optional[int] = None,
    allow_html: int = 1,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"code": (method or "").strip().upper(), "allow_html": int(allow_html)}
    if pay_code:
        params["pay_code"] = pay_code
    if amount is not None:
        params["amount"] = int(amount)
    return await _tripay_request("GET", "payment/instruction", params=params, timeout=10.0)


async def get_transaction_detail(reference: str) -> Dict[str, Any]:
    return await _tripay_request("GET", "transaction/detail", params={"reference": reference}, timeout=12.0)


async def check_transaction_status(reference: str) -> Dict[str, Any]:
    return await _tripay_request("GET", "transaction/check-status", params={"reference": reference}, timeout=12.0)


async def list_merchant_transactions(**filters: Any) -> Dict[str, Any]:
    params = {key: value for key, value in filters.items() if value not in {None, ""}}
    return await _tripay_request("GET", "merchant/transactions", params=params, timeout=15.0)


async def create_open_payment(method: str, merchant_ref: str, customer_name: str = "") -> Dict[str, Any]:
    config = await _runtime_config()
    normalized_method = (method or "").strip().upper()
    payload = {
        "method": normalized_method,
        "merchant_ref": merchant_ref,
        "customer_name": customer_name,
        "signature": create_open_payment_signature(
            normalized_method,
            merchant_ref,
            private_key=config["private_key"],
            merchant_code=config["merchant_code"],
        ),
    }
    return await _tripay_request(
        "POST",
        "open-payment/create",
        form_payload=payload,
        timeout=15.0,
        allow_open_payment=True,
    )


async def get_open_payment_detail(uuid: str) -> Dict[str, Any]:
    return await _tripay_request(
        "GET",
        f"open-payment/{uuid}/detail",
        timeout=12.0,
        allow_open_payment=True,
    )


async def list_open_payment_transactions(uuid: str, **filters: Any) -> Dict[str, Any]:
    params = {key: value for key, value in filters.items() if value not in {None, ""}}
    return await _tripay_request(
        "GET",
        f"open-payment/{uuid}/transactions",
        params=params,
        timeout=15.0,
        allow_open_payment=True,
    )


async def link_ewallet(wallet_type: str, mobile_phone: str) -> Dict[str, Any]:
    config = await _runtime_config()
    normalized_wallet = (wallet_type or "").strip().upper()
    payload = {
        "wallet_type": normalized_wallet,
        "mobile_phone": mobile_phone,
        "signature": create_ewallet_signature(
            normalized_wallet,
            mobile_phone,
            private_key=config["private_key"],
            merchant_code=config["merchant_code"],
        ),
    }
    return await _tripay_request(
        "POST",
        "ewallet/link",
        form_payload=payload,
        timeout=15.0,
        allow_open_payment=True,
    )


async def unlink_ewallet(wallet_type: str, mobile_phone: str) -> Dict[str, Any]:
    config = await _runtime_config()
    normalized_wallet = (wallet_type or "").strip().upper()
    payload = {
        "wallet_type": normalized_wallet,
        "mobile_phone": mobile_phone,
        "signature": create_ewallet_signature(
            normalized_wallet,
            mobile_phone,
            private_key=config["private_key"],
            merchant_code=config["merchant_code"],
        ),
    }
    return await _tripay_request(
        "POST",
        "ewallet/unlink",
        form_payload=payload,
        timeout=15.0,
        allow_open_payment=True,
    )


async def get_ewallet_detail(wallet_type: str, mobile_phone: str) -> Dict[str, Any]:
    return await _tripay_request(
        "GET",
        "ewallet/detail",
        params={"wallet_type": (wallet_type or "").strip().upper(), "mobile_phone": mobile_phone},
        timeout=12.0,
        allow_open_payment=True,
    )


async def create_invoice(
    order_id: str,
    amount: int,
    method: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    callback_url: Optional[str] = None,
    return_url: Optional[str] = None,
    expired_time: Optional[int] = None,
    order_items: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    config = await _runtime_config()
    api_key = config["api_key"]
    private_key = config["private_key"]
    merchant_code = config["merchant_code"]
    base_url = config["base_url"]

    if not api_key or not private_key or not merchant_code:
        return {
            "checkout_url": "",
            "qr_url": "",
            "error": "Konfigurasi Tripay belum lengkap",
            "_invoice_outcome": TRIPAY_INVOICE_OUTCOME_NOT_SENT,
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
        expired_time=expired_time,
        order_items=order_items,
        private_key=private_key,
        merchant_code=merchant_code,
    )

    headers = {"Authorization": f"Bearer {api_key}"}

    async def _post_invoice(base_url: str) -> Dict[str, Any]:
        url = f"{base_url.rstrip('/')}/transaction/create"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)

            try:
                data = response.json()
            except Exception:
                logger.warning("Tripay invoice memberi respons yang tidak dapat dibaca status=%s", response.status_code)
                return {
                    "checkout_url": "",
                    "qr_url": "",
                    "error": "Pembayaran belum dapat dipastikan",
                    "_invoice_outcome": TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN,
                }

            if response.status_code != 200:
                error_message = data.get("message") if isinstance(data, dict) else response.text
                definitive_failure = 400 <= response.status_code < 500 and response.status_code != 409
                logger.warning(
                    "Tripay invoice ditolak status=%s",
                    response.status_code,
                )
                return {
                    "checkout_url": "",
                    "qr_url": "",
                    "error": "Pembayaran belum dapat dibuat",
                    "_invoice_outcome": (
                        TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE
                        if definitive_failure
                        else TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN
                    ),
                    "_retry_alternate": definitive_failure and "Invalid API Key" in str(error_message or ""),
                }

            if data.get("success"):
                tripay_data = data.get("data", {}) or {}
                fee = parse_tripay_fee(tripay_data, amount=tripay_data.get("amount") or amount)
                return {
                    "reference": tripay_data.get("reference", ""),
                    "merchant_ref": tripay_data.get("merchant_ref", order_id),
                    "payment_method": tripay_data.get("payment_method", ""),
                    "payment_name": tripay_data.get("payment_name", ""),
                    "pay_code": str(tripay_data.get("pay_code") or ""),
                    "pay_url": tripay_data.get("pay_url", ""),
                    "checkout_url": tripay_data.get("checkout_url", ""),
                    "qr_url": tripay_data.get("qr_url", ""),
                    "qr_string": tripay_data.get("qr_string", ""),
                    "fee_merchant": fee.merchant_fee,
                    "fee_customer": fee.customer_fee,
                    "total_fee": fee.total_fee,
                    "amount_received": tripay_data.get("amount_received", 0),
                    "expired_time": tripay_data.get("expired_time") or tripay_data.get("expired_at"),
                    "expired_at": tripay_data.get("expired_at"),
                    "instructions": tripay_data.get("instructions", []),
                    "raw": tripay_data,
                    "_invoice_outcome": TRIPAY_INVOICE_OUTCOME_SUCCESS,
                }

            logger.warning("Tripay tidak menerima pembuatan invoice")
            error_message = data.get("message") if isinstance(data, dict) else "Tripay menolak request"
            return {
                "checkout_url": "",
                "qr_url": "",
                "error": "Pembayaran belum dapat dibuat",
                "_invoice_outcome": TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
                "_retry_alternate": "Invalid API Key" in str(error_message or ""),
            }

    try:
        result = await _post_invoice(base_url)
        if result.get("checkout_url"):
            return result

        retry_alternate = bool(result.pop("_retry_alternate", False))
        alternate_base_url = _get_alternate_base_url(base_url)

        if alternate_base_url != base_url and retry_alternate:
            logger.info("Mencoba endpoint Tripay alternatif untuk pembuatan invoice")
            alternate_result = await _post_invoice(alternate_base_url)
            if alternate_result.get("checkout_url"):
                return alternate_result

            alternate_result.pop("_retry_alternate", None)
            return alternate_result

        return result
    except Exception as exc:
        logger.warning("Pembuatan invoice Tripay gagal error_type=%s", type(exc).__name__)
        return {
            "checkout_url": "",
            "qr_url": "",
            "error": "Pembayaran belum dapat dipastikan",
            "_invoice_outcome": TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN,
        }
