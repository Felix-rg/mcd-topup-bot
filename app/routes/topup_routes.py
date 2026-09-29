import hashlib
import html
import json
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from app.core.settings import settings
from app.database import db_execute, db_execute_rowcount, db_query, db_transaction
from app.promotions.engine import (
    PromoIdentity,
    PromotionContext,
    PromotionQuote,
    normalize_payment_code,
    normalize_sku,
    normalize_voucher_code,
    quote_promotions,
)
from app.promotions.service import (
    PromotionChangedError,
    PromotionCodeError,
    code_failure_payload,
    code_failure_reason,
    finalize_order_promotions,
    order_promo_values,
    persist_order_with_promotions,
    quote_catalog_products,
    quote_product,
    release_order_promotions,
    promotion_failure_payload,
)
from app.security import create_access_token, decode_access_token, hash_password, verify_password
from app.services.digiflazz_service import (
    DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE,
    DIGIFLAZZ_OUTCOME_PENDING,
    DIGIFLAZZ_OUTCOME_SENT_UNKNOWN,
    DIGIFLAZZ_OUTCOME_SUCCESS,
    classify_digiflazz_transaction_response,
    inquiry_pln,
    inquiry_postpaid,
)
from app.services.nickname_service import check_game_nickname
from app.services.order_service import create_order_id
from app.services.provider_settings import get_digiflazz_config, get_provider_runtime_settings, get_tripay_config
from app.services.product_utils import normalized_category_name, normalized_provider_name
from app.services.tripay_service import (
    TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
    TRIPAY_INVOICE_OUTCOME_NOT_SENT,
    TRIPAY_INVOICE_OUTCOME_SUCCESS,
    calculate_fee,
    check_transaction_status,
    classify_tripay_invoice_response,
    create_invoice,
    create_open_payment,
    get_payment_channels,
    get_payment_instruction,
    list_open_payment_transactions,
    parse_tripay_fee,
)
from app.services.wallet_service import (
    credit_wallet_deposit_if_new,
    wallet_add_entry,
    wallet_balance,
)

router = APIRouter()
logger = logging.getLogger(__name__)
try:
    APP_TIMEZONE = ZoneInfo("Asia/Jakarta")
except Exception:
    APP_TIMEZONE = timezone(timedelta(hours=7), name="WIB")
LOCAL_TZ = APP_TIMEZONE


def _format_public_datetime(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return {}


def _payment_instructions_from_payload(payload: Any) -> list[Any]:
    parsed = _json_loads(payload)
    if isinstance(parsed, dict):
        instructions = parsed.get("instructions")
        if instructions is None and isinstance(parsed.get("data"), dict):
            instructions = parsed["data"].get("instructions")
        return instructions if isinstance(instructions, list) else []
    return []


def _payment_instructions_from_tripay(tripay_res: Dict[str, Any]) -> list[Any]:
    instructions = tripay_res.get("instructions")
    if isinstance(instructions, list):
        return instructions
    return _payment_instructions_from_payload(tripay_res.get("raw") or tripay_res)


def _timestamp_from_unix(value: Any) -> Optional[str]:
    try:
        numeric = int(value or 0)
        if numeric <= 0:
            return None
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _tripay_data(payload: Dict[str, Any]) -> Any:
    return payload.get("data") if isinstance(payload, dict) else None


def _channel_flag(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "active", "enabled"}


def _normalize_channel(item: Dict[str, Any]) -> Dict[str, Any]:
    code = str(item.get("code") or item.get("method") or "").strip().upper()
    name = item.get("name") or item.get("payment_name") or code
    group = item.get("group") or item.get("type") or "Lainnya"
    status = str(item.get("status") or "").strip().lower()
    maintenance = _channel_flag(item.get("maintenance")) or status in {"maintenance", "down"}
    active = _channel_flag(item.get("active"), default=False)
    if status in {"inactive", "disabled", "unavailable"}:
        active = False
    if maintenance:
        active = False
    return {
        "code": code,
        "name": name,
        "group": group,
        "icon_url": item.get("icon_url") or item.get("icon") or "",
        "active": active,
        "maintenance": maintenance,
        "minimum_amount": _int_value(item.get("minimum_amount") or item.get("min_amount")),
        "maximum_amount": _int_value(item.get("maximum_amount") or item.get("max_amount")),
        "raw": item,
    }


def _provider_channel_items(response: Any) -> list[Dict[str, Any]]:
    if not isinstance(response, dict) or response.get("success") is not True:
        return []
    provider_channels = _tripay_data(response)
    if isinstance(provider_channels, dict):
        provider_channels = provider_channels.get("data") or provider_channels.get("channels") or []
    if not isinstance(provider_channels, list):
        return []
    return [
        _normalize_channel(item)
        for item in provider_channels
        if isinstance(item, dict) and str(item.get("code") or item.get("method") or "").strip()
    ]


def _payment_channel_exception(status_code: int, reason_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"reason_code": reason_code, "message": message},
    )


async def _validated_payment_channel(method: str, amount: int) -> Dict[str, Any]:
    normalized_method = normalize_payment_code(method)
    if normalized_method == "WALLET":
        return {
            "code": "WALLET",
            "name": "Wallet LIXAFA",
            "active": True,
            "maintenance": False,
            "minimum_amount": 0,
            "maximum_amount": 0,
            "raw": {},
        }

    response = await get_payment_channels()
    channels = _provider_channel_items(response)
    if not channels:
        raise _payment_channel_exception(
            503,
            "PAYMENT_CHANNELS_UNAVAILABLE",
            "Metode pembayaran belum dapat dimuat dari provider. Silakan coba lagi.",
        )

    channel = next((item for item in channels if item["code"] == normalized_method), None)
    if channel is None:
        raise _payment_channel_exception(
            400,
            "PAYMENT_CHANNEL_UNAVAILABLE",
            "Metode pembayaran yang dipilih tidak tersedia.",
        )
    if channel.get("maintenance"):
        raise _payment_channel_exception(
            409,
            "PAYMENT_CHANNEL_MAINTENANCE",
            "Metode pembayaran sedang dalam pemeliharaan.",
        )
    if channel.get("active") is not True:
        raise _payment_channel_exception(
            409,
            "PAYMENT_CHANNEL_INACTIVE",
            "Metode pembayaran sedang tidak aktif.",
        )

    minimum_amount = int(channel.get("minimum_amount") or 0)
    maximum_amount = int(channel.get("maximum_amount") or 0)
    if minimum_amount and amount < minimum_amount:
        raise _payment_channel_exception(
            400,
            "PAYMENT_AMOUNT_BELOW_MINIMUM",
            f"Minimum transaksi untuk metode ini adalah Rp{minimum_amount:,}.".replace(",", "."),
        )
    if maximum_amount and amount > maximum_amount:
        raise _payment_channel_exception(
            400,
            "PAYMENT_AMOUNT_ABOVE_MAXIMUM",
            f"Maksimum transaksi untuk metode ini adalah Rp{maximum_amount:,}.".replace(",", "."),
        )
    return channel


async def _quote_payment_fee(base_amount: Any, method: str) -> tuple[int, str]:
    normalized_method = (method or "").strip().upper()
    amount = _int_value(base_amount)
    if amount <= 0:
        return 0, "free_checkout"
    if normalized_method == "WALLET":
        return 0, "wallet"

    channel = await _validated_payment_channel(normalized_method, amount)
    response = await calculate_fee(normalized_method, amount)
    if isinstance(response, dict) and response.get("success"):
        parsed_fee = parse_tripay_fee(response, amount=amount)
        if parsed_fee.valid:
            return parsed_fee.customer_fee, "tripay"

    channel_fee = parse_tripay_fee(channel.get("raw") or {}, amount=amount)
    if channel_fee.valid:
        return channel_fee.customer_fee, "tripay_channel"

    raise _payment_channel_exception(
        503,
        "PAYMENT_FEE_UNAVAILABLE",
        "Biaya pembayaran belum dapat dihitung dari provider. Silakan coba lagi.",
    )


async def _invoice_expired_time() -> int:
    runtime = await get_provider_runtime_settings()
    minutes = _int_value(runtime.get("tripay_invoice_expiry_minutes")) or 30
    return int((datetime.now(timezone.utc) + timedelta(minutes=minutes)).timestamp())


def _extract_tripay_invoice_metadata(tripay_res: Dict[str, Any]) -> Dict[str, Any]:
    expired_value = tripay_res.get("expired_time") or tripay_res.get("expired_at")
    expired_at = _timestamp_from_unix(expired_value) if expired_value else None
    return {
        "payment_reference": tripay_res.get("reference") or "",
        "payment_name": tripay_res.get("payment_name") or tripay_res.get("payment_method") or "",
        "pay_code": str(tripay_res.get("pay_code") or ""),
        "pay_url": tripay_res.get("pay_url") or "",
        "qr_url": tripay_res.get("qr_url") or "",
        "qr_string": tripay_res.get("qr_string") or "",
        "payment_expired_at": expired_at,
        "tripay_payload": _json_dumps(tripay_res.get("raw") or tripay_res),
    }


async def _update_order_payment_metadata(order_id: str, tripay_res: Dict[str, Any]) -> None:
    invoice_url = tripay_res.get("checkout_url") or tripay_res.get("pay_url") or ""
    metadata = _extract_tripay_invoice_metadata(tripay_res)
    await db_execute(
        """
        UPDATE topup
        SET invoice_url=:invoice_url,
            payment_reference=:payment_reference,
            payment_creation_outcome='SUCCESS',
            payment_name=:payment_name,
            pay_code=:pay_code,
            pay_url=:pay_url,
            qr_url=:qr_url,
            qr_string=:qr_string,
            payment_expired_at=:payment_expired_at,
            tripay_payload=:tripay_payload,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
        """,
        {"id": order_id, "invoice_url": invoice_url, **metadata},
    )


async def _mark_invoice_creation_unknown(order_id: str, tripay_res: Any) -> None:
    """Keep an unpaid order reconcilable when Tripay POST outcome is ambiguous."""

    async with db_transaction() as connection:
        updated_rows = await db_execute_rowcount(
            """
            UPDATE topup
            SET payment_last_check_at=CURRENT_TIMESTAMP,
                payment_creation_outcome='SENT_UNKNOWN',
                tripay_payload=:tripay_payload,
                status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
              AND payment_status='UNPAID'
              AND COALESCE(topup_status, '')='PENDING_PAYMENT'
            """,
            {
                "id": order_id,
                "tripay_payload": _json_dumps(
                    tripay_res or {"invoice_outcome": "SENT_UNKNOWN"}
                ),
            },
            connection=connection,
        )
        if updated_rows:
            # An invoice may have been accepted even though the client timed
            # out. Hold the matching promotion in the same transaction so a
            # concurrent checkout cannot reclaim the quota in between.
            await db_execute(
                """
                UPDATE promotion_redemptions
                SET expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE order_id=:order_id AND UPPER(status)='RESERVED'
                """,
                {"order_id": order_id},
                connection=connection,
            )


async def _mark_invoice_creation_failed(order_id: str, tripay_res: Any) -> bool:
    """Fail only a creation outcome proved not to have produced an invoice."""

    updated_rows = await db_execute_rowcount(
        """
        UPDATE topup
        SET payment_status='FAILED',
            topup_status='FAILED',
            payment_creation_outcome='NOT_SENT',
            tripay_payload=:tripay_payload,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
          AND payment_status='UNPAID'
          AND COALESCE(topup_status, '')='PENDING_PAYMENT'
        """,
        {
            "id": order_id,
            "tripay_payload": _json_dumps(tripay_res or {}),
        },
    )
    if updated_rows:
        await release_order_promotions(order_id)
    return bool(updated_rows)


def _invoice_creation_pending_response(
    *,
    order_id: str,
    payment_method: str,
    total: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={
            "id": order_id,
            "order_access_token": _order_access_token(order_id),
            "payment_method": payment_method,
            "total": int(total),
            "payment_creation_pending": True,
            "message": "Pembuatan invoice belum dapat dipastikan dan sedang direkonsiliasi. Jangan membuat order atau pembayaran baru.",
        },
    )


def _public_page_dict(row: tuple[Any, ...], include_content: bool = False) -> Dict[str, Any]:
    page = {
        "id": row[0],
        "slug": row[1],
        "title": row[2],
        "excerpt": row[3] or "",
        "image_url": row[5] or "",
        "page_type": row[6] or "general",
        "badge": row[7] or "",
        "cta_text": row[8] or "",
        "cta_url": row[9] or "",
        "secondary_cta_text": row[10] or "",
        "secondary_cta_url": row[11] or "",
        "promo_code": row[12] or "",
        "highlight_title": row[13] or "",
        "highlight_items": row[14] or "",
        "terms_text": row[15] or "",
        "accent_color": row[16] or "gold",
        "created_at": _format_public_datetime(row[19]),
        "updated_at": _format_public_datetime(row[20]),
        "url": f"/p/{row[1]}",
    }
    if include_content:
        page["content"] = row[4] or ""
    return page


def _page_select_columns() -> str:
    return """
        id, slug, title, excerpt, content, image_url,
        page_type, badge, cta_text, cta_url, secondary_cta_text, secondary_cta_url,
        promo_code, highlight_title, highlight_items, terms_text, accent_color,
        active, show_on_website, created_at, updated_at
    """


def _split_lines(value: Any) -> list[str]:
    lines: list[str] = []
    for raw in str(value or "").replace("\r", "").split("\n"):
        cleaned = raw.strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _safe_url(value: Any, fallback: str = "/") -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return fallback
    if cleaned.startswith(("http://", "https://", "/", "#")):
        return cleaned
    return f"/{cleaned.lstrip('/')}"


def _normalize_public_cta_url(value: Any, fallback: str = "/#promo", *, allow_external: bool = False) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return fallback
    # Protocol-relative URLs bypass a normal scheme check in browsers.
    if cleaned.startswith("//"):
        return fallback
    if cleaned.startswith(("game:", "modal:", "#")):
        return cleaned if allow_external else fallback
    if cleaned in {"order", "/order"}:
        return "/#produk-section"
    if cleaned.startswith("/"):
        return cleaned

    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return _safe_url(cleaned, fallback)

    if parsed.scheme in {"http", "https"}:
        hostname = (parsed.hostname or "").lower()
        if hostname in {"127.0.0.1", "localhost"} or hostname.endswith(".ngrok-free.app") or hostname.endswith(".ngrok.app"):
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            if parsed.fragment:
                path = f"{path}#{parsed.fragment}"
            return path
        return cleaned

    return _safe_url(cleaned, fallback)


def _render_text_blocks(value: Any) -> str:
    lines = _split_lines(value)
    if not lines:
        return ""
    return "".join(f"<p>{html.escape(line)}</p>" for line in lines)


def _accent_vars(accent: str) -> tuple[str, str]:
    colors = {
        "gold": ("#EBC166", "#9E6710"),
        "green": ("#54d18a", "#167a45"),
        "blue": ("#6bb7ff", "#2457c5"),
        "red": ("#ff7875", "#b42318"),
    }
    return colors.get((accent or "gold").lower(), colors["gold"])


def _normalize_match_token(value: Any, *, harmonize_game_terms: bool = True) -> str:
    raw = str(value or "").strip().lower()
    # Samakan ejaan populer agar target promo provider/category lebih toleran.
    if harmonize_game_terms:
        raw = raw.replace("legends", "legend")
    return "".join(ch for ch in raw if ch.isalnum())


def _split_target_values(value: str) -> list[str]:
    if not value:
        return []
    parts: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        cleaned = raw.strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def _matches_target(target_value: str, actual_value: str, *, exact: bool = False) -> bool:
    actual_token = _normalize_match_token(actual_value, harmonize_game_terms=not exact)
    if not actual_token:
        return False

    targets = _split_target_values(target_value)
    if not targets:
        return False

    for item in targets:
        target_token = _normalize_match_token(item, harmonize_game_terms=not exact)
        if not target_token:
            continue
        if exact:
            if target_token == actual_token:
                return True
            continue
        if target_token == actual_token or target_token in actual_token or actual_token in target_token:
            return True
    return False


