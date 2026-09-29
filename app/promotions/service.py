"""Database-backed promotion quoting and atomic redemption lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.database import get_engine
from app.core.settings import settings
from app.promotions.engine import (
    ACTIVE_REDEMPTION_STATUSES,
    EligibilityProfile,
    PromoIdentity,
    PromotionContext,
    PromotionQuote,
    normalize_code,
    normalize_payment_code,
    normalize_phone,
    normalize_sku,
    normalize_target_id,
    parse_datetime,
    quote_fingerprint,
    quote_promotions,
    rupiah,
)


PersistOrder = Callable[[AsyncConnection, PromotionQuote, dict[str, Any]], Awaitable[None]]


class PromotionServiceError(RuntimeError):
    """Base class for safe checkout errors."""


class PromotionCodeError(PromotionServiceError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class PromotionChangedError(PromotionServiceError):
    pass


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items()}


def _identity_hash(kind: str, value: Any) -> Optional[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    digest = hmac.new(
        settings.secret_key.encode("utf-8"),
        f"promotion:{kind}:{cleaned}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def customer_hash(phone: Any) -> Optional[str]:
    normalized = normalize_phone(phone)
    return _identity_hash("phone", normalized) if normalized else None


def target_hash(sku: Any, target_id: Any) -> Optional[str]:
    target = normalize_target_id(target_id)
    if not target:
        return None
    return _identity_hash("target", f"{normalize_sku(sku)}:{target}")


def _is_sqlite(connection: AsyncConnection) -> bool:
    return connection.dialect.name == "sqlite"


async def _begin_locked(connection: AsyncConnection) -> None:
    if _is_sqlite(connection):
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        await connection.begin()


async def _customer_history(
    connection: AsyncConnection,
    *,
    identity: PromoIdentity,
) -> tuple[str, int]:
    """Backward-compatible segment view over the authoritative profile."""

    profile = await _eligibility_profile(connection, identity, now=datetime.now(timezone.utc))
    success_count = profile.successful_order_count
    if success_count is None:
        return "unknown", 0
    return ("existing" if success_count > 0 else "new"), success_count


def _phone_variants(value: Any) -> list[str]:
    normalized = normalize_phone(value)
    if not normalized:
        return []
    variants = {normalized}
    if normalized.startswith("62"):
        variants.add(f"0{normalized[2:]}")
        variants.add(f"+{normalized}")
    return sorted(variants)


async def _eligibility_profile(
    connection: AsyncConnection,
    identity: PromoIdentity,
    *,
    now: datetime,
    lock_account: bool = False,
) -> EligibilityProfile:
    """Load all eligibility facts once; callers reuse them for every promo."""

    moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    account_row = None
    if identity.is_authenticated and identity.customer_id is not None:
        lock_suffix = "" if _is_sqlite(connection) or not lock_account else " FOR UPDATE"
        account_result = await connection.execute(
            text(
                """
                SELECT id, COALESCE(phone, ''), COALESCE(active, 0), created_at
                FROM customer_accounts
                WHERE id=:customer_id
                LIMIT 1
                """
                + lock_suffix
            ),
            {"customer_id": identity.customer_id},
        )
        account_row = account_result.first()
    elif identity.phone:
        guest_params: dict[str, Any] = {}
        guest_phone_clauses: list[str] = []
        for index, value in enumerate(_phone_variants(identity.phone)):
            key = f"account_phone_{index}"
            guest_phone_clauses.append(f"phone=:{key}")
            guest_params[key] = value
        if guest_phone_clauses:
            account_result = await connection.execute(
                text(
                    f"""
                    SELECT id, COALESCE(phone, ''), COALESCE(active, 0), created_at
                    FROM customer_accounts
                    WHERE {' OR '.join(guest_phone_clauses)}
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                guest_params,
            )
            account_row = account_result.first()

    has_account = bool(account_row)
    account_phone = normalize_phone(account_row[1]) if account_row else ""
    account_active = bool(account_row and account_row[2])
    account_created_at = parse_datetime(account_row[3], "UTC") if account_row and account_row[3] else None
    account_age_days = None
    if account_created_at is not None:
        account_age_days = max(int((moment - account_created_at).total_seconds() // 86400), 0)

    # Guest account facts may be resolved from the phone, but a claimed phone
    # never grants that account's customer_id or customer-linked history.
    history_customer_id = int(account_row[0]) if account_row and identity.is_authenticated else None
    history_phone = account_phone if identity.is_authenticated else identity.phone
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if history_customer_id is not None:
        clauses.append("customer_id=:history_customer_id")
        params["history_customer_id"] = history_customer_id
    phone_clauses: list[str] = []
    for index, value in enumerate(_phone_variants(history_phone)):
        key = f"history_phone_{index}"
        phone_clauses.append(f"phone=:{key}")
        params[key] = value
    if phone_clauses:
        clauses.append(f"({' OR '.join(phone_clauses)})")

    success_count: Optional[int] = None
    success_total: Optional[int] = None
    last_successful_order_at: Optional[datetime] = None
    days_since_last_successful_order: Optional[int] = None
    if clauses:
        history_result = await connection.execute(
            text(
                f"""
                SELECT COUNT(*), COALESCE(SUM(COALESCE(price, 0)), 0), MAX(created_at)
                FROM topup
                WHERE UPPER(TRIM(COALESCE(topup_status, '')))='SUCCESS'
                  AND ({' OR '.join(clauses)})
                """
            ),
            params,
        )
        history_row = history_result.first()
        success_count = int(history_row[0] or 0) if history_row else 0
        success_total = rupiah(history_row[1] if history_row else 0)
        last_successful_order_at = (
            parse_datetime(history_row[2], "UTC") if history_row and history_row[2] else None
        )
        if last_successful_order_at is not None:
            days_since_last_successful_order = max(
                int((moment - last_successful_order_at).total_seconds() // 86400),
                0,
            )

    return EligibilityProfile(
        authentication_status="member" if identity.is_authenticated else "guest",
        has_customer_account=has_account,
        account_status=("active" if account_active else "inactive") if has_account else None,
        account_created_at=account_created_at,
        account_age_days=account_age_days,
        successful_order_count=success_count,
        successful_order_total=success_total,
        last_successful_order_at=last_successful_order_at,
        days_since_last_successful_order=days_since_last_successful_order,
        has_successful_order=(success_count > 0) if success_count is not None else None,
        customer_id=identity.customer_id if has_account and identity.is_authenticated else None,
        normalized_phone=history_phone,
        target_id=identity.target_id,
    )


async def _customer_segment(
    connection: AsyncConnection,
    *,
    customer_id: Optional[int] = None,
    phone: str = "",
    identity: Optional[PromoIdentity] = None,
) -> str:
    resolved_identity = identity or PromoIdentity(customer_id=customer_id, phone=phone)
    segment, _ = await _customer_history(connection, identity=resolved_identity)
    return segment


def _profile_segment(profile: EligibilityProfile) -> str:
    count = profile.successful_order_count
    if count is None:
        return "unknown"
    return "existing" if count > 0 else "new"


async def customer_history_for_identity(identity: PromoIdentity) -> dict[str, Any]:
    """Return safe diagnostics for admin simulation without exposing order rows."""

    profile = await eligibility_profile_for_identity(identity)
    payload = profile.to_safe_dict()
    success_count = profile.successful_order_count
    payload.update(
        {
            "status": (
                "unknown"
                if success_count is None
                else ("existing" if success_count > 0 else "new")
            ),
            # Retain the old response keys while exposing the richer facts.
            "success_order_count": success_count or 0,
            "has_success_order": profile.has_successful_order,
        }
    )
    return payload


async def eligibility_profile_for_identity(
    identity: PromoIdentity,
    *,
    now: Optional[datetime] = None,
) -> EligibilityProfile:
    engine = get_engine()
    moment = now or datetime.now(timezone.utc)
    async with engine.connect() as connection:
        return await _eligibility_profile(connection, identity, now=moment)


async def _refresh_authenticated_identity(
    connection: AsyncConnection,
    identity: PromoIdentity,
) -> PromoIdentity:
    """Re-read account activity in the transaction that reserves the promo."""

    if not identity.is_authenticated or identity.customer_id is None:
        return identity
    lock_suffix = "" if _is_sqlite(connection) else " FOR UPDATE"
    result = await connection.execute(
        text(
            """
            SELECT COALESCE(phone, ''), COALESCE(active, 0)
            FROM customer_accounts
            WHERE id=:customer_id
            LIMIT 1
            """
            + lock_suffix
        ),
        {"customer_id": identity.customer_id},
    )
    row = result.first()
    return PromoIdentity(
        customer_id=identity.customer_id,
        # A logged-in member's promotion identity must use the account phone
        # from the locked database row, never a foreign request phone.
        phone=str(row[0] or "") if row else "",
        target_id=identity.target_id,
        is_authenticated=True,
        account_active=bool(row and row[1]),
    )


async def _active_promotion_rows(connection: AsyncConnection, *, lock: bool) -> list[dict[str, Any]]:
    suffix = " FOR UPDATE" if lock and not _is_sqlite(connection) else ""
    result = await connection.execute(
        text(
            """
            SELECT *
            FROM promos
            WHERE COALESCE(active, 1)=1
              AND archived_at IS NULL
            ORDER BY COALESCE(priority, 0) DESC, created_at DESC
            """
            + suffix
        )
    )
    return [_mapping(row) for row in result.mappings().all()]


async def _attach_targets(connection: AsyncConnection, promotions: list[dict[str, Any]]) -> None:
    promo_ids = [int(promo["id"]) for promo in promotions if promo.get("id") is not None]
    if not promo_ids:
        return
    statement = text(
        """
        SELECT pt.promo_id, pt.excluded, catalog.target_type, catalog.target_key,
               catalog.label, catalog.metadata_json
        FROM promotion_targets pt
        JOIN promotion_target_catalog catalog ON catalog.id=pt.target_option_id
        WHERE pt.promo_id IN :promo_ids
          AND COALESCE(catalog.active, 1)=1
        ORDER BY pt.id
        """
    ).bindparams(bindparam("promo_ids", expanding=True))
    rows = await connection.execute(statement, {"promo_ids": promo_ids})
    targets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows.mappings().all():
        targets[int(row["promo_id"])].append(_mapping(row))

    customer_statement = text(
        """
        SELECT promo_id, customer_id
        FROM promotion_customer_targets
        WHERE promo_id IN :promo_ids
        """
    ).bindparams(bindparam("promo_ids", expanding=True))
    customer_rows = await connection.execute(customer_statement, {"promo_ids": promo_ids})
    customer_targets: dict[int, list[int]] = defaultdict(list)
    for row in customer_rows.mappings().all():
        customer_targets[int(row["promo_id"])].append(int(row["customer_id"]))

    for promo in promotions:
        promo_id = int(promo["id"])
        promo["targets"] = targets[promo_id]
        promo["customer_targets"] = customer_targets[promo_id]


def _redemption_timestamp(row: Mapping[str, Any]) -> Optional[datetime]:
    for key in ("reserved_at", "redeemed_at", "created_at"):
        parsed = parse_datetime(row.get(key), "UTC")
        if parsed:
            return parsed
    return None


def _same_local_day(value: Optional[datetime], now: datetime, zone_name: Any) -> bool:
    if value is None:
        return False
    try:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(str(zone_name or "Asia/Jakarta"))
    except Exception:
        zone = timezone.utc
    return value.astimezone(zone).date() == now.astimezone(zone).date()


async def _load_redemptions(connection: AsyncConnection, promo_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not promo_ids:
        return []
    statement = text(
        """
        SELECT *
        FROM promotion_redemptions
        WHERE promo_id IN :promo_ids
          AND UPPER(status) IN ('RESERVED', 'REDEEMED')
        """
    ).bindparams(bindparam("promo_ids", expanding=True))
    result = await connection.execute(statement, {"promo_ids": list(promo_ids)})
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw in result.mappings().all():
        row = _mapping(raw)
        expires_at = parse_datetime(row.get("expires_at"), "UTC")
        if str(row.get("status") or "").upper() == "RESERVED" and expires_at and expires_at <= now:
            continue
        rows.append(row)
    return rows


async def _load_legacy_usage(connection: AsyncConnection, promotions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_code = {
        normalize_code(promo.get("code")): int(promo["id"])
        for promo in promotions
        if normalize_code(promo.get("code")) and promo.get("id") is not None
    }
    if not by_code:
        return []
    result = await connection.execute(
        text(
            """
            SELECT id AS order_id, promo_id, promo_code, customer_id, phone, target_id,
                   nominal AS product_sku, payment_method,
                   COALESCE(promo_original_price, price, 0) AS original_amount,
                   COALESCE(promo_discount_amount, 0) AS discount_amount,
                   COALESCE(price, 0) AS final_amount,
                   payment_status, created_at
            FROM topup
            WHERE COALESCE(promo_code, '')<>''
              AND UPPER(COALESCE(payment_status, '')) NOT IN
                  ('CANCELED', 'CANCELLED', 'FAILED', 'EXPIRED', 'REFUND', 'REFUNDED')
            """
        )
    )
    rows: list[dict[str, Any]] = []
    for raw in result.mappings().all():
        item = _mapping(raw)
        promo_id = item.get("promo_id") or by_code.get(normalize_code(item.get("promo_code")))
        if not promo_id:
            continue
        item["promo_id"] = int(promo_id)
        item["status"] = "REDEEMED" if str(item.get("payment_status") or "").upper() == "PAID" else "RESERVED"
        item["customer_hash"] = customer_hash(item.get("phone"))
        item["target_hash"] = target_hash(item.get("product_sku"), item.get("target_id"))
        item["reserved_at"] = item.get("created_at")
        item["legacy"] = True
        rows.append(item)
    return rows


def _attach_usage(
    promotions: list[dict[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    context: PromotionContext,
) -> None:
    by_promo: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    # Real redemption rows win over compatibility rows for the same order.
    ordered_rows = sorted(rows, key=lambda item: bool(item.get("legacy")))
    for row in ordered_rows:
        promo_id = int(row.get("promo_id") or 0)
        order_id = str(row.get("order_id") or "")
        key = (promo_id, order_id)
        if order_id and key in seen:
            continue
        seen.add(key)
        by_promo[promo_id].append(row)

    context_customer_hash = customer_hash(context.phone)
    context_target_hash = target_hash(context.sku, context.target_id)
    now = context.now.astimezone(timezone.utc)
    for promo in promotions:
        promo_rows = by_promo[int(promo.get("id") or 0)]
        daily_rows = [row for row in promo_rows if _same_local_day(_redemption_timestamp(row), now, promo.get("timezone"))]
        promo["usage_count"] = len(promo_rows)
        promo["daily_usage_count"] = len(daily_rows)
        promo["discount_spent"] = sum(rupiah(row.get("discount_amount")) for row in promo_rows)

        if context.customer_id is not None:
            customer_rows = [row for row in promo_rows if row.get("customer_id") == context.customer_id]
        elif context_customer_hash:
            customer_rows = [row for row in promo_rows if row.get("customer_hash") == context_customer_hash]
        else:
            customer_rows = []
        promo["customer_usage_count"] = len(customer_rows)
        promo["customer_daily_usage_count"] = len(
            [row for row in customer_rows if _same_local_day(_redemption_timestamp(row), now, promo.get("timezone"))]
        )
        promo["phone_usage_count"] = len(
            [row for row in promo_rows if context_customer_hash and row.get("customer_hash") == context_customer_hash]
        )
        promo["target_usage_count"] = len(
            [row for row in promo_rows if context_target_hash and row.get("target_hash") == context_target_hash]
        )


async def _release_expired_with_connection(connection: AsyncConnection, *, now: Optional[datetime] = None) -> int:
    moment = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    result = await connection.execute(
        text(
            """
            UPDATE promotion_redemptions
            SET status='RELEASED', released_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE UPPER(status)='RESERVED'
              AND expires_at IS NOT NULL
              AND expires_at<=:now
            """
        ),
        {"now": moment},
    )
    return int(result.rowcount or 0)


async def _promotions_for_context(
    connection: AsyncConnection,
    context: PromotionContext,
    *,
    lock: bool = False,
) -> list[dict[str, Any]]:
    if lock:
        await _release_expired_with_connection(connection, now=context.now)
    promotions = await _active_promotion_rows(connection, lock=lock)
    await _attach_targets(connection, promotions)
    promo_ids = [int(promo["id"]) for promo in promotions]
    redemptions = await _load_redemptions(connection, promo_ids)
    redemptions.extend(await _load_legacy_usage(connection, promotions))
    _attach_usage(promotions, redemptions, context=context)
    return promotions


def build_context(
    product: Mapping[str, Any],
    *,
    base_price: Optional[Any] = None,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
    customer_segment: str = "all",
    identity: Optional[PromoIdentity] = None,
    eligibility_profile: Optional[EligibilityProfile] = None,
    now: Optional[datetime] = None,
) -> PromotionContext:
    resolved_identity = identity or PromoIdentity(
        customer_id=customer_id,
        phone=str(phone or ""),
        target_id=str(target_id or ""),
    )
    return PromotionContext(
        base_price=rupiah(product.get("price") if base_price is None else base_price),
        sku=str(product.get("sku") or product.get("nominal") or ""),
        product_id=str(product.get("id")) if product.get("id") is not None else None,
        provider=str(product.get("provider") or ""),
        category=str(product.get("category") or ""),
        brand=str(product.get("brand") or ""),
        supplier=str(product.get("supplier") or product.get("provider_type") or ""),
        payment_method=normalize_payment_code(payment_method),
        promo_code=normalize_code(promo_code) or None,
        customer_id=resolved_identity.customer_id,
        phone=resolved_identity.phone,
        target_id=resolved_identity.target_id,
        customer_segment=customer_segment,
        identity=resolved_identity,
        eligibility_profile=eligibility_profile,
        now=now or datetime.now(timezone.utc),
    )


async def quote_product(
    product: Mapping[str, Any],
    *,
    base_price: Optional[Any] = None,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
    identity: Optional[PromoIdentity] = None,
    now: Optional[datetime] = None,
) -> PromotionQuote:
    engine = get_engine()
    async with engine.connect() as connection:
        moment = now or datetime.now(timezone.utc)
        resolved_identity = identity or PromoIdentity(
            customer_id=customer_id,
            phone=str(phone or ""),
            target_id=str(target_id or ""),
        )
        eligibility_profile = await _eligibility_profile(
            connection,
            resolved_identity,
            now=moment,
        )
        segment = _profile_segment(eligibility_profile)
        context = build_context(
            product,
            base_price=base_price,
            promo_code=promo_code,
            payment_method=payment_method,
            phone=phone,
            target_id=target_id,
            customer_id=customer_id,
            customer_segment=segment,
            identity=resolved_identity,
            eligibility_profile=eligibility_profile,
            now=moment,
        )
        promotions = await _promotions_for_context(connection, context)
        return quote_promotions(promotions, context)


async def quote_catalog_products(
    products: Sequence[Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> dict[str, PromotionQuote]:
    """Quote storefront products with one database read of promotion rules."""

    if not products:
        return {}
    moment = now or datetime.now(timezone.utc)
    seed_context = build_context(products[0], customer_segment="unknown", now=moment)
    engine = get_engine()
    async with engine.connect() as connection:
        promotions = await _promotions_for_context(connection, seed_context)
    quotes: dict[str, PromotionQuote] = {}
    for product in products:
        context = build_context(product, customer_segment="unknown", now=moment)
        quotes[context.sku] = quote_promotions(promotions, context)
    return quotes


def code_failure_reason(quote: PromotionQuote) -> tuple[str, str]:
    requested = normalize_code(quote.requested_code)
    reason = "code_not_found"
    for rejection in quote.rejections:
        if normalize_code(rejection.get("code")) == requested:
            reason = str(rejection.get("reason") or reason)
            break
    messages = {
        "code_not_found": "Kode promo tidak ditemukan",
        "not_started": "Promo belum memasuki periode berlaku",
        "expired": "Promo sudah berakhir",
        "paused": "Promo sedang dijeda",
        "disabled": "Promo sedang dinonaktifkan",
        "quota_exhausted": "Kuota promo sudah habis",
        "daily_quota_exhausted": "Kuota promo hari ini sudah habis",
        "budget_exhausted": "Anggaran diskon promo sudah habis",
        "customer_limit": "Batas penggunaan promo pelanggan sudah tercapai",
        "customer_daily_limit": "Batas penggunaan promo pelanggan hari ini sudah tercapai",
        "phone_limit": "Batas penggunaan promo untuk nomor ini sudah tercapai",
        "target_limit": "Batas penggunaan promo untuk akun tujuan ini sudah tercapai",
        "minimum_transaction": "Minimum transaksi promo belum terpenuhi",
        "payment_method": "Metode pembayaran tidak sesuai dengan promo",
        "target_excluded": "Produk ini dikecualikan dari promo",
        "target_not_included": "Produk tidak termasuk dalam promo",
        "customer_segment": "Promo tidak tersedia untuk segmen pelanggan ini",
        "new_customer_only": "Promo ini hanya berlaku untuk pembeli pertama.",
        "existing_customer_only": "Promo ini hanya berlaku untuk pelanggan yang sudah pernah bertransaksi.",
        "customer_not_selected": "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
        "customer_not_targeted": "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
        "customer_identity_required": "Nomor WhatsApp atau akun customer diperlukan untuk memeriksa promo ini.",
        "phone_required": "Nomor WhatsApp diperlukan untuk memeriksa batas penggunaan promo",
        "target_id_required": "ID tujuan diperlukan untuk memeriksa batas penggunaan promo",
        "member_login_required": "Masuk ke akun untuk menggunakan promo ini.",
        "guest_only": "Promo ini hanya berlaku untuk checkout tanpa akun.",
        "account_inactive": "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
        "eligibility_rule_not_met": "Anda belum memenuhi syarat promo ini.",
        "account_too_old": "Promo ini hanya berlaku untuk akun yang lebih baru.",
        "first_purchase_only": "Promo ini hanya berlaku untuk pembeli pertama.",
        "min_successful_orders_not_met": "Jumlah transaksi berhasil Anda belum memenuhi syarat promo.",
        "min_successful_spend_not_met": "Total pembelian berhasil Anda belum memenuhi syarat promo.",
        "invalid_eligibility_rule": "Konfigurasi syarat promo belum valid.",
        "inactive_day": "Promo tidak aktif pada hari ini",
        "inactive_hour": "Promo tidak aktif pada jam ini",
        "calculation_invalid": "Aturan perhitungan promo belum valid",
        "zero_discount": "Promo tidak menghasilkan diskon untuk transaksi ini",
        "archived": "Promo sudah diarsipkan",
        "draft": "Promo belum diaktifkan",
        "ended": "Promo sudah berakhir",
        "target_missing": "Target produk promo belum dikonfigurasi",
        "voucher_not_configured": "Kode voucher promo belum dikonfigurasi",
        "promo_conflict": "Promo tidak dapat digabungkan dengan promo lain",
    }
    return reason, messages.get(reason, "Kode promo tidak berlaku untuk transaksi ini")


PROMOTION_REASON_CODES = {
    "code_not_found": "PROMO_NOT_FOUND",
    "voucher_required": "PROMO_NOT_FOUND",
    "voucher_mismatch": "PROMO_NOT_FOUND",
    "voucher_not_configured": "PROMO_CONFIGURATION_INVALID",
    "not_started": "PROMO_NOT_STARTED",
    "expired": "PROMO_EXPIRED",
    "paused": "PROMO_INACTIVE",
    "disabled": "PROMO_INACTIVE",
    "archived": "PROMO_INACTIVE",
    "draft": "PROMO_INACTIVE",
    "ended": "PROMO_INACTIVE",
    "quota_exhausted": "QUOTA_EXHAUSTED",
    "daily_quota_exhausted": "QUOTA_EXHAUSTED",
    "budget_exhausted": "BUDGET_EXHAUSTED",
    "customer_limit": "CUSTOMER_LIMIT_REACHED",
    "customer_daily_limit": "CUSTOMER_LIMIT_REACHED",
    "phone_limit": "PHONE_LIMIT_REACHED",
    "target_limit": "TARGET_LIMIT_REACHED",
    "minimum_transaction": "MINIMUM_TRANSACTION_NOT_MET",
    "payment_method": "PAYMENT_METHOD_NOT_ELIGIBLE",
    "target_excluded": "PRODUCT_NOT_ELIGIBLE",
    "target_not_included": "PRODUCT_NOT_ELIGIBLE",
    "target_missing": "PROMO_CONFIGURATION_INVALID",
    "customer_segment": "CUSTOMER_NOT_ELIGIBLE",
    "new_customer_only": "FIRST_PURCHASE_ONLY",
    "existing_customer_only": "EXISTING_CUSTOMER_ONLY",
    "customer_not_selected": "CUSTOMER_NOT_TARGETED",
    "customer_not_targeted": "CUSTOMER_NOT_TARGETED",
    "customer_identity_required": "CUSTOMER_IDENTITY_REQUIRED",
    "phone_required": "PHONE_REQUIRED",
    "target_id_required": "TARGET_ID_REQUIRED",
    "member_login_required": "MEMBER_LOGIN_REQUIRED",
    "guest_only": "GUEST_ONLY_PROMO",
    "account_inactive": "ACCOUNT_INACTIVE",
    "eligibility_rule_not_met": "ELIGIBILITY_RULE_NOT_MET",
    "account_too_old": "ACCOUNT_TOO_OLD",
    "first_purchase_only": "FIRST_PURCHASE_ONLY",
    "min_successful_orders_not_met": "MIN_SUCCESSFUL_ORDERS_NOT_MET",
    "min_successful_spend_not_met": "MIN_SUCCESSFUL_SPEND_NOT_MET",
    "invalid_eligibility_rule": "INVALID_ELIGIBILITY_RULE",
    "invalid_transaction_context": "INVALID_TRANSACTION_CONTEXT",
    "inactive_day": "PROMO_INACTIVE",
    "inactive_hour": "PROMO_INACTIVE",
    "calculation_invalid": "PROMO_CONFIGURATION_INVALID",
    "zero_discount": "PROMO_NOT_APPLICABLE",
    "promo_conflict": "PROMO_CONFLICT",
}


def promotion_failure_payload(reason: str, message: str) -> dict[str, Any]:
    """Build the stable, customer-safe rejection contract used by public APIs."""

    normalized_reason = str(reason or "code_not_found").strip().casefold()
    return {
        "valid": False,
        "reason_code": PROMOTION_REASON_CODES.get(normalized_reason, "PROMO_NOT_APPLICABLE"),
        "message": str(message or "Kode promo tidak berlaku untuk transaksi ini"),
    }


def code_failure_payload(quote: PromotionQuote) -> dict[str, Any]:
    reason, message = code_failure_reason(quote)
    return promotion_failure_payload(reason, message)


def order_snapshot(quote: PromotionQuote, context: PromotionContext) -> dict[str, Any]:
    return {
        "version": 2,
        "quoted_at": context.now.astimezone(timezone.utc).isoformat(),
        "sku": context.sku,
        "payment_method": context.payment_method,
        "base_price": quote.base_price,
        "discount_amount": quote.discount_amount,
        "final_price": quote.final_price,
        "requested_code": quote.requested_code,
        "promotions": [promotion.to_dict() for promotion in quote.applied],
    }


def order_promo_values(quote: PromotionQuote, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    primary = quote.primary
    return {
        "promo_id": primary.id if primary else None,
        "promo_name": primary.title if primary else None,
        "promo_type": primary.promo_type if primary else None,
        "promo_discount_type": primary.calculation_type if primary else None,
        "promo_snapshot": _json_dump(snapshot) if quote.applied else None,
        "promo_snapshot_version": 2 if quote.applied else 0,
        "promo_original_price": quote.base_price,
        "promo_discount_amount": quote.discount_amount,
    }


async def _insert_redemptions(
    connection: AsyncConnection,
    *,
    order_id: str,
    quote: PromotionQuote,
    context: PromotionContext,
    status: str,
    expires_at: Optional[datetime],
) -> None:
    normalized_status = str(status or "RESERVED").strip().upper()
    if normalized_status not in {"RESERVED", "REDEEMED"}:
        raise ValueError("Status redemption awal harus RESERVED atau REDEEMED")
    for promotion in quote.applied:
        promotion_snapshot = promotion.to_dict()
        params = {
            "promo_id": promotion.id,
            "order_id": order_id,
            "status": normalized_status,
            "customer_id": context.customer_id,
            "customer_hash": customer_hash(context.phone),
            "target_hash": target_hash(context.sku, context.target_id),
            "product_sku": context.sku,
            "payment_method": context.payment_method,
            "original_amount": promotion.price_before,
            "discount_amount": promotion.discount_amount,
            "final_amount": promotion.price_after,
            "voucher_code": promotion.voucher_code or None,
            "application_source": promotion.application_source,
            "expires_at": expires_at.astimezone(timezone.utc).replace(tzinfo=None) if expires_at else None,
            "snapshot_json": _json_dump(promotion_snapshot),
        }
        await connection.execute(
            text(
                """
                INSERT INTO promotion_redemptions (
                    promo_id, order_id, status, customer_id, customer_hash, target_hash,
                    product_sku, payment_method, original_amount, discount_amount,
                    final_amount, voucher_code, application_source, reserved_at,
                    expires_at, redeemed_at, snapshot_json, updated_at
                )
                VALUES (
                    :promo_id, :order_id, :status, :customer_id, :customer_hash, :target_hash,
                    :product_sku, :payment_method, :original_amount, :discount_amount,
                    :final_amount, :voucher_code, :application_source, CURRENT_TIMESTAMP,
                    :expires_at,
                    CASE WHEN :status='REDEEMED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    :snapshot_json, CURRENT_TIMESTAMP
                )
                """
            ),
            params,
        )


async def persist_order_with_promotions(
    *,
    order_id: str,
    product: Mapping[str, Any],
    expected_quote: PromotionQuote,
    persist_order: PersistOrder,
    base_price: Optional[Any] = None,
    promo_code: Optional[str] = None,
    payment_method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
    identity: Optional[PromoIdentity] = None,
    redemption_status: str = "RESERVED",
    expires_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> PromotionQuote:
    """Revalidate, persist the order, and reserve quota in one DB transaction."""

    engine = get_engine()
    async with engine.connect() as connection:
        await _begin_locked(connection)
        try:
            moment = now or datetime.now(timezone.utc)
            resolved_identity = identity or PromoIdentity(
                customer_id=customer_id,
                phone=str(phone or ""),
                target_id=str(target_id or ""),
            )
            resolved_identity = await _refresh_authenticated_identity(connection, resolved_identity)
            eligibility_profile = await _eligibility_profile(
                connection,
                resolved_identity,
                now=moment,
            )
            segment = _profile_segment(eligibility_profile)
            context = build_context(
                product,
                base_price=base_price,
                promo_code=promo_code,
                payment_method=payment_method,
                phone=phone,
                target_id=target_id,
                customer_id=customer_id,
                customer_segment=segment,
                identity=resolved_identity,
                eligibility_profile=eligibility_profile,
                now=moment,
            )
            promotions = await _promotions_for_context(connection, context, lock=True)
            confirmed = quote_promotions(promotions, context)
            expected_promo_ids = {int(item.id) for item in expected_quote.applied if int(item.id) > 0}
            account_rejection = next(
                (
                    rejection
                    for rejection in confirmed.rejections
                    if str(rejection.get("reason") or "") == "account_inactive"
                    and int(rejection.get("promo_id") or 0) in expected_promo_ids
                ),
                None,
            )
            if account_rejection is not None:
                rejection_quote = PromotionQuote(
                    base_price=confirmed.base_price,
                    final_price=confirmed.final_price,
                    requested_code=str(account_rejection.get("code") or ""),
                    code_valid=False,
                    rejections=(account_rejection,),
                )
                reason, detail = code_failure_reason(rejection_quote)
                raise PromotionCodeError(reason, detail)
            if promo_code and not confirmed.code_valid:
                reason, detail = code_failure_reason(confirmed)
                raise PromotionCodeError(reason, detail)
            if quote_fingerprint(confirmed) != quote_fingerprint(expected_quote):
                raise PromotionChangedError("Kondisi promo berubah. Muat ulang checkout lalu coba kembali.")

            snapshot = order_snapshot(confirmed, context)
            await persist_order(connection, confirmed, snapshot)
            await _insert_redemptions(
                connection,
                order_id=order_id,
                quote=confirmed,
                context=context,
                status=redemption_status,
                expires_at=expires_at,
            )
            await connection.commit()
            return confirmed
        except Exception:
            await connection.rollback()
            raise


async def finalize_order_promotions(order_id: str) -> int:
    engine = get_engine()
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                UPDATE promotion_redemptions
                SET status='REDEEMED',
                    redeemed_at=COALESCE(redeemed_at, CURRENT_TIMESTAMP),
                    expires_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE order_id=:order_id AND UPPER(status)='RESERVED'
                """
            ),
            {"order_id": order_id},
        )
        return int(result.rowcount or 0)


async def release_order_promotions(order_id: str, *, include_redeemed: bool = False) -> int:
    statuses = ["RESERVED"] + (["REDEEMED"] if include_redeemed else [])
    statement = text(
        """
        UPDATE promotion_redemptions
        SET status='RELEASED',
            released_at=COALESCE(released_at, CURRENT_TIMESTAMP),
            updated_at=CURRENT_TIMESTAMP
        WHERE order_id=:order_id AND UPPER(status) IN :statuses
        """
    ).bindparams(bindparam("statuses", expanding=True))
    engine = get_engine()
    async with engine.begin() as connection:
        result = await connection.execute(statement, {"order_id": order_id, "statuses": statuses})
        return int(result.rowcount or 0)


async def release_expired_reservations() -> int:
    engine = get_engine()
    async with engine.begin() as connection:
        return await _release_expired_with_connection(connection)


async def load_promotions_for_legacy_context(
    product: Mapping[str, Any],
    *,
    payment_method: Optional[str] = None,
    phone: Optional[str] = None,
    target_id: Optional[str] = None,
    customer_id: Optional[int] = None,
    promo_code: Optional[str] = None,
    identity: Optional[PromoIdentity] = None,
    now: Optional[datetime] = None,
) -> tuple[list[dict[str, Any]], PromotionContext]:
    engine = get_engine()
    async with engine.connect() as connection:
        moment = now or datetime.now(timezone.utc)
        resolved_identity = identity or PromoIdentity(
            customer_id=customer_id,
            phone=str(phone or ""),
            target_id=str(target_id or ""),
        )
        eligibility_profile = await _eligibility_profile(
            connection,
            resolved_identity,
            now=moment,
        )
        segment = _profile_segment(eligibility_profile)
        context = build_context(
            product,
            promo_code=promo_code,
            payment_method=payment_method,
            phone=phone,
            target_id=target_id,
            customer_id=customer_id,
            customer_segment=segment,
            identity=resolved_identity,
            eligibility_profile=eligibility_profile,
            now=moment,
        )
        promotions = await _promotions_for_context(connection, context)
        return promotions, context
