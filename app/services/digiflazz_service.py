import hashlib
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import DIGIFLAZZ_API_KEY, DIGIFLAZZ_BASE_URL, DIGIFLAZZ_USERNAME
from app.services.provider_settings import get_digiflazz_config


logger = logging.getLogger(__name__)


# These are local outcomes, not raw Digiflazz status values.  In particular,
# a transport error after a POST has started must not be treated as proof that
# the provider did not receive the transaction.
DIGIFLAZZ_OUTCOME_SUCCESS = "SUCCESS"
DIGIFLAZZ_OUTCOME_PENDING = "PENDING_PROVIDER"
DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE = "DEFINITIVE_FAILURE"
DIGIFLAZZ_OUTCOME_RETRYABLE_NOT_SENT = "RETRYABLE_NOT_SENT"
DIGIFLAZZ_OUTCOME_SENT_UNKNOWN = "SENT_UNKNOWN"

# Digiflazz documents these response codes as a failed response.  Timeout,
# duplicate ref_id, and timeout from the biller are deliberately excluded:
# they do not prove that the original external side effect did not happen.
_DIGIFLAZZ_DEFINITIVE_FAILURE_CODES = frozenset(
    {
        "02",
        "40", "41", "42", "43", "44", "45", "47",
        "50", "51", "52", "53", "54", "55", "56", "57", "58", "59", "60",
        "61", "62", "63", "64", "65", "66", "67", "68", "69",
        "71", "72", "73", "74",
        "80", "81", "82", "83", "84", "85", "86", "87", "88",
    }
)
_DIGIFLAZZ_AMBIGUOUS_FAILURE_CODES = frozenset({"01", "49", "70"})


def digiflazz_response_data(response: Any) -> Dict[str, Any]:
    """Return a safely normalized buyer-transaction response payload."""

    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else {}


def classify_digiflazz_transaction_response(response: Any) -> str:
    """Classify a Digiflazz transaction response without making retry unsafe.

    `NOT_SENT` is reserved for adapter failures proved to occur before the
    external request (currently local configuration validation).  All network
    and parsing failures after attempting HTTP are `SENT_UNKNOWN` and require
    reconciliation by the same ref_id before another external POST is allowed.
    A raw `Gagal` value without a documented, unambiguous response code is also
    handled conservatively as unknown.
    """

    if not isinstance(response, dict):
        return DIGIFLAZZ_OUTCOME_SENT_UNKNOWN

    adapter_outcome = str(response.get("_provider_outcome") or "RESPONDED").strip().upper()
    if adapter_outcome == "NOT_SENT":
        return DIGIFLAZZ_OUTCOME_RETRYABLE_NOT_SENT
    if adapter_outcome in {"SENT_UNKNOWN", "UNKNOWN"}:
        return DIGIFLAZZ_OUTCOME_SENT_UNKNOWN

    data = digiflazz_response_data(response)
    status = str(data.get("status") or "").strip().lower()
    rc = str(data.get("rc") or "").strip()

    if status in {"sukses", "success"}:
        return DIGIFLAZZ_OUTCOME_SUCCESS if rc == "00" else DIGIFLAZZ_OUTCOME_SENT_UNKNOWN
    if status == "pending":
        return DIGIFLAZZ_OUTCOME_PENDING if rc in {"03", "99"} else DIGIFLAZZ_OUTCOME_SENT_UNKNOWN
    if status in {"gagal", "failed"}:
        if rc in _DIGIFLAZZ_DEFINITIVE_FAILURE_CODES:
            return DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE
        if rc in _DIGIFLAZZ_AMBIGUOUS_FAILURE_CODES or not rc:
            return DIGIFLAZZ_OUTCOME_SENT_UNKNOWN

    return DIGIFLAZZ_OUTCOME_SENT_UNKNOWN


async def _runtime_config() -> Dict[str, str]:
    config = await get_digiflazz_config()
    return {
        "username": config.get("username") or DIGIFLAZZ_USERNAME,
        "api_key": config.get("api_key") or DIGIFLAZZ_API_KEY,
        "base_url": (config.get("base_url") or DIGIFLAZZ_BASE_URL).rstrip("/"),
    }


async def _call_digiflazz(payload: Dict[str, Any], endpoint: str, base_url: str, timeout: float = 15.0) -> Dict[str, Any]:
    if not payload.get("username") or not payload.get("sign"):
        return {
            "data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"},
            "_provider_outcome": "NOT_SENT",
        }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/{endpoint}", json=payload)
            result = response.json()
            if isinstance(result, dict):
                result.setdefault("_provider_outcome", "RESPONDED")
            return result
    except Exception as exc:
        logger.warning(
            "Digiflazz request gagal endpoint=%s error_type=%s",
            endpoint,
            type(exc).__name__,
        )
        return {
            "data": {"message": "Koneksi ke Digiflazz sedang bermasalah", "rc": "99"},
            "_provider_outcome": "SENT_UNKNOWN",
            "_error_type": type(exc).__name__,
        }