def _promo_payment_methods(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        try:
            parsed = json.loads(str(raw_value or "[]"))
            values = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            values = str(raw_value or "").replace(";", ",").split(",")
    normalized: list[str] = []
    for value in values:
        cleaned = str(value or "").strip().upper()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _promo_usage_available(promo: Dict[str, Any]) -> bool:
    checks = (
        ("usage_limit", "usage_count"),
        ("max_per_customer", "customer_usage_count"),
        ("max_per_phone", "phone_usage_count"),
        ("max_per_target", "target_usage_count"),
    )
    for limit_key, count_key in checks:
        limit = _int_value(promo.get(limit_key))
        if limit > 0 and _int_value(promo.get(count_key)) >= limit:
            return False

    budget_limit = float(promo.get("budget_limit") or 0)
    if budget_limit > 0 and float(promo.get("discount_spent") or 0) >= budget_limit:
        return False
    return True


def _promo_budget_remaining(promo: Dict[str, Any]) -> Optional[float]:
    budget_limit = float(promo.get("budget_limit") or 0)
    if budget_limit <= 0:
        return None
    return max(budget_limit - float(promo.get("discount_spent") or 0), 0)


def _promo_discount_amount(base_price: float, promo: Dict[str, Any]) -> float:
    discount_type = (promo.get("discount_type") or "").lower()
    discount_value = float(promo.get("discount_value") or 0)
    max_discount = float(promo.get("max_discount") or 0)

    if base_price <= 0 or discount_value <= 0:
        return 0

    if discount_type == "percent":
        discount = base_price * (discount_value / 100)
    elif discount_type == "fixed":
        discount = discount_value
    else:
        return 0

    if max_discount > 0:
        discount = min(discount, max_discount)

    remaining_budget = _promo_budget_remaining(promo)
    if remaining_budget is not None:
        discount = min(discount, remaining_budget)

    return max(discount, 0)


def _promo_priority(promo: Dict[str, Any]) -> int:
    return _int_value(promo.get("priority"))


def _to_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _promo_visible_on_website_now(*, starts_at: Any, ends_at: Any, active: bool, show_on_website: bool) -> bool:
    if not active or not show_on_website:
        return False
    now_utc = datetime.now(timezone.utc)
    starts_at_utc = _to_utc_datetime(starts_at)
    ends_at_utc = _to_utc_datetime(ends_at)
    if starts_at_utc and now_utc < starts_at_utc:
        return False
    if ends_at_utc and now_utc > ends_at_utc:
        return False
    return True


def _format_public_promo_datetime(value: Any) -> str:
    parsed = _to_utc_datetime(value)
    if not parsed:
        return "-"
    return parsed.astimezone(LOCAL_TZ).strftime("%d %b %Y, %H:%M WIB")


def _promo_target_label(scope: Any, target_value: Any) -> str:
    normalized_scope = str(scope or "all").strip().lower()
    readable_value = str(target_value or "").strip()
    if normalized_scope == "all":
        return "Semua produk"
    if normalized_scope == "sku":
        return f"SKU {readable_value}" if readable_value else "SKU tertentu"
    if normalized_scope == "provider":
        return readable_value or "Provider tertentu"
    if normalized_scope == "category":
        return readable_value or "Kategori tertentu"
    return readable_value or "Promo khusus"


def _promo_discount_label(discount_type: Any, discount_value: Any, max_discount: Any) -> str:
    normalized_type = str(discount_type or "").strip().lower()
    numeric_value = _float_value(discount_value)
    numeric_cap = _float_value(max_discount)

    if normalized_type == "percent":
        label = f"{numeric_value:.0f}% OFF"
        if numeric_cap > 0:
            label = f"{label} • maks Rp {numeric_cap:,.0f}".replace(",", ".")
        return label
    if normalized_type == "fixed":
        return f"Potongan Rp {numeric_value:,.0f}".replace(",", ".")
    return "Promo spesial"


def _promo_period_label(starts_at: Any, ends_at: Any) -> str:
    start_label = _format_public_promo_datetime(starts_at)
    end_label = _format_public_promo_datetime(ends_at)
    if start_label == "-" and end_label == "-":
        return "Selama promo aktif"
    if start_label == "-":
        return f"Sampai {end_label}"
    if end_label == "-":
        return f"Mulai {start_label}"
    return f"{start_label} - {end_label}"


def _promo_cta_href(cta_url: Any, *, allow_external: bool = False) -> str:
    normalized = _normalize_public_cta_url(cta_url, allow_external=allow_external)
    if normalized.startswith(("http://", "https://", "/")):
        return normalized
    if normalized.startswith("#"):
        return f"/{normalized}"
    return "/#produk-section"


def _promo_cta_is_external(cta_url: Any, *, allow_external: bool = False) -> bool:
    normalized = _normalize_public_cta_url(cta_url, allow_external=allow_external)
    return normalized.startswith(("http://", "https://"))


def _public_promo_dict(row: Any) -> Dict[str, Any]:
    allow_external_cta = bool(row[18]) if len(row) > 18 else False
    return {
        "id": row[0],
        "title": row[1] or "",
        "code": row[2] or "",
        "description": row[3] or "",
        "badge": row[4] or "",
        "cta_text": row[5] or "Lihat Promo",
        "cta_url": row[6] or "#",
        "resolved_cta_url": _normalize_public_cta_url(row[6], allow_external=allow_external_cta),
        "allow_external_cta": allow_external_cta,
        "image_url": row[7] or "",
        "rule_type": row[8] or "content",
        "target_scope": row[9] or "all",
        "target_value": row[10] or "",
        "discount_type": row[11] or "",
        "discount_value": float(row[12] or 0),
        "max_discount": float(row[13] or 0),
        "starts_at": row[14].isoformat() if hasattr(row[14], "isoformat") else row[14],
        "ends_at": row[15].isoformat() if hasattr(row[15], "isoformat") else row[15],
        "show_on_website": bool(row[16]),
        "discount_label": _promo_discount_label(row[11], row[12], row[13]),
        "target_label": _promo_target_label(row[9], row[10]),
        "period_label": _promo_period_label(row[14], row[15]),
    }


async def _load_public_active_promos() -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, title, code, description, badge, cta_text, cta_url, image_url,
               rule_type, target_scope, target_value, discount_type, discount_value, max_discount,
               starts_at, ends_at, show_on_website, active, COALESCE(allow_external_cta, 0)
        FROM promos
        WHERE active=1 AND COALESCE(show_on_website, 1)=1
        ORDER BY created_at DESC
        """
    )

    promos: list[Dict[str, Any]] = []
    for row in rows:
        if not _promo_visible_on_website_now(
            starts_at=row[14],
            ends_at=row[15],
            show_on_website=bool(row[16]),
            active=bool(row[17]),
        ):
            continue
        promos.append(_public_promo_dict(row))
    return promos


def _promo_matches(
    promo: Dict[str, Any],
    product: Dict[str, Any],
    now_utc: datetime,
    *,
    payment_method: Optional[str] = None,
) -> bool:
    if not promo.get("active"):
        return False
    if not _promo_usage_available(promo):
        return False

    allowed_methods = _promo_payment_methods(promo.get("payment_methods"))
    if allowed_methods:
        normalized_method = str(payment_method or "").strip().upper()
        if not normalized_method or normalized_method not in allowed_methods:
            return False

    starts_at = _to_utc_datetime(promo.get("starts_at"))
    ends_at = _to_utc_datetime(promo.get("ends_at"))

    if starts_at and now_utc < starts_at:
        return False
    if ends_at and now_utc > ends_at:
        return False

    scope = (promo.get("target_scope") or "all").lower()
    target_value = (promo.get("target_value") or "").strip()

    if scope == "all":
        return True
    if scope == "sku":
        # SKU wajib cocok persis agar promo tidak bocor ke SKU lain.
        return _matches_target(target_value, str(product.get("sku") or ""), exact=True)
    if scope == "provider":
        return _matches_target(target_value, str(product.get("provider") or ""))
    if scope == "category":
        return _matches_target(target_value, str(product.get("category") or ""))
    return False


def _apply_price_promo(base_price: float, promo: Dict[str, Any]) -> float:
    discount = _promo_discount_amount(base_price, promo)
    return max(base_price - discount, 0)


def _promo_summary(promo: Dict[str, Any], price_before: float, price_after: float) -> Dict[str, Any]:
    return {
        "id": promo.get("id"),
        "title": promo.get("title"),
        "badge": promo.get("badge"),
        "code": promo.get("code") or "",
        "discount_type": promo.get("discount_type"),
        "discount_value": promo.get("discount_value"),
        "price_before": round(price_before, 2),
        "price_after": round(price_after, 2),
        "discount_amount": round(max(price_before - price_after, 0), 2),
    }


def _select_best_price_promo(
    product: Dict[str, Any],
    promos: list[Dict[str, Any]],
    now_utc: datetime,
    current_price: float,
    *,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], float]:
    best_price = current_price
    best_promo: Optional[Dict[str, Any]] = None
    normalized_code = (promo_code or "").strip().lower()
    require_code = bool(normalized_code)

    for promo in promos:
        if (promo.get("rule_type") or "").lower() != "price":
            continue
        if not _promo_matches(promo, product, now_utc, payment_method=payment_method):
            continue

        required_code = str(promo.get("code") or "").strip().lower()
        if require_code:
            if not required_code or required_code != normalized_code:
                continue
        elif required_code:
            continue

        candidate_price = _apply_price_promo(current_price, promo)
        better_discount = candidate_price < best_price
        same_discount_higher_priority = (
            best_promo is not None
            and candidate_price == best_price
            and _promo_priority(promo) > _promo_priority(best_promo)
        )
        if better_discount or same_discount_higher_priority:
            best_price = candidate_price
            best_promo = promo

    return best_promo, best_price


def _promo_code_was_applied(product: Dict[str, Any], promo_code: Optional[str]) -> bool:
    normalized_code = (promo_code or "").strip().lower()
    if not normalized_code:
        return False

    return any(
        str(promo.get("code") or "").strip().lower() == normalized_code
        for promo in product.get("promos_applied", [])
    )


async def _promo_context_count(code: str, column: str, value: Any) -> int:
    if not code or value in (None, ""):
        return 0
    allowed_columns = {"customer_id", "phone", "target_id"}
    if column not in allowed_columns:
        return 0
    rows = await db_query(
        f"""
        SELECT COUNT(*)
        FROM topup t
        WHERE UPPER(COALESCE(t.promo_code, '')) = UPPER(:code)
          AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED')
          AND t.{column} = :value
        """,
        {"code": code, "value": value},
    )
    return _int_value(rows[0][0]) if rows else 0


async def _get_active_price_promos(
    *,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, title, code, badge, rule_type, target_scope, target_value,
               discount_type, discount_value, max_discount,
               COALESCE(usage_limit, 0), payment_methods,
               COALESCE(budget_limit, 0), COALESCE(max_per_customer, 0),
               COALESCE(max_per_phone, 0), COALESCE(max_per_target, 0),
               COALESCE(stackable, 1), COALESCE(priority, 0),
               starts_at, ends_at, active,
               CASE
                   WHEN COALESCE(code, '') = '' THEN 0
                   ELSE (
                       SELECT COUNT(*)
                       FROM topup t
                       WHERE UPPER(COALESCE(t.promo_code, '')) = UPPER(COALESCE(promos.code, ''))
                         AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED')
                   )
               END AS usage_count,
               CASE
                   WHEN COALESCE(code, '') = '' THEN 0
                   ELSE (
                       SELECT COALESCE(SUM(COALESCE(t.promo_discount_amount, 0)), 0)
                       FROM topup t
                       WHERE UPPER(COALESCE(t.promo_code, '')) = UPPER(COALESCE(promos.code, ''))
                         AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED')
                   )
               END AS discount_spent
        FROM promos
        WHERE active=1
        ORDER BY COALESCE(priority, 0) DESC, created_at DESC
        """
    )

    promos: list[Dict[str, Any]] = []
    for row in rows:
        code = row[2] or ""
        promo = {
            "id": row[0],
            "title": row[1] or "",
            "code": code,
            "badge": row[3] or "",
            "rule_type": row[4] or "content",
            "target_scope": row[5] or "all",
            "target_value": row[6] or "",
            "discount_type": row[7] or "",
            "discount_value": float(row[8] or 0),
            "max_discount": float(row[9] or 0),
            "usage_limit": _int_value(row[10]),
            "payment_methods": _promo_payment_methods(row[11]),
            "budget_limit": float(row[12] or 0),
            "max_per_customer": _int_value(row[13]),
            "max_per_phone": _int_value(row[14]),
            "max_per_target": _int_value(row[15]),
            "stackable": bool(row[16]),
            "priority": _int_value(row[17]),
            "starts_at": row[18],
            "ends_at": row[19],
            "active": bool(row[20]),
            "usage_count": _int_value(row[21]),
            "discount_spent": float(row[22] or 0),
            "customer_usage_count": 0,
            "phone_usage_count": 0,
            "target_usage_count": 0,
        }
        if code and customer_id is not None and promo["max_per_customer"] > 0:
            promo["customer_usage_count"] = await _promo_context_count(code, "customer_id", customer_id)
        if code and phone and promo["max_per_phone"] > 0:
            promo["phone_usage_count"] = await _promo_context_count(code, "phone", phone)
        if code and target_id and promo["max_per_target"] > 0:
            promo["target_usage_count"] = await _promo_context_count(code, "target_id", target_id)
        promos.append(promo)
    return promos