def _sign(username: str, api_key: str, key: str) -> str:
    return hashlib.md5((username + api_key + key).encode()).hexdigest()


async def kirim_digiflazz(
    sku: str,
    tujuan: str,
    ref_id: str,
    *,
    max_price: Optional[int] = None,
    callback_url: Optional[str] = None,
    testing: bool = False,
) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not username or not api_key:
        return {
            "data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"},
            "_provider_outcome": "NOT_SENT",
        }

    payload = {
        "username": username,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": _sign(username, api_key, ref_id),
    }
    if max_price is not None and int(max_price) > 0:
        payload["max_price"] = int(max_price)
    if callback_url:
        payload["cb_url"] = callback_url
    if testing:
        payload["testing"] = True
    return await _call_digiflazz(payload, "transaction", base_url)


async def cek_status_digiflazz(sku: str, tujuan: str, ref_id: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not username or not api_key:
        return {
            "data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"},
            "_provider_outcome": "NOT_SENT",
        }

    payload = {
        "username": username,
        "buyer_sku_code": sku,
        "customer_no": tujuan,
        "ref_id": ref_id,
        "sign": _sign(username, api_key, ref_id),
    }
    return await _call_digiflazz(payload, "transaction", base_url)


async def get_digiflazz_balance() -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not username or not api_key:
        return {"data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"}}

    payload = {
        "cmd": "deposit",
        "username": username,
        "sign": _sign(username, api_key, "depo"),
    }
    return await _call_digiflazz(payload, "cek-saldo", base_url, timeout=10.0)


async def create_digiflazz_deposit_ticket(amount: int, bank: str, owner_name: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not username or not api_key:
        return {"data": {"message": "Konfigurasi Digiflazz belum lengkap", "rc": "99"}}

    payload = {
        "username": username,
        "amount": int(amount),
        "Bank": str(bank or "").strip().upper(),
        "owner_name": str(owner_name or "").strip(),
        "sign": _sign(username, api_key, "deposit"),
    }
    return await _call_digiflazz(payload, "deposit", base_url, timeout=15.0)


async def get_digiflazz_products(
    *,
    cmd: str = "prepaid",
    code: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    product_type: Optional[str] = None,
) -> Any:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    if not username or not api_key:
        return "Konfigurasi Digiflazz belum lengkap"

    payload = {
        "cmd": cmd,
        "username": username,
        "sign": _sign(username, api_key, "pricelist"),
    }
    if code:
        payload["code"] = code
    if category:
        payload["category"] = category
    if brand:
        payload["brand"] = brand
    if product_type:
        payload["type"] = product_type

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{base_url}/price-list", json=payload)
            data = response.json()

            products = data.get("data")
            if isinstance(products, list):
                return products
            if isinstance(products, dict):
                error_msg = products.get("message", "Format data salah")
                logger.warning("Digiflazz menolak sinkronisasi katalog")
                return error_msg
            return "Format data salah"
    except Exception as exc:
        logger.warning("Sinkronisasi katalog Digiflazz gagal error_type=%s", type(exc).__name__)
        return "Koneksi ke Digiflazz sedang bermasalah"


async def inquiry_postpaid(sku: str, customer_no: str, ref_id: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    payload = {
        "commands": "inq-pasca",
        "username": username,
        "buyer_sku_code": sku,
        "customer_no": customer_no,
        "ref_id": ref_id,
        "sign": _sign(username, api_key, ref_id),
    }
    return await _call_digiflazz(payload, "transaction", base_url, timeout=20.0)


async def pay_postpaid(sku: str, customer_no: str, ref_id: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    payload = {
        "commands": "pay-pasca",
        "username": username,
        "buyer_sku_code": sku,
        "customer_no": customer_no,
        "ref_id": ref_id,
        "sign": _sign(username, api_key, ref_id),
    }
    return await _call_digiflazz(payload, "transaction", base_url, timeout=20.0)


async def cek_status_postpaid(sku: str, customer_no: str, ref_id: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    payload = {
        "commands": "status-pasca",
        "username": username,
        "buyer_sku_code": sku,
        "customer_no": customer_no,
        "ref_id": ref_id,
        "sign": _sign(username, api_key, ref_id),
    }
    return await _call_digiflazz(payload, "transaction", base_url, timeout=20.0)


async def inquiry_pln(customer_no: str) -> Dict[str, Any]:
    config = await _runtime_config()
    username = config["username"]
    api_key = config["api_key"]
    base_url = config["base_url"]

    payload = {
        "commands": "pln-subscribe",
        "customer_no": customer_no,
        "username": username,
        "sign": _sign(username, api_key, customer_no),
    }
    return await _call_digiflazz(payload, "inquiry-pln", base_url, timeout=15.0)