def _apply_best_price_promo(
    product: Dict[str, Any],
    promos: list[Dict[str, Any]],
    now_utc: datetime,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> Dict[str, Any]:
    context = PromotionContext(
        base_price=product.get("price") or 0,
        sku=str(product.get("sku") or ""),
        provider=str(product.get("provider") or ""),
        category=str(product.get("category") or ""),
        brand=str(product.get("brand") or ""),
        supplier=str(product.get("supplier") or product.get("provider_type") or ""),
        payment_method=str(payment_method or "").strip().upper(),
        promo_code=promo_code,
        now=now_utc,
    )
    quote = quote_promotions(promos, context)
    product["original_price"] = quote.base_price
    product["price"] = quote.final_price
    if quote.applied:
        applied_promos = [item.to_dict() for item in quote.applied]
        product["promos_applied"] = applied_promos
        # Backward-compatible single promo field for the existing frontend.
        product["promo_applied"] = applied_promos[-1]
    else:
        product.pop("promos_applied", None)
        product.pop("promo_applied", None)

    return product


async def _resolve_effective_product_by_sku(
    sku: str,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
    identity: Optional[PromoIdentity] = None,
) -> Dict[str, Any]:
    canonical_sku = normalize_sku(sku)
    if not canonical_sku:
        raise HTTPException(status_code=400, detail="Produk tidak ditemukan")
    row = await db_query(
        """
        SELECT sku, provider, name, price, cost_price, category,
               COALESCE(product_type, 'prepaid'),
               COALESCE(buyer_product_status, 1),
               COALESCE(seller_product_status, 1),
               stock,
               COALESCE(unlimited_stock, 0),
               COALESCE(multi, 0),
               brand,
               provider_type
        FROM products
        WHERE LOWER(TRIM(sku))=:sku AND COALESCE(active, 1)=1
        """,
        {"sku": canonical_sku},
    )
    if not row:
        raise HTTPException(400, "Produk tidak ditemukan")

    (
        sku_value,
        provider,
        name,
        base_price,
        cost_price,
        category,
        product_type,
        buyer_product_status,
        seller_product_status,
        stock,
        unlimited_stock,
        multi,
        brand,
        provider_type,
    ) = row[0]
    normalized_provider = normalized_provider_name(provider, name, category)
    product = {
        "sku": sku_value,
        "provider": normalized_provider,
        "name": name,
        "price": float(base_price or 0),
        "product_cost": float(cost_price or 0),
        "category": normalized_category_name(category, normalized_provider),
        "product_type": str(product_type or "prepaid").lower(),
        "buyer_product_status": bool(buyer_product_status),
        "seller_product_status": bool(seller_product_status),
        "stock": int(stock) if stock is not None else None,
        "unlimited_stock": bool(unlimited_stock),
        "multi": bool(multi),
        "brand": brand or "",
        "provider_type": provider_type or "",
    }

    quote = await quote_product(
        product,
        promo_code=promo_code,
        payment_method=payment_method,
        phone=phone,
        target_id=target_id,
        customer_id=customer_id,
        identity=identity,
    )
    if promo_code and not quote.code_valid:
        reason, detail = code_failure_reason(quote)
        raise PromotionCodeError(reason, detail)
    product["original_price"] = quote.base_price
    product["price"] = quote.final_price
    product["promos_applied"] = [item.to_dict() for item in quote.applied]
    product["promo_applied"] = quote.primary.to_dict() if quote.primary else None
    product["promotion_quote"] = quote.to_dict(include_rejections=False)
    product["_promotion_quote"] = quote
    return product


async def _resolve_effective_price_by_sku(sku: str, promo_code: Optional[str] = None) -> float:
    product = await _resolve_effective_product_by_sku(sku, promo_code=promo_code)
    return float(product.get("price") or 0)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def _ensure_customer_allowed(phone: str, target_id: str) -> None:
    rows = await db_query(
        """
        SELECT reason
        FROM customer_blocks
        WHERE active=1
          AND (
              (phone IS NOT NULL AND phone != '' AND phone=:phone)
              OR (target_id IS NOT NULL AND target_id != '' AND target_id=:target_id)
          )
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"phone": phone, "target_id": target_id},
    )
    if rows:
        reason = rows[0][0] or "Customer diblokir"
        raise HTTPException(status_code=403, detail=f"Order ditolak: {reason}")


async def _existing_order_for_idempotency(key: str) -> Optional[str]:
    if not key:
        return None
    rows = await db_query(
        "SELECT order_id FROM order_idempotency_keys WHERE key=:key LIMIT 1",
        {"key": key},
    )
    return str(rows[0][0]) if rows else None


async def _bind_order_idempotency(connection, key: str, order_id: str) -> None:
    if not key:
        return
    result = await connection.execute(
        text(
            """
            INSERT INTO order_idempotency_keys (key, order_id)
            VALUES (:key, :order_id)
            ON CONFLICT(key) DO NOTHING
            """
        ),
        {"key": key, "order_id": order_id},
    )
    if int(result.rowcount or 0) == 0:
        existing = await connection.execute(
            text("SELECT order_id FROM order_idempotency_keys WHERE key=:key"),
            {"key": key},
        )
        row = existing.first()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Request order sudah pernah diproses",
                "order_id": str(row[0]) if row else "",
                "idempotent": True,
            },
        )


async def _log_webhook_event(
    *,
    provider: str,
    event_type: str,
    reference_id: Optional[str],
    signature_valid: bool,
    payload: Any,
    response_status: str,
    message: str,
) -> None:
    try:
        await db_execute(
            """
            INSERT INTO webhook_events (
                provider, event_type, reference_id, signature_valid,
                payload, response_status, message
            )
            VALUES (
                :provider, :event_type, :reference_id, :signature_valid,
                :payload, :response_status, :message
            )
            """,
            {
                "provider": provider,
                "event_type": event_type,
                "reference_id": reference_id,
                "signature_valid": 1 if signature_valid else 0,
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
                "response_status": response_status,
                "message": message,
            },
        )
    except Exception as exc:
        logger.warning("Webhook log gagal provider=%s error_type=%s", provider, type(exc).__name__)


def resolve_public_base_url(_: Request) -> str:
    """Use the configured public origin, never a client-controlled Host header."""
    return settings.app_base_url.rstrip("/")


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalize_phone(value: Optional[str]) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch == "+")


def _customer_public_dict(row: tuple[Any, ...], balance: float = 0.0) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1] or "",
        "phone": row[2] or "",
        "email": row[3] or "",
        "active": bool(row[5]) if len(row) > 5 else True,
        "wallet_balance": round(float(balance or 0), 2),
    }


async def _wallet_balance(customer_id: int) -> float:
    return await wallet_balance(customer_id)


async def _wallet_add_entry(
    *,
    customer_id: int,
    entry_type: str,
    amount: float,
    reference_type: str,
    reference_id: str,
    note: str,
) -> float:
    return await wallet_add_entry(
        customer_id=customer_id,
        entry_type=entry_type,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    )


async def _queue_notification(
    *,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    reference_type: str,
    reference_id: str,
) -> None:
    if not recipient:
        return
    try:
        await db_execute(
            """
            INSERT INTO notification_outbox (
                channel, recipient, subject, body, status,
                reference_type, reference_id, event_key
            )
            VALUES (
                :channel, :recipient, :subject, :body, 'QUEUED',
                :reference_type, :reference_id, :event_key
            )
            ON CONFLICT(event_key) DO NOTHING
            """,
            {
                "channel": channel,
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "event_key": f"{channel}:{reference_type}:{reference_id}:{subject}",
            },
        )
    except Exception as exc:
        logger.warning("Notification outbox gagal error_type=%s", type(exc).__name__)


async def _customer_by_id(customer_id: int) -> Optional[Dict[str, Any]]:
    rows = await db_query(
        "SELECT id, name, phone, email, password, active, created_at, updated_at FROM customer_accounts WHERE id=:id",
        {"id": customer_id},
    )
    if not rows:
        return None
    balance = await _wallet_balance(int(rows[0][0]))
    return _customer_public_dict(rows[0], balance)


async def _require_customer(
    customer_token: Optional[str] = Header(default=None, alias="customer-token"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    token = customer_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token customer diperlukan")

    payload = decode_access_token(token)
    if not payload or payload.get("role") != "customer":
        raise HTTPException(status_code=401, detail="Token customer tidak valid")

    customer_id = payload.get("customer_id")
    if not customer_id:
        raise HTTPException(status_code=401, detail="Token customer tidak lengkap")

    customer = await _customer_by_id(int(customer_id))
    if not customer:
        raise HTTPException(status_code=401, detail="Customer tidak ditemukan")
    if not customer.get("active"):
        raise HTTPException(status_code=403, detail="Akun customer nonaktif")
    return customer


async def _optional_customer_from_request(request: Request) -> Optional[Dict[str, Any]]:
    token = request.headers.get("customer-token")
    authorization = request.headers.get("authorization")
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "customer" or not payload.get("customer_id"):
        return None
    customer = await _customer_by_id(int(payload["customer_id"]))
    if not customer or not customer.get("active"):
        return None
    return customer


def _order_access_token(order_id: str) -> str:
    """Create an unguessable capability for a guest order without storing it."""

    payload = f"lixafa-order-access:{order_id}".encode()
    return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).hexdigest()


async def _require_order_access(request: Request, order_id: str, customer_id: Any) -> None:
    provided = str(request.headers.get("x-order-access-token") or "").strip()
    expected = _order_access_token(order_id)
    if provided and hmac.compare_digest(provided, expected):
        return

    customer = await _optional_customer_from_request(request)
    if customer and customer_id is not None:
        try:
            if int(customer["id"]) == int(customer_id):
                return
        except (TypeError, ValueError):
            pass

    raise HTTPException(status_code=403, detail="Akses order tidak valid")


async def _promo_identity_from_request(
    request: Request,
    *,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
) -> PromoIdentity:
    """Resolve promo auth state without silently treating an inactive account as guest."""

    token = request.headers.get("customer-token")
    authorization = request.headers.get("authorization")
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return PromoIdentity(phone=str(phone or ""), target_id=str(target_id or ""))

    payload = decode_access_token(token)
    if not payload or payload.get("role") != "customer" or not payload.get("customer_id"):
        return PromoIdentity(phone=str(phone or ""), target_id=str(target_id or ""))
    try:
        customer_id = int(payload["customer_id"])
    except (TypeError, ValueError):
        return PromoIdentity(phone=str(phone or ""), target_id=str(target_id or ""))

    customer = await _customer_by_id(customer_id)
    return PromoIdentity(
        customer_id=customer_id,
        phone=str(phone or (customer or {}).get("phone") or ""),
        target_id=str(target_id or ""),
        is_authenticated=True,
        account_active=bool(customer and customer.get("active")),
    )


class TopupRequest(BaseModel):
    phone: Optional[str] = Field(default=None)
    target_id: Optional[str] = Field(default=None)
    nominal: Optional[str] = Field(default=None)
    method: Optional[str] = Field(default=None)
    promo_code: Optional[str] = Field(default=None)
    nickname: str = Field(default="-")
    idempotency_key: Optional[str] = Field(default=None, max_length=255)


class PromoQuoteRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=255)
    method: str = Field(default="QRIS", min_length=1, max_length=50)
    promo_code: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=30)
    target_id: Optional[str] = Field(default=None, max_length=255)


class CustomerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., min_length=8, max_length=30)
    email: Optional[str] = Field(default=None, max_length=255)
    password: str = Field(..., min_length=8)


class CustomerLoginRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=30)
    password: str = Field(..., min_length=8)


class CustomerProfileUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, min_length=8)


class SupportTicketCreateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    order_id: Optional[str] = None
    subject: str = Field(..., min_length=3, max_length=255)
    message: str = Field(..., min_length=5)


class WalletOpenPaymentRequest(BaseModel):
    method: str = Field(default="QRIS")
    amount: Optional[float] = Field(default=None, ge=1000)


class PostpaidInquiryRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=30)
    customer_no: str = Field(..., min_length=3, max_length=80)
    sku: str = Field(..., min_length=1, max_length=100)
    method: str = Field(default="QRIS")
    promo_code: Optional[str] = None


class PlnInquiryRequest(BaseModel):
    customer_no: str = Field(..., min_length=3, max_length=80)


@router.post("/api/customer/register")
async def customer_register(payload: CustomerRegisterRequest) -> Dict[str, Any]:
    phone = _normalize_phone(payload.phone)
    email = _clean_text(payload.email)
    name = _clean_text(payload.name)
    if not phone or len(phone) < 8:
        raise HTTPException(status_code=400, detail="Nomor HP tidak valid")
    if not name:
        raise HTTPException(status_code=400, detail="Nama customer wajib diisi")

    existing = await db_query(
        """
        SELECT id FROM customer_accounts
        WHERE phone=:phone OR (:email IS NOT NULL AND email=:email)
        """,
        {"phone": phone, "email": email},
    )
    if existing:
        raise HTTPException(status_code=400, detail="Nomor HP atau email sudah terdaftar")

    await db_execute(
        """
        INSERT INTO customer_accounts (name, phone, email, password, active)
        VALUES (:name, :phone, :email, :password, 1)
        """,
        {"name": name, "phone": phone, "email": email, "password": hash_password(payload.password)},
    )
    row = await db_query(
        "SELECT id, name, phone, email, password, active, created_at, updated_at FROM customer_accounts WHERE phone=:phone",
        {"phone": phone},
    )
    customer = _customer_public_dict(row[0], 0)
    await _wallet_balance(int(customer["id"]))
    token = create_access_token({"customer_id": customer["id"], "sub": phone, "role": "customer"})
    await _queue_notification(
        channel="WHATSAPP",
        recipient=phone,
        subject="Akun LIXAFA dibuat",
        body=f"Halo {name}, akun LIXAFA kamu berhasil dibuat.",
        reference_type="customer",
        reference_id=str(customer["id"]),
    )
    return {"success": True, "token": token, "customer": {**customer, "wallet_balance": 0}}


@router.post("/api/customer/login")
async def customer_login(payload: CustomerLoginRequest) -> Dict[str, Any]:
    phone = _normalize_phone(payload.phone)
    rows = await db_query(
        "SELECT id, name, phone, email, password, active, created_at, updated_at FROM customer_accounts WHERE phone=:phone",
        {"phone": phone},
    )
    if not rows or not verify_password(payload.password, rows[0][4]):
        raise HTTPException(status_code=401, detail="Nomor HP atau password tidak valid")
    if not rows[0][5]:
        raise HTTPException(status_code=403, detail="Akun customer nonaktif")

    balance = await _wallet_balance(int(rows[0][0]))
    customer = _customer_public_dict(rows[0], balance)
    token = create_access_token({"customer_id": customer["id"], "sub": phone, "role": "customer"})
    return {"success": True, "token": token, "customer": customer}


@router.get("/api/customer/me")
async def customer_me(customer: Dict[str, Any] = Depends(_require_customer)) -> Dict[str, Any]:
    return {"customer": customer}


@router.put("/api/customer/me")
async def update_customer_me(
    payload: CustomerProfileUpdateRequest,
    customer: Dict[str, Any] = Depends(_require_customer),
) -> Dict[str, Any]:
    updates: list[str] = []
    values: Dict[str, Any] = {"id": customer["id"]}

    if payload.name is not None:
        name = _clean_text(payload.name)
        if not name:
            raise HTTPException(status_code=400, detail="Nama customer wajib diisi")
        updates.append("name=:name")
        values["name"] = name

    if payload.email is not None:
        email = _clean_text(payload.email)
        if email:
            existing = await db_query(
                "SELECT id FROM customer_accounts WHERE email=:email AND id!=:id",
                {"email": email, "id": customer["id"]},
            )
            if existing:
                raise HTTPException(status_code=400, detail="Email sudah digunakan customer lain")
        updates.append("email=:email")
        values["email"] = email

    if payload.password is not None:
        updates.append("password=:password")
        values["password"] = hash_password(payload.password)

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data profil yang diperbarui")

    await db_execute(
        f"UPDATE customer_accounts SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        values,
    )
    refreshed = await _customer_by_id(int(customer["id"]))
    return {"success": True, "customer": refreshed}


@router.get("/api/customer/wallet")
async def customer_wallet(customer: Dict[str, Any] = Depends(_require_customer)) -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT entry_type, amount, balance_after, reference_type, reference_id, note, created_at
        FROM wallet_ledger
        WHERE customer_id=:customer_id
        ORDER BY created_at DESC
        LIMIT 50
        """,
        {"customer_id": customer["id"]},
    )
    return {
        "balance": await _wallet_balance(int(customer["id"])),
        "ledger": [
            {
                "entry_type": row[0],
                "amount": float(row[1] or 0),
                "balance_after": float(row[2] or 0),
                "reference_type": row[3] or "",
                "reference_id": row[4] or "",
                "note": row[5] or "",
                "created_at": _format_public_datetime(row[6]),
            }
            for row in rows
        ],
    }


@router.get("/api/payment/channels")
async def payment_channels() -> Any:
    response = await get_payment_channels()
    channels = _provider_channel_items(response)
    if not channels:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "channels": [],
                "source": "tripay",
                "reason_code": "PAYMENT_CHANNELS_UNAVAILABLE",
                "message": "Metode pembayaran belum dapat dimuat dari provider. Silakan coba lagi.",
            },
        )
    channels.append(
        {
            "code": "WALLET",
            "name": "Wallet LIXAFA",
            "group": "Saldo LIXAFA",
            "icon_url": "",
            "active": True,
            "maintenance": False,
            "minimum_amount": 0,
            "maximum_amount": 0,
            "raw": {},
        }
    )
    return {"success": True, "channels": channels, "source": "tripay"}


@router.get("/api/payment/quote")
async def payment_quote(
    request: Request,
    sku: str,
    method: str = "QRIS",
    promo_code: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Any:
    customer = await _optional_customer_from_request(request)
    promo_identity = await _promo_identity_from_request(request, phone=phone, target_id=target_id)
    method_code = normalize_payment_code(method or "QRIS")
    voucher_code = normalize_voucher_code(promo_code) or None
    try:
        product = await _resolve_effective_product_by_sku(
            sku,
            promo_code=voucher_code,
            payment_method=method_code,
            phone=phone,
            target_id=target_id,
            customer_id=(customer or {}).get("id"),
            identity=promo_identity,
        )
    except PromotionCodeError as exc:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(exc.reason, exc.detail),
        )
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return JSONResponse(
                status_code=exc.status_code,
                content=promotion_failure_payload("invalid_transaction_context", str(exc.detail)),
            )
        raise
    base_amount = _int_value(product.get("price"))
    fee, source = await _quote_payment_fee(base_amount, method_code)
    free_checkout = base_amount <= 0
    public_product = {key: value for key, value in product.items() if not key.startswith("_")}
    return {
        "success": True,
        "sku": product.get("sku") or normalize_sku(sku),
        "method": "FREE_PROMO" if free_checkout else method_code,
        "base_amount": base_amount,
        "payment_fee": fee,
        "total": base_amount + fee,
        "source": source,
        "free_checkout": free_checkout,
        "product": public_product,
    }


@router.post("/api/promos/quote")
async def promotion_quote(payload: PromoQuoteRequest, request: Request) -> Any:
    """Authoritative promotion quote shared with the real checkout engine."""

    customer = await _optional_customer_from_request(request)
    promo_identity = await _promo_identity_from_request(
        request,
        phone=payload.phone,
        target_id=payload.target_id,
    )
    method = normalize_payment_code(payload.method or "QRIS")
    promo_code = normalize_voucher_code(payload.promo_code) or None
    try:
        product = await _resolve_effective_product_by_sku(
            payload.sku,
            promo_code=promo_code,
            payment_method=method,
            phone=payload.phone,
            target_id=payload.target_id,
            customer_id=(customer or {}).get("id"),
            identity=promo_identity,
        )
    except PromotionCodeError as exc:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(exc.reason, exc.detail),
        )
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return JSONResponse(
                status_code=exc.status_code,
                content=promotion_failure_payload("invalid_transaction_context", str(exc.detail)),
            )
        raise
    quote = product.get("_promotion_quote")
    if not isinstance(quote, PromotionQuote):
        raise HTTPException(status_code=500, detail="Quote promo tidak dapat dibuat")
    payment_fee, fee_source = await _quote_payment_fee(quote.final_price, method)
    result = quote.to_dict(include_rejections=False)
    result.update(
        {
            "success": True,
            "sku": product.get("sku") or normalize_sku(payload.sku),
            "method": "FREE_PROMO" if quote.final_price <= 0 else method,
            "payment_fee": payment_fee,
            "total": quote.final_price + payment_fee,
            "fee_source": fee_source,
            "free_checkout": quote.final_price <= 0,
        }
    )
    return result


@router.get("/api/payment/instructions")
async def payment_instructions(method: str, pay_code: Optional[str] = None, amount: Optional[int] = None) -> Dict[str, Any]:
    response = await get_payment_instruction(method, pay_code=pay_code, amount=amount)
    if not response.get("success"):
        return {"success": False, "message": response.get("message") or "Instruksi pembayaran belum tersedia", "instructions": []}
    data = response.get("data")
    return {"success": True, "instructions": data if isinstance(data, list) else []}


async def _credit_wallet_deposit_if_new(customer_id: int, transaction: Dict[str, Any]) -> bool:
    return await credit_wallet_deposit_if_new(customer_id, transaction)


@router.post("/api/customer/wallet/open-payment")
async def customer_wallet_open_payment(
    payload: WalletOpenPaymentRequest,
    request: Request,
    customer: Dict[str, Any] = Depends(_require_customer),
) -> Dict[str, Any]:
    method = (payload.method or "QRIS").strip().upper()
    deposit_amount = int(_float_value(payload.amount))
    if deposit_amount > 0:
        fee, fee_source = await _quote_payment_fee(deposit_amount, method)
        total = int(deposit_amount + fee)
        merchant_ref = f"WALLETDEP-{customer['id']}-{uuid4().hex[:10].upper()}"
        await db_execute(
            """
            INSERT INTO customer_wallet_deposits (
                customer_id, provider, reference, merchant_ref, amount, fee, status, payload
            )
            VALUES (
                :customer_id, 'tripay', NULL, :merchant_ref, :amount, :fee, 'PENDING', :payload
            )
            """,
            {
                "customer_id": customer["id"],
                "merchant_ref": merchant_ref,
                "amount": deposit_amount,
                "fee": fee,
                "payload": _json_dumps({"invoice_outcome": "CREATION_PENDING"}),
            },
        )
        public_base_url = resolve_public_base_url(request)
        tripay_res: Any = None
        try:
            tripay_res = await create_invoice(
                order_id=merchant_ref,
                amount=total,
                method=method,
                customer_name=customer.get("name") or "Customer LIXAFA",
                customer_email=customer.get("email") or "customer@lixafa.id",
                customer_phone=customer.get("phone") or "",
                callback_url=f"{public_base_url}/callback",
                return_url=f"{public_base_url}/",
                expired_time=await _invoice_expired_time(),
                order_items=[{"name": "Deposit Wallet LIXAFA", "price": total, "quantity": 1}],
            )
        except Exception as exc:
            logger.warning(
                "Pembuatan invoice deposit wallet belum pasti customer=%s error_type=%s",
                customer.get("id"),
                type(exc).__name__,
            )

        invoice_outcome = classify_tripay_invoice_response(tripay_res)
        if invoice_outcome != TRIPAY_INVOICE_OUTCOME_SUCCESS:
            if invoice_outcome in {
                TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
                TRIPAY_INVOICE_OUTCOME_NOT_SENT,
            }:
                await db_execute(
                    """
                    UPDATE customer_wallet_deposits
                    SET status='FAILED', payload=:payload, last_check_at=CURRENT_TIMESTAMP
                    WHERE merchant_ref=:merchant_ref AND status='PENDING'
                    """,
                    {
                        "merchant_ref": merchant_ref,
                        "payload": _json_dumps(tripay_res or {}),
                    },
                )
                raise HTTPException(status_code=502, detail="Pembayaran belum dapat dibuat. Silakan coba lagi.")
            await db_execute(
                """
                UPDATE customer_wallet_deposits
                SET payload=:payload, last_check_at=CURRENT_TIMESTAMP
                WHERE merchant_ref=:merchant_ref AND status='PENDING'
                """,
                {
                    "merchant_ref": merchant_ref,
                    "payload": _json_dumps(tripay_res or {"invoice_outcome": "SENT_UNKNOWN"}),
                },
            )
            return JSONResponse(
                status_code=202,
                content={
                    "success": True,
                    "type": "invoice",
                    "merchant_ref": merchant_ref,
                    "amount": deposit_amount,
                    "payment_fee": int(fee),
                    "total": total,
                    "payment_creation_pending": True,
                    "message": "Pembuatan invoice deposit belum dapat dipastikan dan sedang direkonsiliasi. Jangan membuat pembayaran baru.",
                },
            )

        reference = str(tripay_res.get("reference") or "").strip()
        await db_execute(
            """
            UPDATE customer_wallet_deposits
            SET reference=:reference, payload=:payload, last_check_at=CURRENT_TIMESTAMP
            WHERE merchant_ref=:merchant_ref AND status='PENDING'
            """,
            {
                "reference": reference or None,
                "merchant_ref": merchant_ref,
                "payload": _json_dumps(tripay_res.get("raw") or tripay_res),
            },
        )
        return {
            "success": True,
            "type": "invoice",
            "reused": False,
            "reference": reference or merchant_ref,
            "merchant_ref": merchant_ref,
            "method": method,
            "payment_name": tripay_res.get("payment_name") or method,
            "pay_code": str(tripay_res.get("pay_code") or ""),
            "pay_url": tripay_res.get("pay_url") or "",
            "qr_url": tripay_res.get("qr_url") or "",
            "qr_string": tripay_res.get("qr_string") or "",
            "invoice_url": tripay_res.get("checkout_url") or tripay_res.get("pay_url") or "",
            "amount": deposit_amount,
            "payment_fee": int(fee),
            "total": total,
            "fee_source": fee_source,
            "expired_time": tripay_res.get("expired_time") or tripay_res.get("expired_at"),
            "payment_instructions": _payment_instructions_from_tripay(tripay_res),
        }

    existing = await db_query(
        """
        SELECT uuid, merchant_ref, method, payment_name, pay_code, qr_url, payload
        FROM customer_open_payments
        WHERE customer_id=:customer_id AND method=:method AND active=1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"customer_id": customer["id"], "method": method},
    )
    if existing:
        row = existing[0]
        return {
            "success": True,
            "reused": True,
            "uuid": row[0] or "",
            "merchant_ref": row[1] or "",
            "method": row[2] or method,
            "payment_name": row[3] or "",
            "pay_code": row[4] or "",
            "qr_url": row[5] or "",
        }

    merchant_ref = f"WALLET-{customer['id']}-{uuid4().hex[:10].upper()}"
    response = await create_open_payment(method, merchant_ref, customer.get("name") or "Customer LIXAFA")
    if not response.get("success"):
        message = response.get("message") or "Gagal membuat Open Payment Tripay"
        if "Sandbox credential" in message or "Sandbox API" in message:
            message = (
                "Open Payment Tripay tidak tersedia untuk credential sandbox. "
                "Masukkan nominal deposit agar sistem membuat invoice Tripay, atau gunakan credential production."
            )
        raise HTTPException(status_code=400, detail=message)

    data = response.get("data") or {}
    await db_execute(
        """
        INSERT INTO customer_open_payments (
            customer_id, uuid, merchant_ref, method, payment_name, pay_code, qr_url, active, payload
        )
        VALUES (
            :customer_id, :uuid, :merchant_ref, :method, :payment_name, :pay_code, :qr_url, 1, :payload
        )
        """,
        {
            "customer_id": customer["id"],
            "uuid": data.get("uuid") or "",
            "merchant_ref": merchant_ref,
            "method": method,
            "payment_name": data.get("payment_name") or data.get("method") or method,
            "pay_code": str(data.get("pay_code") or ""),
            "qr_url": data.get("qr_url") or "",
            "payload": _json_dumps(data),
        },
    )
    return {
        "success": True,
        "reused": False,
        "uuid": data.get("uuid") or "",
        "merchant_ref": merchant_ref,
        "method": method,
        "payment_name": data.get("payment_name") or method,
        "pay_code": str(data.get("pay_code") or ""),
        "qr_url": data.get("qr_url") or "",
    }


@router.post("/api/customer/wallet/sync-open-payment")
async def sync_customer_wallet_open_payment(customer: Dict[str, Any] = Depends(_require_customer)) -> Dict[str, Any]:
    rows = await db_query(
        "SELECT uuid FROM customer_open_payments WHERE customer_id=:customer_id AND active=1",
        {"customer_id": customer["id"]},
    )
    credited = 0
    for row in rows:
        uuid_value = row[0]
        if not uuid_value:
            continue
        response = await list_open_payment_transactions(uuid_value)
        transactions = response.get("data") if isinstance(response, dict) else []
        if isinstance(transactions, dict):
            transactions = transactions.get("data") or transactions.get("transactions") or []
        if not isinstance(transactions, list):
            continue
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue
            status = str(transaction.get("status") or "").upper()
            if status and status not in {"PAID", "SUCCESS", "SETTLED"}:
                continue
            if await _credit_wallet_deposit_if_new(int(customer["id"]), transaction):
                credited += 1

    pending_rows = await db_query(
        """
        SELECT reference, merchant_ref
        FROM customer_wallet_deposits
        WHERE customer_id=:customer_id AND status='PENDING'
        ORDER BY created_at DESC
        LIMIT 25
        """,
        {"customer_id": customer["id"]},
    )
    for reference, merchant_ref in pending_rows:
        if not reference:
            continue
        response = await check_transaction_status(str(reference))
        if not response.get("success"):
            continue
        transaction = response.get("data") or {}
        if not isinstance(transaction, dict):
            continue
        transaction.setdefault("reference", reference)
        transaction.setdefault("merchant_ref", merchant_ref)
        status = str(transaction.get("status") or "").upper()
        if status == "PAID":
            if await _credit_wallet_deposit_if_new(int(customer["id"]), transaction):
                credited += 1
        elif status in {"EXPIRED", "FAILED", "REFUND"}:
            await db_execute(
                "UPDATE customer_wallet_deposits SET status=:status, payload=:payload WHERE reference=:reference",
                {"status": status, "payload": _json_dumps(transaction), "reference": reference},
            )
    return {"success": True, "credited": credited, "balance": await _wallet_balance(int(customer["id"]))}


@router.post("/api/pln/inquiry")
async def api_pln_inquiry(payload: PlnInquiryRequest) -> Dict[str, Any]:
    response = await inquiry_pln(payload.customer_no.strip())
    data = response.get("data") if isinstance(response, dict) else {}
    if not isinstance(data, dict):
        data = {}
    if str(data.get("rc") or "") not in {"", "00"} and str(data.get("status") or "").lower() not in {"sukses", "success"}:
        raise HTTPException(status_code=400, detail=data.get("message") or "Inquiry PLN gagal")
    return {"success": True, "data": data}


@router.get("/api/customer/orders")
async def customer_orders(customer: Dict[str, Any] = Depends(_require_customer)) -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT
            t.id,
            t.target_id,
            t.nickname,
            t.nominal,
            t.amount,
            t.payment_method,
            t.payment_status,
            t.topup_status,
            t.sn,
            t.note,
            t.invoice_url,
            t.created_at,
            COALESCE(p.name, t.nominal) AS product_name,
            COALESCE(p.provider, '') AS provider,
            t.status_updated_at
        FROM topup t
        LEFT JOIN products p ON p.sku=t.nominal
        WHERE t.customer_id=:customer_id
        ORDER BY t.created_at DESC
        LIMIT 50
        """,
        {"customer_id": customer["id"]},
    )
    return {
        "orders": [
            {
                "id": row[0],
                "target_id": row[1] or "",
                "nickname": row[2] or "",
                "nominal": row[3] or "",
                "amount": float(row[4] or 0),
                "payment_method": row[5] or "",
                "payment_status": row[6] or "",
                "topup_status": row[7] or "",
                "sn": row[8] or "",
                "note": row[9] or "",
                "invoice_url": row[10] or "",
                "created_at": _format_public_datetime(row[11]),
                "product_name": row[12] or row[3] or "",
                "provider": row[13] or "",
                "status_updated_at": _format_public_datetime(row[14]),
            }
            for row in rows
        ]
    }


@router.post("/api/support/tickets")
async def create_support_ticket(request: Request, payload: SupportTicketCreateRequest) -> Dict[str, Any]:
    customer = await _optional_customer_from_request(request)
    name = _clean_text(payload.name) or (customer or {}).get("name")
    phone = _normalize_phone(payload.phone) or (customer or {}).get("phone")
    email = _clean_text(payload.email) or (customer or {}).get("email")
    subject = _clean_text(payload.subject)
    message = _clean_text(payload.message)

    if not customer and not phone and not email:
        raise HTTPException(status_code=400, detail="Nomor HP atau email wajib diisi")
    if not subject or not message:
        raise HTTPException(status_code=400, detail="Subjek dan pesan wajib diisi")

    await db_execute(
        """
        INSERT INTO support_tickets (
            customer_id, name, phone, email, order_id, subject, message, status, priority
        )
        VALUES (
            :customer_id, :name, :phone, :email, :order_id, :subject, :message, 'OPEN', 'NORMAL'
        )
        """,
        {
            "customer_id": (customer or {}).get("id"),
            "name": name,
            "phone": phone,
            "email": email,
            "order_id": _clean_text(payload.order_id),
            "subject": subject,
            "message": message,
        },
    )
    rows = await db_query(
        """
        SELECT id FROM support_tickets
        WHERE COALESCE(phone, '')=:phone AND subject=:subject
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"phone": phone or "", "subject": subject},
    )
    ticket_id = rows[0][0] if rows else ""
    if phone:
        await _queue_notification(
            channel="WHATSAPP",
            recipient=phone,
            subject="Ticket support diterima",
            body=f"Ticket kamu sudah diterima. ID ticket: {ticket_id}",
            reference_type="support_ticket",
            reference_id=str(ticket_id),
        )
    return {"success": True, "message": "Ticket support berhasil dibuat", "ticket_id": ticket_id}


@router.get("/api/customer/tickets")
async def customer_tickets(customer: Dict[str, Any] = Depends(_require_customer)) -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT id, order_id, subject, message, status, priority, admin_note, created_at, updated_at
        FROM support_tickets
        WHERE customer_id=:customer_id
        ORDER BY created_at DESC
        LIMIT 50
        """,
        {"customer_id": customer["id"]},
    )
    return {
        "tickets": [
            {
                "id": row[0],
                "order_id": row[1] or "",
                "subject": row[2] or "",
                "message": row[3] or "",
                "status": row[4] or "",
                "priority": row[5] or "",
                "admin_note": row[6] or "",
                "created_at": _format_public_datetime(row[7]),
                "updated_at": _format_public_datetime(row[8]),
            }
            for row in rows
        ]
    }


@router.post("/api/postpaid/inquiry")
async def postpaid_inquiry_order(payload: PostpaidInquiryRequest, request: Request) -> Any:
    phone = payload.phone.strip()
    customer_no = payload.customer_no.strip()
    method = normalize_payment_code(payload.method or "QRIS")
    promo_code = normalize_voucher_code(payload.promo_code) or None

    await _ensure_customer_allowed(phone, customer_no)
    customer = await _optional_customer_from_request(request)
    promo_identity = await _promo_identity_from_request(request, phone=phone, target_id=customer_no)
    wallet_requested = method == "WALLET"
    if wallet_requested and not customer:
        raise HTTPException(status_code=401, detail="Login customer diperlukan untuk membayar dengan wallet")
    if wallet_requested and _normalize_phone(phone) != _normalize_phone(customer["phone"]):
        raise HTTPException(status_code=400, detail="Nomor WA order harus sama dengan akun customer untuk pembayaran wallet")

    # Postpaid discount must be calculated from the authoritative inquiry
    # amount, not from the catalog placeholder price.
    product = await _resolve_effective_product_by_sku(
        payload.sku,
        promo_code=None,
        payment_method=method,
        phone=phone,
        target_id=customer_no,
        customer_id=(customer or {}).get("id"),
        identity=promo_identity,
    )
    sku = str(product.get("sku") or normalize_sku(payload.sku))
    if product.get("product_type") != "postpaid":
        raise HTTPException(status_code=400, detail="Produk ini bukan produk pascabayar")
    if not product.get("buyer_product_status") or not product.get("seller_product_status"):
        raise HTTPException(status_code=400, detail="Produk sedang tidak tersedia dari provider")

    order_id = create_order_id()
    response = await inquiry_postpaid(sku, customer_no, order_id)
    inquiry_data = response.get("data") if isinstance(response, dict) else {}
    if not isinstance(inquiry_data, dict):
        inquiry_data = {}
    status_text = str(inquiry_data.get("status") or "").lower()
    rc = str(inquiry_data.get("rc") or "")
    if status_text not in {"sukses", "success"} and rc not in {"", "00"}:
        raise HTTPException(status_code=400, detail=inquiry_data.get("message") or "Cek tagihan gagal")

    customer_name = inquiry_data.get("customer_name") or inquiry_data.get("name") or "-"
    provider_cost = _int_value(inquiry_data.get("price") or inquiry_data.get("amount") or product.get("product_cost"))
    base_amount = _int_value(inquiry_data.get("selling_price") or inquiry_data.get("total_bayar") or inquiry_data.get("total_amount"))
    if base_amount <= 0:
        admin_from_provider = _int_value(inquiry_data.get("admin") or inquiry_data.get("admin_fee"))
        base_amount = provider_cost + admin_from_provider
    if base_amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal tagihan tidak ditemukan dari provider")

    expected_quote = await quote_product(
        product,
        base_price=base_amount,
        promo_code=promo_code,
        payment_method=method,
        phone=phone,
        target_id=customer_no,
        customer_id=(customer or {}).get("id"),
        identity=promo_identity,
    )
    if promo_code and not expected_quote.code_valid:
        reason, detail = code_failure_reason(expected_quote)
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(reason, detail),
        )
    discounted_amount = expected_quote.final_price
    free_checkout = discounted_amount <= 0
    wallet_payment = wallet_requested and not free_checkout
    effective_payment_method = "FREE_PROMO" if free_checkout else method
    fee, fee_source = (0, "free_checkout") if free_checkout else await _quote_payment_fee(discounted_amount, method)
    total_bayar = discounted_amount + fee
    if wallet_payment and customer and await _wallet_balance(int(customer["id"])) < total_bayar:
        raise HTTPException(status_code=400, detail="Saldo wallet tidak cukup")

    payment_status = "PAID" if free_checkout else "UNPAID"
    topup_status = "PROCESSING" if free_checkout else "PENDING_PAYMENT"
    invoice_expired_unix = None if wallet_payment or free_checkout else await _invoice_expired_time()
    reservation_expires_at = (
        datetime.fromtimestamp(invoice_expired_unix, tz=timezone.utc) if invoice_expired_unix else None
    )

    async def persist_postpaid_order(connection, confirmed_quote: PromotionQuote, snapshot: dict[str, Any]) -> None:
        promo_values = order_promo_values(confirmed_quote, snapshot)
        await connection.execute(
            text(
                """
        INSERT INTO topup (
            id, order_type, phone, target_id, nickname, nominal, price, product_cost,
            amount, payment_method, payment_fee, promo_code, promo_original_price,
            promo_discount_amount, promo_id, promo_name, promo_type, promo_discount_type,
            promo_snapshot, promo_snapshot_version, customer_ip, customer_id,
            payment_status, topup_status, provider_ref_id, provider_rc, provider_price,
            provider_selling_price, provider_payload, note, status_updated_at
        )
        VALUES (
            :id, 'POSTPAID', :phone, :target_id, :nickname, :nominal, :price, :product_cost,
            :amount, :payment_method, :payment_fee, :promo_code, :promo_original_price,
            :promo_discount_amount, :promo_id, :promo_name, :promo_type, :promo_discount_type,
            :promo_snapshot, :promo_snapshot_version, :customer_ip, :customer_id,
            :payment_status, :topup_status, :provider_ref_id, :provider_rc, :provider_price,
            :provider_selling_price, :provider_payload, :note, CURRENT_TIMESTAMP
        )
        """
            ),
            {
                "id": order_id,
                "phone": phone,
                "target_id": customer_no,
                "nickname": customer_name,
                "nominal": sku,
                "price": confirmed_quote.final_price,
                "product_cost": provider_cost,
                "amount": total_bayar,
                "payment_method": effective_payment_method,
                "payment_fee": fee,
                "promo_code": promo_code,
                **promo_values,
                "customer_ip": _client_ip(request),
                "customer_id": (customer or {}).get("id"),
                "payment_status": payment_status,
                "topup_status": topup_status,
                "provider_ref_id": inquiry_data.get("ref_id") or order_id,
                "provider_rc": inquiry_data.get("rc") or "",
                "provider_price": provider_cost,
                "provider_selling_price": base_amount,
                "provider_payload": _json_dumps(inquiry_data),
                "note": inquiry_data.get("message") or f"Postpaid inquiry. Fee source: {fee_source}",
            },
        )

    try:
        expected_quote = await persist_order_with_promotions(
            order_id=order_id,
            product=product,
            expected_quote=expected_quote,
            persist_order=persist_postpaid_order,
            base_price=base_amount,
            promo_code=promo_code,
            payment_method=method,
            phone=phone,
            target_id=customer_no,
            customer_id=(customer or {}).get("id"),
            identity=promo_identity,
            redemption_status="REDEEMED" if wallet_payment or free_checkout else "RESERVED",
            expires_at=reservation_expires_at,
        )
    except PromotionCodeError as exc:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(exc.reason, exc.detail),
        )
    except PromotionChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if free_checkout:
        return {
            "success": True,
            "id": order_id,
            "order_access_token": _order_access_token(order_id),
            "payment_method": "FREE_PROMO",
            "free_checkout": True,
            "customer_name": customer_name,
            "base_amount": base_amount,
            "discount_amount": expected_quote.discount_amount,
            "final_price": 0,
            "payment_fee": 0,
            "total": 0,
            "provider_data": inquiry_data,
        }

    if wallet_payment and customer:
        try:
            await _wallet_add_entry(
                customer_id=int(customer["id"]),
                entry_type="DEBIT",
                amount=total_bayar,
                reference_type="postpaid",
                reference_id=order_id,
                note=f"Pembayaran wallet tagihan {order_id}",
            )
            await db_execute(
                "UPDATE topup SET payment_status='PAID', topup_status='PROCESSING', status_updated_at=CURRENT_TIMESTAMP WHERE id=:id AND payment_status='UNPAID' AND topup_status='PENDING_PAYMENT'",
                {"id": order_id},
            )
        except Exception:
            await db_execute(
                "UPDATE topup SET payment_status='FAILED', topup_status='FAILED', status_updated_at=CURRENT_TIMESTAMP WHERE id=:id AND payment_status='UNPAID'",
                {"id": order_id},
            )
            await release_order_promotions(order_id)
            raise
        return {
            "success": True,
            "id": order_id,
            "order_access_token": _order_access_token(order_id),
            "wallet_paid": True,
            "customer_name": customer_name,
            "base_amount": int(base_amount),
            "discount_amount": expected_quote.discount_amount,
            "final_price": expected_quote.final_price,
            "payment_fee": int(fee),
            "total": total_bayar,
            "provider_data": inquiry_data,
        }

    tripay_res: Any = None
    try:
        public_base_url = resolve_public_base_url(request)
        tripay_res = await create_invoice(
            order_id=order_id,
            amount=total_bayar,
            method=method,
            customer_name=customer_name if customer_name != "-" else "Customer LIXAFA",
            customer_email=(customer or {}).get("email") or "customer@lixafa.id",
            customer_phone=phone,
            callback_url=f"{public_base_url}/callback",
            return_url=f"{public_base_url}/",
            expired_time=invoice_expired_unix or await _invoice_expired_time(),
            order_items=[{"name": product.get("name") or "Tagihan LIXAFA", "price": total_bayar, "quantity": 1}],
        )
    except Exception as exc:
        logger.warning(
            "Pembuatan invoice Tripay postpaid belum pasti order=%s error_type=%s",
            order_id,
            type(exc).__name__,
        )

    invoice_outcome = classify_tripay_invoice_response(tripay_res)
    if invoice_outcome != TRIPAY_INVOICE_OUTCOME_SUCCESS:
        if invoice_outcome in {
            TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
            TRIPAY_INVOICE_OUTCOME_NOT_SENT,
        }:
            await _mark_invoice_creation_failed(order_id, tripay_res)
            raise HTTPException(status_code=502, detail="Pembayaran belum dapat dibuat. Silakan coba lagi.")
        await _mark_invoice_creation_unknown(order_id, tripay_res)
        return _invoice_creation_pending_response(
            order_id=order_id,
            payment_method=method,
            total=total_bayar,
        )

    await _update_order_payment_metadata(order_id, tripay_res)
    return {
        "success": True,
        "id": order_id,
        "order_access_token": _order_access_token(order_id),
        "invoice_url": tripay_res.get("checkout_url") or tripay_res.get("pay_url"),
        "qr_url": tripay_res.get("qr_url") or "",
        "qr_string": tripay_res.get("qr_string") or "",
        "pay_code": tripay_res.get("pay_code") or "",
        "pay_url": tripay_res.get("pay_url") or "",
        "payment_name": tripay_res.get("payment_name") or "",
        "payment_method": method,
        "payment_instructions": _payment_instructions_from_tripay(tripay_res),
        "customer_name": customer_name,
        "base_amount": int(base_amount),
        "discount_amount": expected_quote.discount_amount,
        "final_price": expected_quote.final_price,
        "payment_fee": int(fee),
        "total": total_bayar,
        "provider_data": inquiry_data,
    }


@router.post("/topup")
async def topup(data: TopupRequest, request: Request) -> Any:
    wa_pembeli = data.phone
    target_id = data.target_id
    sku = data.nominal
    method = normalize_payment_code(data.method)
    promo_code = normalize_voucher_code(data.promo_code) or None
    nickname = data.nickname or "-"

    if not wa_pembeli or not target_id or not sku or not method:
        raise HTTPException(status_code=400, detail="phone, target_id, nominal, dan method wajib diisi")

    idempotency_key = (data.idempotency_key or request.headers.get("Idempotency-Key") or "").strip()
    existing_order_id = await _existing_order_for_idempotency(idempotency_key)
    if existing_order_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Request order sudah pernah diproses",
                "order_id": existing_order_id,
                "idempotent": True,
            },
        )

    await _ensure_customer_allowed(wa_pembeli, target_id)
    customer = await _optional_customer_from_request(request)
    promo_identity = await _promo_identity_from_request(
        request,
        phone=wa_pembeli,
        target_id=target_id,
    )

    # Gunakan harga efektif setelah aturan promo aktif diterapkan.
    try:
        product = await _resolve_effective_product_by_sku(
            sku,
            promo_code=promo_code,
            payment_method=method,
            phone=wa_pembeli,
            target_id=target_id,
            customer_id=(customer or {}).get("id"),
            identity=promo_identity,
        )
    except PromotionCodeError as exc:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(exc.reason, exc.detail),
        )
    sku = str(product.get("sku") or normalize_sku(sku))
    if product.get("product_type") != "prepaid":
        raise HTTPException(status_code=400, detail="Produk pascabayar wajib dicek tagihan dulu")
    if not product.get("buyer_product_status") or not product.get("seller_product_status"):
        raise HTTPException(status_code=400, detail="Produk sedang tidak tersedia dari provider")
    if product.get("stock") is not None and not product.get("unlimited_stock") and int(product.get("stock") or 0) <= 0:
        raise HTTPException(status_code=400, detail="Stok provider sedang habis")
    expected_quote = product.get("_promotion_quote")
    if not isinstance(expected_quote, PromotionQuote):
        raise HTTPException(status_code=500, detail="Quote promo checkout tidak tersedia")
    price_val = expected_quote.final_price
    original_price_val = expected_quote.base_price
    promo_discount_amount = expected_quote.discount_amount
    product_cost = _int_value(product.get("product_cost"))
    free_checkout = price_val <= 0
    wallet_payment = method == "WALLET" and not free_checkout
    payment_method = "FREE_PROMO" if free_checkout else method
    if wallet_payment and not customer:
        raise HTTPException(status_code=401, detail="Login customer diperlukan untuk membayar dengan wallet")
    if wallet_payment and _normalize_phone(wa_pembeli) != _normalize_phone(customer["phone"]):
        raise HTTPException(status_code=400, detail="Nomor WA order harus sama dengan akun customer untuk pembayaran wallet")

    admin_fee, fee_source = (0, "free_checkout") if free_checkout else await _quote_payment_fee(price_val, method)
    total_bayar = int(price_val + admin_fee)
    order_id = create_order_id()
    payment_status = "PAID" if wallet_payment or free_checkout else "UNPAID"
    topup_status = "PROCESSING" if wallet_payment or free_checkout else "PENDING_PAYMENT"
    if wallet_payment and customer and await _wallet_balance(int(customer["id"])) < total_bayar:
        raise HTTPException(status_code=400, detail="Saldo wallet tidak cukup")

    invoice_expired_unix = None if wallet_payment or free_checkout else await _invoice_expired_time()
    reservation_expires_at = (
        datetime.fromtimestamp(invoice_expired_unix, tz=timezone.utc) if invoice_expired_unix else None
    )

    async def persist_prepaid_order(connection, confirmed_quote: PromotionQuote, snapshot: dict[str, Any]) -> None:
        await _bind_order_idempotency(connection, idempotency_key, order_id)
        promo_values = order_promo_values(confirmed_quote, snapshot)
        await connection.execute(
            text(
                """
            INSERT INTO topup (
                id, order_type, phone, target_id, nickname, nominal, price, product_cost,
                amount, payment_method, payment_fee, promo_code, promo_original_price,
                promo_discount_amount, promo_id, promo_name, promo_type,
                promo_discount_type, promo_snapshot, promo_snapshot_version, customer_ip,
                customer_id, payment_status, topup_status, note, status_updated_at
            )
            VALUES (
                :id, :order_type, :phone, :target_id, :nickname, :nominal, :price, :product_cost,
                :amount, :payment_method, :payment_fee, :promo_code, :promo_original_price,
                :promo_discount_amount, :promo_id, :promo_name, :promo_type,
                :promo_discount_type, :promo_snapshot, :promo_snapshot_version, :customer_ip,
                :customer_id, :payment_status, :topup_status, :note, CURRENT_TIMESTAMP
            )
            """
            ),
            {
                "id": order_id,
                "order_type": "PREPAID",
                "phone": wa_pembeli,
                "target_id": target_id,
                "nickname": nickname,
                "nominal": sku,
                "price": price_val,
                "product_cost": product_cost,
                "amount": total_bayar,
                "payment_method": payment_method,
                "payment_fee": admin_fee,
                "promo_code": promo_code,
                **promo_values,
                "customer_ip": _client_ip(request),
                "customer_id": (customer or {}).get("id"),
                "payment_status": payment_status,
                "topup_status": topup_status,
                "note": "Promo gratis 100% - tanpa pembayaran Tripay" if free_checkout else f"Payment fee source: {fee_source}",
            },
        )

    try:
        expected_quote = await persist_order_with_promotions(
            order_id=order_id,
            product=product,
            expected_quote=expected_quote,
            persist_order=persist_prepaid_order,
            base_price=original_price_val,
            promo_code=promo_code,
            payment_method=method,
            phone=wa_pembeli,
            target_id=target_id,
            customer_id=(customer or {}).get("id"),
            identity=promo_identity,
            redemption_status="REDEEMED" if wallet_payment or free_checkout else "RESERVED",
            expires_at=reservation_expires_at,
        )
    except PromotionCodeError as exc:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload(exc.reason, exc.detail),
        )
    except PromotionChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        logger.exception("Gagal menyimpan order prepaid")
        raise HTTPException(status_code=500, detail="Gagal menyimpan pesanan")

    if free_checkout:
        await _queue_notification(
            channel="WHATSAPP",
            recipient=wa_pembeli,
            subject="Order promo gratis diproses",
            body=f"Order {order_id} memakai promo gratis dan langsung diproses.",
            reference_type="topup",
            reference_id=order_id,
        )
        return {
            "id": order_id,
            "order_access_token": _order_access_token(order_id),
            "invoice_url": "",
            "qr_url": "",
            "pay_code": "",
            "payment_name": "Promo Gratis",
            "payment_method": "FREE_PROMO",
            "payment_fee": 0,
            "total": 0,
            "free_checkout": True,
            "message": "Promo gratis 100%, order langsung diproses tanpa Tripay.",
        }

    if wallet_payment and customer:
        try:
            await _wallet_add_entry(
                customer_id=int(customer["id"]),
                entry_type="DEBIT",
                amount=total_bayar,
                reference_type="topup",
                reference_id=order_id,
                note=f"Pembayaran wallet order {order_id}",
            )
            await db_execute(
                """
                UPDATE topup
                SET payment_status='PAID', topup_status='PROCESSING',
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND payment_status='UNPAID' AND topup_status='PENDING_PAYMENT'
                """,
                {"id": order_id},
            )
        except Exception:
            await db_execute(
                """
                UPDATE topup
                SET payment_status='FAILED', topup_status='FAILED',
                    note=COALESCE(NULLIF(note, ''), 'Debit wallet gagal'),
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND payment_status='UNPAID'
                """,
                {"id": order_id},
            )
            await release_order_promotions(order_id)
            raise
        await _queue_notification(
            channel="WHATSAPP",
            recipient=wa_pembeli,
            subject="Order dibayar dengan wallet",
            body=f"Order {order_id} dibayar dari wallet dan sedang diproses.",
            reference_type="topup",
            reference_id=order_id,
        )
        return {
            "id": order_id,
            "order_access_token": _order_access_token(order_id),
            "invoice_url": "",
            "qr_url": "",
            "wallet_paid": True,
        }

    tripay_res: Any = None
    try:
        # WAJIB AWAIT
        public_base_url = resolve_public_base_url(request)
        tripay_res = await create_invoice(
            order_id=order_id,
            amount=total_bayar,
            method=method,
            customer_name="Customer LIXAFA",
            customer_email="customer@lixafa.id",
            customer_phone=wa_pembeli,
            callback_url=f"{public_base_url}/callback",
            return_url=f"{public_base_url}/",
            expired_time=invoice_expired_unix or await _invoice_expired_time(),
            order_items=[{"name": product.get("name") or "LIXAFA PROJEK Top-up", "price": total_bayar, "quantity": 1}],
        )

    except Exception as exc:
        logger.warning(
            "Pembuatan invoice Tripay prepaid belum pasti order=%s error_type=%s",
            order_id,
            type(exc).__name__,
        )

    invoice_outcome = classify_tripay_invoice_response(tripay_res)
    if invoice_outcome != TRIPAY_INVOICE_OUTCOME_SUCCESS:
        if invoice_outcome in {
            TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
            TRIPAY_INVOICE_OUTCOME_NOT_SENT,
        }:
            await _mark_invoice_creation_failed(order_id, tripay_res)
            raise HTTPException(status_code=502, detail="Pembayaran belum dapat dibuat. Silakan coba lagi.")
        await _mark_invoice_creation_unknown(order_id, tripay_res)
        return _invoice_creation_pending_response(
            order_id=order_id,
            payment_method=method,
            total=total_bayar,
        )

    await _update_order_payment_metadata(order_id, tripay_res)
    invoice_url = tripay_res.get("checkout_url") or tripay_res.get("pay_url")
    qr_url = tripay_res.get("qr_url")
    await _queue_notification(
        channel="WHATSAPP",
        recipient=wa_pembeli,
        subject="Invoice topup dibuat",
        body=f"Invoice order {order_id} sudah dibuat. Silakan selesaikan pembayaran.",
        reference_type="topup",
        reference_id=order_id,
    )

    return {
        "id": order_id,
        "order_access_token": _order_access_token(order_id),
        "invoice_url": invoice_url,
        "qr_url": qr_url or "",
        "pay_code": tripay_res.get("pay_code") or "",
        "pay_url": tripay_res.get("pay_url") or "",
        "payment_name": tripay_res.get("payment_name") or "",
        "payment_method": method,
        "qr_string": tripay_res.get("qr_string") or "",
        "payment_instructions": _payment_instructions_from_tripay(tripay_res),
        "expired_time": tripay_res.get("expired_time") or tripay_res.get("expired_at"),
        "payment_fee": admin_fee,
        "total": total_bayar,
    }

@router.get("/topup/{identifier}")
async def cek_status_pesanan(identifier: str, request: Request):
    """Return an order only to its customer or holder of its access capability."""
    rows = await db_query(
        """
        SELECT
            t.id,
            t.topup_status AS status,
            t.invoice_url,
            t.target_id,
            t.nominal AS nominal_name,
            t.phone,
            t.payment_status,
            t.sn,
            t.note,
            t.amount,
            t.payment_method,
            t.price,
            t.payment_fee,
            t.promo_code,
            t.created_at,
            COALESCE(t.status_updated_at, t.created_at) AS status_updated_at,
            COALESCE(p.name, t.nominal) AS product_name,
            COALESCE(p.provider, '') AS provider,
            COALESCE(p.category, '') AS category,
            t.nickname,
            COALESCE(t.order_type, 'PREPAID') AS order_type,
            t.payment_name,
            t.pay_code,
            t.pay_url,
            t.qr_url,
            t.qr_string,
            t.payment_expired_at,
            t.tripay_payload,
            t.customer_id,
            COALESCE(t.payment_creation_outcome, '') AS payment_creation_outcome
        FROM topup t
        LEFT JOIN products p ON p.sku=t.nominal
        WHERE t.id = :identifier
        LIMIT 1
        """,
        {"identifier": identifier},
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan. Pastikan Order ID benar.")

    row = rows[0]
    await _require_order_access(request, str(row[0]), row[28])

    raw_status = (row[1] or "").upper()
    payment_status = (row[6] or "").upper()

    if raw_status == "SUCCESS":
        stage = "success"
    elif raw_status == "FAILED":
        stage = "failed"
    elif raw_status == "PENDING_PROVIDER":
        stage = "provider_pending"
    elif payment_status == "PAID" or raw_status in {"PROCESSING", "PAID"}:
        stage = "processing"
    else:
        stage = "pending_payment"

    timeline = [
        {
            "key": "created",
            "label": "Order dibuat",
            "done": True,
            "active": stage == "pending_payment" and payment_status != "PAID",
            "description": "Pesanan sudah tercatat di sistem.",
        },
        {
            "key": "paid",
            "label": "Pembayaran diterima",
            "done": payment_status in {"PAID", "REFUNDED"},
            "active": stage == "pending_payment",
            "description": "Menunggu pembayaran dari customer." if payment_status != "PAID" else "Pembayaran sudah masuk.",
        },
        {
            "key": "provider",
            "label": "Dikirim ke provider",
            "done": raw_status in {"PENDING_PROVIDER", "SUCCESS", "FAILED"},
            "active": stage in {"processing", "provider_pending"},
            "description": "Order sedang masuk antrean provider.",
        },
        {
            "key": "complete",
            "label": "Selesai",
            "done": raw_status == "SUCCESS",
            "active": stage in {"success", "failed"},
            "description": "Topup berhasil." if raw_status == "SUCCESS" else ("Topup gagal diproses." if raw_status == "FAILED" else "Menunggu hasil provider."),
        },
    ]

    return {
        "id": row[0],
        "status": row[1],
        "stage": stage,
        "invoice_url": row[2],
        "target_id": row[3],
        "nominal_name": row[4],
        "phone": row[5],
        "payment_status": row[6],
        "sn": row[7],
        "note": row[8],
        "amount": float(row[9] or 0),
        "payment_method": row[10] or "",
        "price": float(row[11] or 0),
        "payment_fee": float(row[12] or 0),
        "promo_code": row[13] or "",
        "created_at": _format_public_datetime(row[14]),
        "status_updated_at": _format_public_datetime(row[15]),
        "product_name": row[16] or row[4] or "",
        "provider": row[17] or "",
        "category": row[18] or "",
        "nickname": row[19] or "",
        "order_type": row[20] or "PREPAID",
        "payment_name": row[21] or "",
        "pay_code": row[22] or "",
        "pay_url": row[23] or "",
        "qr_url": row[24] or "",
        "qr_string": row[25] or "",
        "payment_expired_at": _format_public_datetime(row[26]),
        "timeline": timeline,
        "payment_instructions": _payment_instructions_from_payload(row[27]),
        "can_pay": payment_status == "UNPAID" and bool(row[2] or row[22] or row[24] or row[25]),
        "payment_creation_pending": str(row[29] or "").upper() == "SENT_UNKNOWN",
    }

# Buat schema model untuk request checker
class NicknameRequest(BaseModel):
    game_code: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    zone_id: str = Field(default="")

# Endpoint API baru
@router.post("/check-nickname")
async def api_check_nickname(req: NicknameRequest):
    nickname = await check_game_nickname(req.game_code, req.user_id, req.zone_id)

    if not nickname:
        # Mengembalikan error 400 jika ID tidak valid
        raise HTTPException(status_code=400, detail="ID Game tidak ditemukan atau salah ketik.")

    return {"status": "success", "nickname": nickname}


@router.get("/api/site-settings")
async def get_public_site_settings() -> Dict[str, Any]:
    rows = await db_query("SELECT key, value FROM site_settings")
    settings_map = {row[0]: row[1] for row in rows}
    return {
        "site_name": settings_map.get("site_name") or settings.app_name,
        "logo_url": settings_map.get("logo_url") or "",
        "footer_text": settings_map.get("footer_text") or "",
    }


@router.get("/api/storefront/stats")
async def get_storefront_stats() -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT
            (SELECT COUNT(*) FROM products WHERE active=1) AS active_products,
            (SELECT COUNT(DISTINCT provider) FROM products WHERE active=1) AS active_providers,
            (SELECT COUNT(*) FROM topup WHERE payment_status='PAID') AS paid_orders,
            (SELECT COUNT(*) FROM topup WHERE topup_status='SUCCESS') AS success_orders,
            (SELECT COUNT(*) FROM topup WHERE topup_status='FAILED') AS failed_orders
        """
    )
    row = rows[0] if rows else (0, 0, 0, 0, 0)
    promo_rows = await db_query("SELECT starts_at, ends_at, show_on_website, active FROM promos")
    active_promos = sum(
        1
        for promo_row in promo_rows
        if _promo_visible_on_website_now(
            starts_at=promo_row[0],
            ends_at=promo_row[1],
            show_on_website=bool(promo_row[2]),
            active=bool(promo_row[3]),
        )
    )
    paid_orders = int(row[2] or 0)
    success_orders = int(row[3] or 0)
    success_rate = round((success_orders / paid_orders) * 100, 2) if paid_orders else 0
    return {
        "active_products": int(row[0] or 0),
        "active_providers": int(row[1] or 0),
        "active_promos": active_promos,
        "paid_orders": paid_orders,
        "success_orders": success_orders,
        "failed_orders": int(row[4] or 0),
        "success_rate": success_rate,
    }


@router.get("/api/pages")
async def get_public_pages() -> Dict[str, Any]:
    rows = await db_query(
        f"""
        SELECT {_page_select_columns()}
        FROM site_pages
        WHERE active=1 AND show_on_website=1
        ORDER BY updated_at DESC
        """
    )
    return {"pages": [_public_page_dict(row) for row in rows]}


@router.get("/api/pages/{slug}")
async def get_public_page(slug: str) -> Dict[str, Any]:
    rows = await db_query(
        f"""
        SELECT {_page_select_columns()}
        FROM site_pages
        WHERE slug=:slug AND active=1 AND show_on_website=1
        """,
        {"slug": slug},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Halaman tidak ditemukan")
    return {"page": _public_page_dict(rows[0], include_content=True)}


@router.get("/promo/latest", response_class=HTMLResponse)
async def latest_promo_page():
    promos = await _load_public_active_promos()
    if promos:
        settings_data = await get_public_site_settings()
        site_name = html.escape(settings_data["site_name"])
        logo_url = html.escape(settings_data.get("logo_url") or "")
        logo_html = f'<img src="{logo_url}" alt="{site_name}" class="page-logo-img">' if logo_url else '<span class="page-logo-mark">L</span>'
        promo_count = len(promos)
        hero_title = f"{promo_count} Promo Aktif" if promo_count > 1 else "1 Promo Aktif"
        hero_desc = "Pilih promo yang paling cocok lalu lanjutkan checkout langsung dari website." if promo_count > 1 else "Promo ini sedang aktif dan siap dipakai untuk transaksi kamu."

        card_chunks: list[str] = []
        for promo in promos:
            promo_title = html.escape(promo.get("title") or "Promo LIXAFA")
            promo_badge = html.escape(promo.get("badge") or "Promo")
            promo_discount = html.escape(promo.get("discount_label") or "Promo spesial")
            promo_description = html.escape(promo.get("description") or "Promo terbatas, cek detail dan gunakan sebelum periode berakhir.")
            promo_target = html.escape(promo.get("target_label") or "Semua produk")
            promo_period = html.escape(promo.get("period_label") or "Selama promo aktif")
            promo_cta_text = html.escape(promo.get("cta_text") or "Pakai Promo")
            promo_cta_value = promo.get("resolved_cta_url") or promo.get("cta_url")
            allow_external_cta = bool(promo.get("allow_external_cta"))
            promo_cta_href = html.escape(_promo_cta_href(promo_cta_value, allow_external=allow_external_cta))
            promo_cta_attrs = ' target="_blank" rel="noopener"' if _promo_cta_is_external(promo_cta_value, allow_external=allow_external_cta) else ""
            promo_image = promo.get("image_url") or ""
            image_html = (
                f'<img src="{html.escape(_safe_url(promo_image, ""))}" alt="{promo_title}" class="promo-cover">'
                if promo_image
                else '<div class="promo-art-fallback">%</div>'
            )
            code_html = (
                f'<div class="promo-code-box"><span>Kode promo</span><strong>{html.escape(promo.get("code") or "")}</strong></div>'
                if promo.get("code")
                else ""
            )
            card_chunks.append(
                f"""
                <article class="promo-card">
                    {image_html}
                    <div class="promo-card-body">
                        <div class="promo-chip-row">
                            <span class="promo-chip">{promo_badge}</span>
                            <span class="promo-chip accent">{promo_discount}</span>
                        </div>
                        <h2>{promo_title}</h2>
                        <p class="promo-copy">{promo_description}</p>
                        <div class="promo-meta-grid">
                            <div class="promo-meta-item">
                                <span class="meta-label">Target</span>
                                <strong>{promo_target}</strong>
                            </div>
                            <div class="promo-meta-item">
                                <span class="meta-label">Periode</span>
                                <strong>{promo_period}</strong>
                            </div>
                        </div>
                        {code_html}
                        <div class="promo-card-actions">
                            <a class="primary-cta" href="{promo_cta_href}"{promo_cta_attrs}>{promo_cta_text}</a>
                            <a class="secondary-cta" href="/">Kembali ke Beranda</a>
                        </div>
                    </div>
                </article>
                """
            )
        cards_html = "".join(card_chunks)

        page_html = f"""
        <!DOCTYPE html>
        <html lang="id">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Promo Aktif - {site_name}</title>
            <style>
                :root {{ --accent-a:#EBC166; --accent-b:#9E6710; --bg:#050505; --panel:rgba(255,255,255,.045); --line:rgba(235,193,102,.22); --text:#f5f0e6; --muted:#cbbfa9; }}
                * {{ box-sizing:border-box; }}
                body {{ margin:0; background:var(--bg); color:var(--text); font-family:Arial, sans-serif; }}
                body::before {{ content:""; position:fixed; inset:0; pointer-events:none; background:radial-gradient(circle at 16% 10%, rgba(235,193,102,.14), transparent 28%), radial-gradient(circle at 84% 12%, rgba(255,255,255,.06), transparent 24%); }}
                .page-shell {{ width:min(1180px, 100%); margin:0 auto; padding:24px 18px 64px; position:relative; z-index:1; }}
                .page-nav {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:10px 0 28px; }}
                .page-logo {{ display:flex; align-items:center; gap:10px; color:#f2d074; text-decoration:none; font-weight:900; }}
                .page-logo-img {{ width:auto; max-width:160px; height:40px; object-fit:contain; }}
                .page-logo-mark {{ width:38px; height:38px; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; background:linear-gradient(135deg,var(--accent-b),var(--accent-a)); color:#050505; }}
                .page-back {{ color:#f2d074; text-decoration:none; border:1px solid rgba(235,193,102,.4); padding:9px 13px; border-radius:8px; }}
                .hero-card {{ padding:34px; border-radius:20px; border:1px solid var(--line); background:linear-gradient(135deg, rgba(255,255,255,.07), rgba(255,255,255,.02)); box-shadow:0 24px 80px rgba(0,0,0,.35); }}
                .hero-badge {{ display:inline-flex; padding:7px 12px; border-radius:999px; background:rgba(235,193,102,.14); color:var(--accent-a); font-size:12px; font-weight:900; margin-bottom:16px; }}
                h1 {{ margin:0 0 10px; font-size:clamp(34px, 6vw, 62px); line-height:1.02; color:#fff; }}
                .hero-copy {{ margin:0; max-width:700px; color:var(--muted); font-size:17px; line-height:1.7; }}
                .promo-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:18px; margin-top:24px; }}
                .promo-card {{ border:1px solid var(--line); border-radius:18px; overflow:hidden; background:linear-gradient(160deg, rgba(255,255,255,.055), rgba(0,0,0,.18)); }}
                .promo-cover {{ width:100%; aspect-ratio:16/8; object-fit:cover; display:block; }}
                .promo-art-fallback {{ width:100%; aspect-ratio:16/8; display:flex; align-items:center; justify-content:center; font-size:72px; font-weight:900; color:var(--accent-a); background:linear-gradient(135deg, rgba(235,193,102,.16), rgba(255,255,255,.04)); }}
                .promo-card-body {{ padding:18px; }}
                .promo-chip-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; }}
                .promo-chip {{ display:inline-flex; align-items:center; min-height:28px; padding:4px 10px; border-radius:999px; background:rgba(255,255,255,.06); color:#f1e7cf; font-size:11px; font-weight:800; }}
                .promo-chip.accent {{ background:rgba(235,193,102,.16); color:var(--accent-a); }}
                h2 {{ margin:0 0 8px; color:#fff; font-size:24px; line-height:1.2; }}
                .promo-copy {{ margin:0; color:var(--muted); font-size:14px; line-height:1.7; min-height:72px; }}
                .promo-meta-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-top:16px; }}
                .promo-meta-item {{ border:1px solid rgba(255,255,255,.08); border-radius:12px; padding:12px; background:rgba(255,255,255,.03); }}
                .meta-label {{ display:block; color:#bcae92; font-size:11px; text-transform:uppercase; font-weight:800; margin-bottom:6px; }}
                .promo-meta-item strong {{ color:#f7f2e8; font-size:13px; line-height:1.5; }}
                .promo-code-box {{ margin-top:14px; border:1px dashed rgba(235,193,102,.42); border-radius:12px; padding:14px; background:rgba(235,193,102,.06); }}
                .promo-code-box span {{ display:block; color:#bcae92; font-size:11px; text-transform:uppercase; font-weight:800; margin-bottom:6px; }}
                .promo-code-box strong {{ color:var(--accent-a); font-size:24px; letter-spacing:.5px; }}
                .promo-card-actions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; }}
                .primary-cta,.secondary-cta {{ display:inline-flex; align-items:center; justify-content:center; min-height:46px; padding:12px 18px; border-radius:10px; text-decoration:none; font-weight:900; }}
                .primary-cta {{ background:linear-gradient(135deg,var(--accent-b),var(--accent-a)); color:#050505; }}
                .secondary-cta {{ border:1px solid rgba(235,193,102,.42); color:var(--accent-a); }}
                @media (max-width: 860px) {{
                    .promo-grid {{ grid-template-columns:1fr; }}
                    .hero-card {{ padding:24px; }}
                    .promo-copy {{ min-height:0; }}
                    .promo-meta-grid {{ grid-template-columns:1fr; }}
                }}
            </style>
        </head>
        <body>
            <main class="page-shell">
                <nav class="page-nav">
                    <a class="page-logo" href="/">{logo_html}<span>{site_name}</span></a>
                    <a class="page-back" href="/">Beranda</a>
                </nav>
                <section class="hero-card">
                    <div class="hero-badge">Website Promo</div>
                    <h1>{hero_title}</h1>
                    <p class="hero-copy">{hero_desc}</p>
                </section>
                <section class="promo-grid">{cards_html}</section>
            </main>
        </body>
        </html>
        """
        return HTMLResponse(page_html)

    rows = await db_query(
        f"""
        SELECT slug
        FROM site_pages
        WHERE active=1 AND show_on_website=1 AND page_type='promo'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """
    )
    if rows:
        return RedirectResponse(url=f"/p/{rows[0][0]}", status_code=302)
    return RedirectResponse(url="/#promo", status_code=302)


@router.get("/p/{slug}", response_class=HTMLResponse)
async def render_public_page(slug: str) -> HTMLResponse:
    rows = await db_query(
        f"""
        SELECT {_page_select_columns()}
        FROM site_pages
        WHERE slug=:slug AND active=1 AND show_on_website=1
        """,
        {"slug": slug},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Halaman tidak ditemukan")

    page = _public_page_dict(rows[0], include_content=True)
    settings_data = await get_public_site_settings()
    site_name = html.escape(settings_data["site_name"])
    title = html.escape(page["title"])
    excerpt = html.escape(page.get("excerpt") or "")
    image_url = html.escape(page.get("image_url") or "")
    content_html = _render_text_blocks(page.get("content") or "")
    logo_url = html.escape(settings_data.get("logo_url") or "")
    logo_html = f'<img src="{logo_url}" alt="{site_name}" class="page-logo-img">' if logo_url else '<span class="page-logo-mark">L</span>'
    image_html = f'<img src="{image_url}" alt="{title}" class="promo-cover">' if image_url else ""
    page_type = page.get("page_type") or "general"
    badge = html.escape(page.get("badge") or ("Promo Terbaru" if page_type == "promo" else "Info"))
    cta_text = html.escape(page.get("cta_text") or ("Ambil Promo" if page_type == "promo" else "Kembali ke Beranda"))
    cta_url = html.escape(_safe_url(page.get("cta_url"), "/#produk-section" if page_type == "promo" else "/"))
    secondary_cta_text = html.escape(page.get("secondary_cta_text") or "Lihat Produk")
    secondary_cta_url = html.escape(_safe_url(page.get("secondary_cta_url"), "/#produk-section"))
    promo_code = html.escape(page.get("promo_code") or "")
    highlight_title = html.escape(page.get("highlight_title") or "Kenapa promo ini menarik?")
    highlight_items = _split_lines(page.get("highlight_items") or "")
    terms_items = _split_lines(page.get("terms_text") or "")
    accent_a, accent_b = _accent_vars(page.get("accent_color") or "gold")
    fallback_art = "%" if page_type == "promo" else "L"

    highlight_html = "".join(
        f'<div class="benefit-item"><span class="benefit-icon">&check;</span><span>{html.escape(item)}</span></div>'
        for item in highlight_items
    )
    terms_html = "".join(f"<li>{html.escape(item)}</li>" for item in terms_items)
    promo_code_html = (
        f'<div class="promo-code-card"><span>Kode Promo</span><strong>{promo_code}</strong></div>'
        if promo_code
        else ""
    )
    promo_extra_html = (
        f"""
        <section class="promo-section promo-grid">
            <div>
                <div class="section-kicker">Benefit</div>
                <h2>{highlight_title}</h2>
                <div class="benefit-list">{highlight_html or '<p class="muted">Tambahkan benefit promo dari dashboard admin.</p>'}</div>
            </div>
            <aside class="claim-panel">
                {promo_code_html}
                <a class="primary-cta wide" href="{cta_url}">{cta_text}</a>
                <a class="secondary-cta wide" href="{secondary_cta_url}">{secondary_cta_text}</a>
            </aside>
        </section>
        """
        if page_type == "promo"
        else ""
    )
    terms_section = (
        f"""
        <section class="promo-section terms-box">
            <div class="section-kicker">Syarat</div>
            <h2>Syarat & Ketentuan</h2>
            <ul>{terms_html}</ul>
        </section>
        """
        if page_type == "promo" and terms_html
        else ""
    )

    body = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title} - {site_name}</title>
        <style>
            :root {{ --accent-a:{accent_a}; --accent-b:{accent_b}; }}
            * {{ box-sizing:border-box; }}
            body {{ margin:0; background:#050505; color:#e8e1cf; font-family: Arial, sans-serif; }}
            body::before {{ content:""; position:fixed; inset:0; pointer-events:none; background:radial-gradient(circle at 18% 8%, color-mix(in srgb, var(--accent-a) 22%, transparent), transparent 28%), radial-gradient(circle at 84% 12%, rgba(255,255,255,.08), transparent 24%); }}
            .page-shell {{ width:min(1120px, 100%); margin:0 auto; padding:24px 18px 64px; position:relative; z-index:1; }}
            .page-nav {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 0 28px; }}
            .page-logo {{ display:flex; align-items:center; gap:10px; color:#f2d074; text-decoration:none; font-weight:900; }}
            .page-logo-img {{ width:auto; max-width:160px; height:40px; object-fit:contain; }}
            .page-logo-mark {{ width:38px; height:38px; display:inline-flex; align-items:center; justify-content:center; border-radius:10px; background:linear-gradient(135deg,var(--accent-b),var(--accent-a)); color:#050505; }}
            .page-back {{ color:#f2d074; text-decoration:none; border:1px solid color-mix(in srgb, var(--accent-a) 42%, transparent); padding:9px 13px; border-radius:8px; }}
            .hero-card {{ display:grid; grid-template-columns:minmax(0,1.05fr) minmax(280px,.95fr); gap:34px; align-items:center; min-height:470px; padding:38px; border:1px solid color-mix(in srgb, var(--accent-a) 24%, transparent); border-radius:18px; background:linear-gradient(135deg, rgba(255,255,255,.075), rgba(255,255,255,.025)); box-shadow:0 24px 80px rgba(0,0,0,.38); }}
            .badge {{ display:inline-flex; width:max-content; padding:7px 13px; border-radius:999px; background:color-mix(in srgb, var(--accent-a) 18%, transparent); color:var(--accent-a); font-size:12px; font-weight:900; margin-bottom:18px; }}
            h1 {{ color:#fff; font-size:clamp(38px, 7vw, 76px); line-height:.98; margin:0 0 16px; }}
            .excerpt {{ color:#c9c0ae; font-size:18px; line-height:1.65; max-width:680px; }}
            .hero-actions {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:28px; }}
            .primary-cta,.secondary-cta {{ display:inline-flex; justify-content:center; align-items:center; min-height:46px; padding:12px 18px; border-radius:8px; text-decoration:none; font-weight:900; }}
            .primary-cta {{ background:linear-gradient(135deg,var(--accent-b),var(--accent-a)); color:#050505; }}
            .secondary-cta {{ border:1px solid color-mix(in srgb, var(--accent-a) 48%, transparent); color:var(--accent-a); }}
            .wide {{ width:100%; }}
            .promo-cover {{ width:100%; aspect-ratio:1.15; object-fit:cover; border-radius:16px; border:1px solid color-mix(in srgb, var(--accent-a) 28%, transparent); }}
            .promo-art-fallback {{ min-height:320px; border-radius:16px; border:1px solid color-mix(in srgb, var(--accent-a) 28%, transparent); background:linear-gradient(135deg, color-mix(in srgb, var(--accent-a) 24%, transparent), rgba(255,255,255,.04)); display:flex; align-items:center; justify-content:center; font-size:86px; font-weight:900; color:var(--accent-a); }}
            .promo-section {{ margin-top:24px; padding:28px; border-radius:16px; border:1px solid rgba(255,255,255,.09); background:rgba(255,255,255,.045); }}
            .promo-grid {{ display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:22px; }}
            .section-kicker {{ color:var(--accent-a); font-size:12px; text-transform:uppercase; font-weight:900; margin-bottom:8px; }}
            h2 {{ color:#fff; font-size:28px; margin:0 0 16px; }}
            .content p,.muted {{ color:#d7d0c2; line-height:1.85; font-size:16px; }}
            .benefit-list {{ display:grid; gap:10px; }}
            .benefit-item {{ display:flex; align-items:flex-start; gap:10px; color:#f4ecdc; padding:12px; border-radius:10px; background:rgba(255,255,255,.045); }}
            .benefit-icon {{ width:24px; height:24px; display:inline-flex; align-items:center; justify-content:center; border-radius:999px; background:var(--accent-a); color:#050505; flex:0 0 auto; font-weight:900; }}
            .claim-panel {{ display:flex; flex-direction:column; gap:12px; }}
            .promo-code-card {{ padding:18px; border-radius:14px; background:#080808; border:1px dashed color-mix(in srgb, var(--accent-a) 60%, transparent); text-align:center; }}
            .promo-code-card span {{ display:block; color:#a9a193; font-size:12px; margin-bottom:8px; }}
            .promo-code-card strong {{ color:var(--accent-a); font-size:28px; letter-spacing:1px; }}
            .terms-box ul {{ margin:0; padding-left:20px; color:#cfc7b9; line-height:1.9; }}
            @media (max-width: 820px) {{
                .hero-card,.promo-grid {{ grid-template-columns:1fr; padding:22px; }}
                .page-shell {{ padding:18px 14px 44px; }}
                h1 {{ font-size:40px; }}
                .promo-section {{ padding:20px; }}
            }}
        </style>
    </head>
    <body>
        <main class="page-shell">
            <nav class="page-nav">
                <a class="page-logo" href="/">{logo_html}<span>{site_name}</span></a>
                <a class="page-back" href="/">Beranda</a>
            </nav>
            <section class="hero-card">
                <div>
                    <div class="badge">{badge}</div>
                    <h1>{title}</h1>
                    <div class="excerpt">{excerpt}</div>
                    <div class="hero-actions">
                        <a class="primary-cta" href="{cta_url}">{cta_text}</a>
                        <a class="secondary-cta" href="{secondary_cta_url}">{secondary_cta_text}</a>
                    </div>
                </div>
                <div>{image_html or f'<div class="promo-art-fallback">{fallback_art}</div>'}</div>
            </section>
            <section class="promo-section content">{content_html}</section>
            {promo_extra_html}
            {terms_section}
        </main>
    </body>
    </html>
    """
    return HTMLResponse(body)

    page = _public_page_dict(rows[0], include_content=True)
    settings_data = await get_public_site_settings()
    site_name = html.escape(settings_data["site_name"])
    title = html.escape(page["title"])
    excerpt = html.escape(page.get("excerpt") or "")
    image_url = html.escape(page.get("image_url") or "")
    content = html.escape(page.get("content") or "").replace("\n", "<br>")
    logo_url = html.escape(settings_data.get("logo_url") or "")
    logo_html = f'<img src="{logo_url}" alt="{site_name}" class="page-logo-img">' if logo_url else '<span class="page-logo-mark">L</span>'
    image_html = f'<img src="{image_url}" alt="{title}" class="page-cover">' if image_url else ""

    body = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title} - {site_name}</title>
        <style>
            body {{ margin:0; background:#050505; color:#e8e1cf; font-family: Arial, sans-serif; }}
            .page-shell {{ max-width: 860px; margin: 0 auto; padding: 28px 18px 56px; }}
            .page-nav {{ display:flex; align-items:center; justify-content:space-between; gap:16px; padding:18px 0; }}
            .page-logo {{ display:flex; align-items:center; gap:10px; color:#e9c15d; text-decoration:none; font-weight:800; }}
            .page-logo-img {{ width:auto; max-width:160px; height:40px; border-radius:0; object-fit:contain; background:transparent; border:none; }}
            .page-logo-mark {{ color:#c19b32; }}
            .page-back {{ color:#e9c15d; text-decoration:none; border:1px solid rgba(193,155,50,.45); padding:8px 12px; border-radius:8px; }}
            .page-cover {{ width:100%; max-height:380px; object-fit:cover; border-radius:14px; border:1px solid rgba(193,155,50,.25); margin:22px 0; }}
            h1 {{ color:#fff; font-size: clamp(32px, 7vw, 58px); line-height:1.05; margin:28px 0 12px; }}
            .excerpt {{ color:#b9b1a2; font-size:17px; line-height:1.65; margin-bottom:26px; }}
            .content {{ color:#e8e1cf; font-size:16px; line-height:1.85; border-top:1px solid rgba(193,155,50,.2); padding-top:24px; }}
        </style>
    </head>
    <body>
        <main class="page-shell">
            <nav class="page-nav">
                <a class="page-logo" href="/">{logo_html}<span>{site_name}</span></a>
                <a class="page-back" href="/">Beranda</a>
            </nav>
            {image_html}
            <h1>{title}</h1>
            <div class="excerpt">{excerpt}</div>
            <article class="content">{content}</article>
        </main>
    </body>
    </html>
    """
    return HTMLResponse(body)

@router.get("/api/products")
async def get_public_products():
    rows = await db_query(
        """
        SELECT
            p.sku,
            p.provider,
            p.name,
            p.price,
            p.category,
            p.description,
            p.image_url,
            p.logo_url,
            p.promo_title,
            p.promo_text,
            p.promo_badge,
            p.promo_url,
            p.display_order,
            COALESCE(p.product_type, 'prepaid'),
            COALESCE(p.buyer_product_status, 1),
            COALESCE(p.seller_product_status, 1),
            COALESCE(p.stock, 0),
            COALESCE(p.unlimited_stock, 0),
            COALESCE(p.multi, 0),
            p.start_cut_off,
            p.end_cut_off,
            (
                SELECT COUNT(*)
                FROM topup t
                WHERE t.nominal=p.sku
            ) AS order_count,
            (
                SELECT COUNT(*)
                FROM topup t
                WHERE t.nominal=p.sku AND t.topup_status='SUCCESS'
            ) AS success_count
        FROM products p
        WHERE p.active=1
        ORDER BY COALESCE(p.category, ''), COALESCE(p.provider, ''), COALESCE(p.display_order, 0), p.price
        """
    )

    now_utc = datetime.now(timezone.utc)

    categories: Dict[str, Dict[str, Any]] = {}
    flat_products = []

    for row in rows:
        (
            sku,
            provider,
            name,
            price,
            category,
            description,
            image_url,
            logo_url,
            promo_title,
            promo_text,
            promo_badge,
            promo_url,
            display_order,
            product_type,
            buyer_product_status,
            seller_product_status,
            stock,
            unlimited_stock,
            multi,
            start_cut_off,
            end_cut_off,
            order_count,
            success_count,
        ) = row
        normalized_provider = normalized_provider_name(provider, name, category)
        normalized_category = normalized_category_name(category, normalized_provider)
        product = {
            "sku": sku,
            "provider": normalized_provider,
            "name": name,
            "price": float(price or 0),
            "category": normalized_category,
            "description": description or "",
            "image_url": image_url or "",
            "logo_url": logo_url or "",
            "promo_title": promo_title or "",
            "promo_text": promo_text or "",
            "promo_badge": promo_badge or "",
            "promo_url": promo_url or "",
            "display_order": int(display_order or 0),
            "product_type": str(product_type or "prepaid").lower(),
            "buyer_product_status": bool(buyer_product_status),
            "seller_product_status": bool(seller_product_status),
            "stock": int(stock or 0),
            "unlimited_stock": bool(unlimited_stock),
            "multi": bool(multi),
            "start_cut_off": start_cut_off or "",
            "end_cut_off": end_cut_off or "",
            "order_count": int(order_count or 0),
            "success_count": int(success_count or 0),
        }

        product_quote = await quote_product(product, now=now_utc)
        product["original_price"] = product_quote.base_price
        product["price"] = product_quote.final_price
        product["promos_applied"] = [item.to_dict() for item in product_quote.applied]
        product["promo_applied"] = product_quote.primary.to_dict() if product_quote.primary else None
        flat_products.append(product)

        category_name = product["category"]
        provider_name = product["provider"]
        category_entry = categories.setdefault(category_name, {"name": category_name, "providers": {}})
        provider_entry = category_entry["providers"].setdefault(
            provider_name,
            {
                "name": provider_name,
                "logo_url": product["logo_url"],
                "image_url": product["image_url"],
                "description": product["description"],
                "promo_title": product["promo_title"],
                "promo_text": product["promo_text"],
                "promo_badge": product["promo_badge"],
                "promo_url": product["promo_url"],
                "product_count": 0,
                "order_count": 0,
                "success_count": 0,
                "min_price": None,
                "max_price": None,
                "items": [],
            },
        )
        if not provider_entry["logo_url"] and product["logo_url"]:
            provider_entry["logo_url"] = product["logo_url"]
        if not provider_entry["image_url"] and product["image_url"]:
            provider_entry["image_url"] = product["image_url"]
        if not provider_entry["description"] and product["description"]:
            provider_entry["description"] = product["description"]
        if not provider_entry["promo_title"] and product["promo_title"]:
            provider_entry["promo_title"] = product["promo_title"]
        if not provider_entry["promo_text"] and product["promo_text"]:
            provider_entry["promo_text"] = product["promo_text"]
        if not provider_entry["promo_badge"] and product["promo_badge"]:
            provider_entry["promo_badge"] = product["promo_badge"]
        if not provider_entry["promo_url"] and product["promo_url"]:
            provider_entry["promo_url"] = product["promo_url"]
        provider_entry["product_count"] += 1
        provider_entry["order_count"] += product["order_count"]
        provider_entry["success_count"] += product["success_count"]
        provider_entry["min_price"] = product["price"] if provider_entry["min_price"] is None else min(provider_entry["min_price"], product["price"])
        provider_entry["max_price"] = product["price"] if provider_entry["max_price"] is None else max(provider_entry["max_price"], product["price"])
        provider_entry["items"].append(product)

    grouped_categories = []
    for category_name in sorted(categories.keys()):
        provider_map = categories[category_name]["providers"]
        grouped_categories.append(
            {
                "name": category_name,
                "providers": [
                    provider_map[key]
                    for key in sorted(
                        provider_map.keys(),
                        key=lambda name: (-int(provider_map[name].get("order_count") or 0), name.lower()),
                    )
                ],
            }
        )

    return {"categories": grouped_categories, "products": flat_products}


@router.get("/api/promos/active")
async def get_active_promos() -> Dict[str, Any]:
    return {"promos": await _load_public_active_promos()}


@router.get("/api/promos/validate")
async def validate_promo(
    request: Request,
    sku: str,
    code: str,
    method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Any:
    cleaned_code = normalize_voucher_code(code)
    if not cleaned_code:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "reason_code": "PROMO_CODE_REQUIRED",
                "message": "Kode promo wajib diisi",
            },
        )

    canonical_sku = normalize_sku(sku)
    if not canonical_sku:
        return JSONResponse(
            status_code=400,
            content=promotion_failure_payload("invalid_transaction_context", "Produk tidak ditemukan"),
        )

    row = await db_query(
        """
        SELECT sku, provider, name, price, category
        FROM products
        WHERE LOWER(TRIM(sku))=:sku AND active=1
        """,
        {"sku": canonical_sku},
    )
    if not row:
        return JSONResponse(
            status_code=404,
            content=promotion_failure_payload("invalid_transaction_context", "Produk tidak ditemukan"),
        )

    sku_value, provider, name, base_price, category = row[0]
    normalized_provider = normalized_provider_name(provider, name, category)
    product = {
        "sku": sku_value,
        "provider": normalized_provider,
        "name": name,
        "price": float(base_price or 0),
        "category": normalized_category_name(category, normalized_provider),
    }

    customer = await _optional_customer_from_request(request)
    promo_identity = await _promo_identity_from_request(request, phone=phone, target_id=target_id)
    quote = await quote_product(
        product,
        promo_code=cleaned_code,
        payment_method=normalize_payment_code(method),
        phone=phone,
        target_id=target_id,
        customer_id=(customer or {}).get("id"),
        identity=promo_identity,
    )
    if not quote.code_valid:
        return JSONResponse(status_code=400, content=code_failure_payload(quote))
    payload = quote.to_dict()
    return {
        "valid": True,
        "sku": sku_value,
        "code": cleaned_code,
        "original_price": quote.base_price,
        "final_price": quote.final_price,
        "discount_amount": quote.discount_amount,
        "promo": payload.get("promo_applied") or {},
        "promos_applied": payload.get("promos_applied", []),
    }

@router.post("/callback")
async def tripay_callback(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Callback-Signature")
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        await _log_webhook_event(
            provider="tripay",
            event_type="invalid_json",
            reference_id=None,
            signature_valid=False,
            payload=raw_body.decode("utf-8", errors="replace"),
            response_status="INVALID_JSON",
            message="Payload callback Tripay bukan JSON valid",
        )
        return {"success": False, "message": "Invalid JSON"}

    merchant_ref = data.get("merchant_ref")
    status = data.get("status")
    tripay_config = await get_tripay_config()
    private_key = tripay_config.get("private_key") or ""

    if not private_key:
        await _log_webhook_event(
            provider="tripay",
            event_type=str(status or "unknown"),
            reference_id=merchant_ref,
            signature_valid=False,
            payload=data,
            response_status="CONFIG_ERROR",
            message="Konfigurasi Tripay belum lengkap",
        )
        return {"success": False, "message": "Konfigurasi Tripay belum lengkap"}

    expected_sig = hmac.new(private_key.encode(), raw_body, hashlib.sha256).hexdigest()

    if not signature or not hmac.compare_digest(signature, expected_sig):
        await _log_webhook_event(
            provider="tripay",
            event_type=str(status or "unknown"),
            reference_id=merchant_ref,
            signature_valid=False,
            payload=data,
            response_status="INVALID_SIGNATURE",
            message="Signature Tripay tidak valid",
        )
        return {"success": False, "message": "Invalid signature"}

    open_payment_rows = await db_query(
        "SELECT customer_id FROM customer_open_payments WHERE merchant_ref=:merchant_ref AND active=1 LIMIT 1",
        {"merchant_ref": merchant_ref},
    )
    if open_payment_rows and status == "PAID":
        credited = await _credit_wallet_deposit_if_new(int(open_payment_rows[0][0]), data)
        await _log_webhook_event(
            provider="tripay",
            event_type="open_payment",
            reference_id=merchant_ref,
            signature_valid=True,
            payload=data,
            response_status="OK",
            message="Callback Open Payment Tripay diproses" if credited else "Callback Open Payment Tripay sudah pernah diproses",
        )
        return {"success": True}

    wallet_deposit_rows = await db_query(
        """
        SELECT customer_id
        FROM customer_wallet_deposits
        WHERE merchant_ref=:merchant_ref
           OR reference=:reference
        LIMIT 1
        """,
        {"merchant_ref": merchant_ref, "reference": data.get("reference") or ""},
    )
    if wallet_deposit_rows:
        if status == "PAID":
            credited = await _credit_wallet_deposit_if_new(int(wallet_deposit_rows[0][0]), data)
            await _log_webhook_event(
                provider="tripay",
                event_type="wallet_deposit",
                reference_id=merchant_ref,
                signature_valid=True,
                payload=data,
                response_status="OK",
                message="Deposit wallet Tripay dikreditkan" if credited else "Deposit wallet Tripay sudah pernah dikreditkan",
            )
            return {"success": True}
        if status in {"EXPIRED", "FAILED", "REFUND"}:
            await db_execute(
                """
                UPDATE customer_wallet_deposits
                SET status=:status,
                    payload=:payload
                WHERE merchant_ref=:merchant_ref
                   OR reference=:reference
                """,
                {
                    "status": status,
                    "payload": _json_dumps(data),
                    "merchant_ref": merchant_ref,
                    "reference": data.get("reference") or "",
                },
            )
            await _log_webhook_event(
                provider="tripay",
                event_type="wallet_deposit",
                reference_id=merchant_ref,
                signature_valid=True,
                payload=data,
                response_status=status,
                message="Deposit wallet Tripay tidak berhasil",
            )
            return {"success": True}

    status = str(status or "").strip().upper()
    callback_message = "Callback Tripay diproses"
    if status == "PAID":
        order = await db_query(
            """
            SELECT payment_status, topup_status, COALESCE(amount, 0),
                   COALESCE(payment_reference, ''), COALESCE(refund_status, '')
            FROM topup
            WHERE id=:id
            """,
            {"id": merchant_ref},
        )

        if order:
            current_status = str(order[0][0] or "").upper()
            current_topup_status = str(order[0][1] or "").upper()
            callback_reference = str(data.get("reference") or "").strip()
            callback_amount = data.get("amount") or data.get("amount_received")
            expected_amount = _int_value(order[0][2])
            stored_reference = str(order[0][3] or "").strip()
            current_refund_status = str(order[0][4] or "").upper()
            if expected_amount > 0 and _int_value(callback_amount) != expected_amount:
                callback_message = "Callback PAID ditolak karena amount tidak cocok"
                await _log_webhook_event(
                    provider="tripay",
                    event_type="PAID",
                    reference_id=merchant_ref,
                    signature_valid=True,
                    payload=data,
                    response_status="AMOUNT_MISMATCH",
                    message=callback_message,
                )
                return {"success": False, "message": callback_message}
            if stored_reference and callback_reference and stored_reference != callback_reference:
                callback_message = "Callback PAID ditolak karena reference tidak cocok"
                await _log_webhook_event(
                    provider="tripay",
                    event_type="PAID",
                    reference_id=merchant_ref,
                    signature_valid=True,
                    payload=data,
                    response_status="REFERENCE_MISMATCH",
                    message=callback_message,
                )
                return {"success": False, "message": callback_message}
            if current_status in {"CANCELED", "CANCELLED"} and current_topup_status == "FAILED":
                if current_refund_status == "PAYMENT_RECEIVED_AFTER_CANCEL":
                    callback_message = "Callback PAID sudah dicatat untuk review refund manual"
                else:
                    updated_rows = await db_execute_rowcount(
                        """
                        UPDATE topup
                        SET payment_reference=COALESCE(NULLIF(:payment_reference, ''), payment_reference),
                            payment_creation_outcome='SUCCESS',
                            tripay_payload=:tripay_payload,
                            refund_status='PAYMENT_RECEIVED_AFTER_CANCEL',
                            refund_note=CASE
                                WHEN COALESCE(refund_note, '')='' THEN
                                    'Pembayaran diterima setelah pembatalan customer; review refund manual diperlukan'
                                ELSE refund_note
                            END,
                            status_updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                          AND payment_status IN ('CANCELED', 'CANCELLED')
                          AND COALESCE(topup_status, '')='FAILED'
                          AND COALESCE(refund_status, '') != 'PAYMENT_RECEIVED_AFTER_CANCEL'
                        """,
                        {
                            "id": merchant_ref,
                            "payment_reference": data.get("reference"),
                            "tripay_payload": _json_dumps(data),
                        },
                    )
                    callback_message = (
                        "Pembayaran setelah pembatalan dicatat untuk review refund manual; fulfillment diblokir"
                        if updated_rows
                        else "Callback PAID sudah dicatat untuk review refund manual"
                    )
            elif current_status == "UNPAID":
                updated_rows = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET payment_status='PAID',
                        topup_status=CASE
                            WHEN COALESCE(topup_status, '') IN ('', 'PENDING_PAYMENT') THEN 'PROCESSING'
                            ELSE topup_status
                        END,
                        payment_reference=:payment_reference,
                        payment_creation_outcome='SUCCESS',
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND payment_status='UNPAID'
                    """,
                    {"id": merchant_ref, "payment_reference": data.get("reference")}
                )
                if updated_rows:
                    await finalize_order_promotions(str(merchant_ref))
                    if current_topup_status in {"", "PENDING_PAYMENT"}:
                        print(f"Tripay PAID. Order masuk queue engine: {merchant_ref}")
                    else:
                        callback_message = f"Callback PAID memperbarui pembayaran tanpa mengubah topup terminal ({current_topup_status})"
                    phone_rows = await db_query("SELECT phone FROM topup WHERE id=:id", {"id": merchant_ref})
                    if phone_rows and current_topup_status in {"", "PENDING_PAYMENT"}:
                        await _queue_notification(
                            channel="WHATSAPP",
                            recipient=phone_rows[0][0],
                            subject="Pembayaran diterima",
                            body=f"Pembayaran order {merchant_ref} diterima. Topup sedang diproses.",
                            reference_type="topup",
                            reference_id=str(merchant_ref),
                        )
                else:
                    callback_message = "Callback Tripay sudah pernah diproses"
            elif current_status == "PAID":
                # Idempotent repair for an order whose callback committed but
                # promotion finalization was interrupted.
                await finalize_order_promotions(str(merchant_ref))
                callback_message = "Callback Tripay sudah pernah diproses"
            else:
                callback_message = f"Callback PAID diabaikan karena order sudah terminal ({current_status})"
    elif status in {"EXPIRED", "FAILED", "REFUND"}:
        terminal_rows = await db_query("SELECT payment_status FROM topup WHERE id=:id", {"id": merchant_ref})
        current_status = str(terminal_rows[0][0] or "").upper() if terminal_rows else ""
        allowed_current = {"UNPAID", "PAID"} if status == "REFUND" else {"UNPAID"}
        if current_status not in allowed_current:
            callback_message = f"Callback {status} diabaikan karena order sudah terminal ({current_status})"
        else:
            allowed_sql = "('UNPAID', 'PAID')" if status == "REFUND" else "('UNPAID')"
            updated_rows = await db_execute_rowcount(
                f"""
                UPDATE topup
                SET payment_status=:payment_status,
                    topup_status=CASE
                        WHEN :payment_status IN ('EXPIRED', 'FAILED') THEN 'FAILED'
                        ELSE topup_status
                    END,
                    payment_reference=:payment_reference,
                    payment_creation_outcome='SUCCESS',
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND payment_status IN {allowed_sql}
                """,
                {"id": merchant_ref, "payment_status": status, "payment_reference": data.get("reference")},
            )
            if updated_rows:
                await release_order_promotions(str(merchant_ref), include_redeemed=status == "REFUND")

    await _log_webhook_event(
        provider="tripay",
        event_type=str(status or "unknown"),
        reference_id=merchant_ref,
        signature_valid=True,
        payload=data,
        response_status="OK",
        message=callback_message,
    )

    return {"success": True}

@router.post("/api/webhook/digiflazz")
async def digiflazz_webhook(request: Request, x_hub_signature: str = Header(None, alias="X-Hub-Signature")):
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        await _log_webhook_event(
            provider="digiflazz",
            event_type="invalid_json",
            reference_id=None,
            signature_valid=False,
            payload=body.decode("utf-8", errors="replace"),
            response_status="INVALID_JSON",
            message="Payload webhook Digiflazz bukan JSON valid",
        )
        raise HTTPException(status_code=400, detail="Payload JSON tidak valid")

    payload = data.get("data", {}) if isinstance(data, dict) else {}
    ref_id = payload.get("ref_id")
    status = payload.get("status")
    digiflazz_config = await get_digiflazz_config()
    webhook_secret = digiflazz_config.get("webhook_secret") or ""

    if not webhook_secret:
        await _log_webhook_event(
            provider="digiflazz",
            event_type=str(status or "unknown"),
            reference_id=ref_id,
            signature_valid=False,
            payload=data,
            response_status="CONFIG_ERROR",
            message="Konfigurasi webhook Digiflazz belum lengkap",
        )
        raise HTTPException(status_code=403, detail="Konfigurasi webhook Digiflazz belum lengkap")

    my_signature = "sha1=" + hmac.new(webhook_secret.encode(), body, hashlib.sha1).hexdigest()

    if not x_hub_signature or not hmac.compare_digest(my_signature, x_hub_signature):
        await _log_webhook_event(
            provider="digiflazz",
            event_type=str(status or "unknown"),
            reference_id=ref_id,
            signature_valid=False,
            payload=data,
            response_status="INVALID_SIGNATURE",
            message="Signature Digiflazz tidak valid",
        )
        raise HTTPException(status_code=403, detail="Signature tidak valid!")
    sn = payload.get("sn", "")
    provider_meta = {
        "provider_rc": payload.get("rc") or "",
        "provider_price": _float_value(payload.get("price")),
        "provider_selling_price": _float_value(payload.get("selling_price")),
        "provider_last_balance": _float_value(payload.get("buyer_last_saldo")),
        "provider_payload": _json_dumps(payload),
    }
    order_rows = await db_query("SELECT topup_status, sn FROM topup WHERE id=:id", {"id": ref_id})
    if not order_rows:
        await _log_webhook_event(
            provider="digiflazz",
            event_type=str(status or "unknown"),
            reference_id=ref_id,
            signature_valid=True,
            payload=data,
            response_status="ORDER_NOT_FOUND",
            message="Webhook Digiflazz tidak menemukan order",
        )
        return {"message": "Webhook Digiflazz diterima"}

    current_topup_status = str(order_rows[0][0] or "").upper()
    current_sn = str(order_rows[0][1] or "")
    transition_allowed = current_topup_status in {"PROCESSING", "PENDING_PROVIDER"}
    webhook_message = "Webhook Digiflazz diproses"

    classification = classify_digiflazz_transaction_response(data)

    if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
        if transition_allowed:
            updated_rows = await db_execute_rowcount(
                """
                UPDATE topup
                SET topup_status='SUCCESS',
                    provider_outcome='SUCCESS',
                    sn=CASE
                        WHEN COALESCE(:sn, '') != '' THEN :sn
                        ELSE sn
                    END,
                    note=:note,
                    provider_rc=:provider_rc,
                    provider_price=:provider_price,
                    provider_selling_price=:provider_selling_price,
                    provider_last_balance=:provider_last_balance,
                    provider_payload=:provider_payload,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                """,
                {"sn": sn, "note": "Sukses", "id": ref_id, **provider_meta}
            )
            if updated_rows:
                await finalize_order_promotions(str(ref_id))
                phone_rows = await db_query("SELECT phone FROM topup WHERE id=:id", {"id": ref_id})
                if phone_rows:
                    await _queue_notification(
                        channel="WHATSAPP",
                        recipient=phone_rows[0][0],
                        subject="Topup sukses",
                        body=f"Order {ref_id} sukses. SN: {sn or current_sn or '-'}",
                        reference_type="topup",
                        reference_id=str(ref_id),
                    )
                print(f"TOPUP SUKSES. Ref: {ref_id} | SN: {sn or current_sn}")
            else:
                webhook_message = "Webhook success duplikat diabaikan"
        else:
            webhook_message = f"Webhook success diabaikan karena order sudah terminal ({current_topup_status})"
    elif classification == DIGIFLAZZ_OUTCOME_PENDING:
        if transition_allowed:
            updated_rows = await db_execute_rowcount(
                """
                UPDATE topup
                SET topup_status='PENDING_PROVIDER',
                    provider_outcome='PENDING_PROVIDER',
                    provider_last_error=NULL,
                    provider_rc=:provider_rc,
                    provider_price=:provider_price,
                    provider_selling_price=:provider_selling_price,
                    provider_last_balance=:provider_last_balance,
                    provider_payload=:provider_payload,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                """,
                {"id": ref_id, **provider_meta},
            )
            if not updated_rows:
                webhook_message = "Webhook pending duplikat diabaikan"
        else:
            webhook_message = f"Webhook pending diabaikan karena order sudah terminal ({current_topup_status})"
    elif classification == DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE:
        pesan_error = payload.get("message", "Gagal dari provider")
        if transition_allowed:
            updated_rows = await db_execute_rowcount(
                """
                UPDATE topup
                SET topup_status='FAILED',
                    provider_outcome='FAILED',
                    note=:note,
                    provider_rc=:provider_rc,
                    provider_price=:provider_price,
                    provider_selling_price=:provider_selling_price,
                    provider_last_balance=:provider_last_balance,
                    provider_payload=:provider_payload,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                """,
                {"note": pesan_error, "id": ref_id, **provider_meta}
            )
            if updated_rows:
                await release_order_promotions(str(ref_id), include_redeemed=True)
                phone_rows = await db_query("SELECT phone FROM topup WHERE id=:id", {"id": ref_id})
                if phone_rows:
                    await _queue_notification(
                        channel="WHATSAPP",
                        recipient=phone_rows[0][0],
                        subject="Topup gagal",
                        body=f"Order {ref_id} gagal diproses. Catatan: {pesan_error}",
                        reference_type="topup",
                        reference_id=str(ref_id),
                    )
                print(f"TOPUP GAGAL. Ref: {ref_id} | Error: {pesan_error}")
            else:
                webhook_message = "Webhook failed duplikat diabaikan"
        else:
            webhook_message = f"Webhook failed diabaikan karena order sudah terminal ({current_topup_status})"
    else:
        if transition_allowed:
            updated_rows = await db_execute_rowcount(
                """
                UPDATE topup
                SET provider_outcome=:provider_outcome,
                    provider_last_error=:provider_last_error,
                    provider_rc=:provider_rc,
                    provider_price=:provider_price,
                    provider_selling_price=:provider_selling_price,
                    provider_last_balance=:provider_last_balance,
                    provider_payload=:provider_payload,
                    provider_last_check_at=CURRENT_TIMESTAMP,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                """,
                {
                    "id": ref_id,
                    "provider_outcome": DIGIFLAZZ_OUTCOME_SENT_UNKNOWN,
                    "provider_last_error": "Outcome webhook provider belum pasti; rekonsiliasi diperlukan",
                    **provider_meta,
                },
            )
            webhook_message = (
                "Webhook outcome belum pasti dan menunggu rekonsiliasi"
                if updated_rows
                else "Webhook unknown duplikat diabaikan"
            )
        else:
            webhook_message = f"Webhook unknown diabaikan karena order sudah terminal ({current_topup_status})"

    await _log_webhook_event(
        provider="digiflazz",
        event_type=str(status or "unknown"),
        reference_id=ref_id,
        signature_valid=True,
        payload=data,
        response_status="OK",
        message=webhook_message,
    )

    return {"message": "Webhook Digiflazz diterima"}

@router.get("/callback")
def tripay_return():
    return RedirectResponse(url="/")

@router.post("/topup/{identifier}/cancel")
async def cancel_transaction(identifier: str, request: Request):
    try:
        rows = await db_query(
            """
            SELECT payment_status, topup_status, customer_id,
                   COALESCE(payment_creation_outcome, '')
            FROM topup
            WHERE id=:id
            LIMIT 1
            """,
            {"id": identifier},
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

        await _require_order_access(request, identifier, rows[0][2])

        payment_status = str(rows[0][0] or "").upper()
        topup_status = str(rows[0][1] or "").upper()
        if str(rows[0][3] or "").upper() == "SENT_UNKNOWN":
            raise HTTPException(
                status_code=409,
                detail="Pembuatan invoice masih direkonsiliasi; pembatalan diblokir untuk mencegah pembayaran terlambat terabaikan",
            )
        if payment_status != "UNPAID" or topup_status not in {"", "PENDING_PAYMENT"}:
            raise HTTPException(
                status_code=409,
                detail="Transaksi hanya bisa dibatalkan sebelum pembayaran diterima",
            )

        updated_rows = await db_execute_rowcount(
            """
            UPDATE topup
            SET payment_status='CANCELED',
                topup_status='FAILED',
                note=CASE
                    WHEN COALESCE(note, '')='' THEN 'Dibatalkan oleh customer sebelum pembayaran'
                    ELSE note
                END,
                status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
              AND payment_status='UNPAID'
              AND COALESCE(topup_status, '') IN ('', 'PENDING_PAYMENT')
              AND COALESCE(payment_creation_outcome, '') != 'SENT_UNKNOWN'
            """,
            {"id": identifier}
        )
        if not updated_rows:
            raise HTTPException(
                status_code=409,
                detail="Transaksi berubah sebelum pembatalan diproses",
            )
        await release_order_promotions(identifier)
        return {"success": True, "message": "Transaksi berhasil dibatalkan"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Gagal membatalkan transaksi order=%s", identifier)
        raise HTTPException(status_code=500, detail="Gagal membatalkan transaksi")
