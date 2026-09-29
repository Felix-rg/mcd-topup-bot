import csv
import io
import json
import re
from enum import Enum
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, Optional, Union
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from app.database import db_execute, db_execute_rowcount, db_query, db_transaction
from app.engine import (
    MAX_DIGIFLAZZ_PREPAID_STATUS_LOOKUP_AGE_DAYS,
    MAX_PROVIDER_RETRY,
    claim_provider_status_reconciliation,
    release_provider_status_reconciliation,
)
from app.security import create_access_token, decode_access_token, verify_password
from app.services.digiflazz_service import (
    DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE,
    DIGIFLAZZ_OUTCOME_PENDING,
    DIGIFLAZZ_OUTCOME_SENT_UNKNOWN,
    DIGIFLAZZ_OUTCOME_SUCCESS,
    cek_status_digiflazz,
    cek_status_postpaid,
    classify_digiflazz_transaction_response,
    create_digiflazz_deposit_ticket,
    digiflazz_response_data,
    get_digiflazz_balance,
)
from app.services.catalog_sync_service import sync_digiflazz_products
from app.services.provider_settings import (
    SECRET_PROVIDER_KEYS,
    build_provider_admin_payload,
    get_provider_runtime_settings,
    update_provider_runtime_settings,
)
from app.services.product_utils import normalized_category_name, normalized_provider_name
from app.services.order_service import calculate_admin_fee
from app.services.tripay_service import (
    calculate_fee,
    check_transaction_status,
    get_ewallet_detail,
    get_payment_channels,
    get_transaction_detail,
    link_ewallet,
    list_merchant_transactions,
    unlink_ewallet,
)
from app.services.wallet_service import wallet_add_entry, wallet_balance
from app.utils import hash_password
from app.core.settings import settings
from app.promotions.engine import (
    PromoIdentity,
    canonicalize_eligibility_rules,
    eligibility_customer_ids,
    eligibility_options_metadata,
    eligibility_rules_summary,
    evaluate_promotion_eligibility,
    legacy_eligibility_rules,
    normalize_payment_code,
    normalize_phone,
    normalize_sku,
    normalize_voucher_code,
    quote_promotions,
)
from app.promotions.service import (
    build_context,
    code_failure_payload,
    eligibility_profile_for_identity,
    finalize_order_promotions,
    release_order_promotions,
)

router = APIRouter(prefix="/admin", tags=["admin"])

try:
    APP_TIMEZONE = ZoneInfo("Asia/Jakarta")
except Exception:
    APP_TIMEZONE = timezone(timedelta(hours=7), name="WIB")

PERMISSIONS = {
    "orders:view": "Melihat transaksi dan statistik",
    "orders:manage": "Melakukan retry, update status, dan refund order",
    "products:manage": "Mengelola produk dan sinkronisasi provider",
    "promos:manage": "Mengelola promo dan kode voucher",
    "admins:manage": "Mengelola akun admin dan hak akses",
    "content:manage": "Mengelola halaman/konten website",
    "settings:manage": "Mengelola logo dan identitas website",
    "api:monitor": "Melihat status API Tripay, Digiflazz, dan engine transaksi",
    "finance:view": "Melihat laporan omzet, profit, dan export transaksi",
    "customers:manage": "Mengelola data customer dan blacklist",
    "audit:view": "Melihat audit log admin dan webhook",
}

ROLE_PERMISSIONS = {
    "owner": list(PERMISSIONS.keys()),
    "manager": [
        "orders:view",
        "orders:manage",
        "products:manage",
        "promos:manage",
        "content:manage",
        "finance:view",
        "customers:manage",
    ],
    "marketing": ["promos:manage", "content:manage"],
    "support": ["orders:view"],
    "catalog": ["products:manage"],
}


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    username: str
    role: str
    permissions: list[str]
    token: Optional[str] = None
    message: Optional[str] = None


class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    role: str = Field(default="support")
    permissions: Optional[list[str]] = None
    active: int = Field(default=1, ge=0, le=1)


class AdminUpdateRequest(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8)
    role: Optional[str] = None
    permissions: Optional[list[str]] = None
    active: Optional[int] = Field(default=None, ge=0, le=1)


class SiteSettingsUpdateRequest(BaseModel):
    site_name: Optional[str] = None
    logo_url: Optional[str] = None
    footer_text: Optional[str] = None


class ProviderSettingsUpdateRequest(BaseModel):
    active_payment_provider: Optional[str] = None
    active_topup_provider: Optional[str] = None
    backup_topup_provider: Optional[str] = None
    topup_failover_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    tripay_invoice_expiry_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    digiflazz_testing_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    digiflazz_max_price_margin_percent: Optional[float] = Field(default=None, ge=0, le=100)
    digiflazz_low_balance_threshold: Optional[float] = Field(default=None, ge=0)
    tripay_wallet_sync_interval_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    digiflazz_product_auto_sync_enabled: Optional[int] = Field(default=None, ge=0, le=1)
    digiflazz_product_sync_interval_minutes: Optional[int] = Field(default=None, ge=30, le=1440)
    tripay_base_url: Optional[str] = None
    tripay_api_key: Optional[str] = None
    tripay_private_key: Optional[str] = None
    tripay_merchant_code: Optional[str] = None
    digiflazz_base_url: Optional[str] = None
    digiflazz_username: Optional[str] = None
    digiflazz_api_key: Optional[str] = None
    digiflazz_webhook_secret: Optional[str] = None


class SitePageCreateRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=120)
    title: str = Field(..., min_length=1, max_length=255)
    excerpt: Optional[str] = None
    content: Optional[str] = None
    image_url: Optional[str] = None
    page_type: str = Field(default="general")
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    secondary_cta_text: Optional[str] = None
    secondary_cta_url: Optional[str] = None
    promo_code: Optional[str] = None
    highlight_title: Optional[str] = None
    highlight_items: Optional[str] = None
    terms_text: Optional[str] = None
    accent_color: str = Field(default="gold")
    active: int = Field(default=1, ge=0, le=1)
    show_on_website: int = Field(default=1, ge=0, le=1)


class SitePageUpdateRequest(BaseModel):
    slug: Optional[str] = Field(default=None, min_length=2, max_length=120)
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    excerpt: Optional[str] = None
    content: Optional[str] = None
    image_url: Optional[str] = None
    page_type: Optional[str] = None
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    secondary_cta_text: Optional[str] = None
    secondary_cta_url: Optional[str] = None
    promo_code: Optional[str] = None
    highlight_title: Optional[str] = None
    highlight_items: Optional[str] = None
    terms_text: Optional[str] = None
    accent_color: Optional[str] = None
    active: Optional[int] = Field(default=None, ge=0, le=1)
    show_on_website: Optional[int] = Field(default=None, ge=0, le=1)


class ProductCreateRequest(BaseModel):
    sku: Optional[str] = None
    provider: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    cost_price: Optional[float] = None
    cost: Optional[float] = None
    price: float = Field(..., ge=0)
    category: str = Field(default="Lainnya")
    active: int = Field(default=1, ge=0, le=1)
    description: Optional[str] = None
    image_url: Optional[str] = None
    logo_url: Optional[str] = None
    promo_title: Optional[str] = None
    promo_text: Optional[str] = None
    promo_badge: Optional[str] = None
    promo_url: Optional[str] = None
    display_order: int = Field(default=0, ge=0)

    @field_validator("price")
    @classmethod
    def price_must_exceed_cost(cls, v, info):
        cost = info.data.get("cost_price") or info.data.get("cost")
        if cost is not None and v < cost:
            raise ValueError("Harga jual tidak boleh lebih rendah dari harga modal")
        return v


class ProductUpdateRequest(BaseModel):
    provider: Optional[str] = None
    name: Optional[str] = None
    price: Optional[float] = None
    cost_price: Optional[float] = None
    cost: Optional[float] = None
    category: Optional[str] = None
    active: Optional[int] = Field(default=None, ge=0, le=1)
    description: Optional[str] = None
    image_url: Optional[str] = None
    logo_url: Optional[str] = None
    promo_title: Optional[str] = None
    promo_text: Optional[str] = None
    promo_badge: Optional[str] = None
    promo_url: Optional[str] = None
    display_order: Optional[int] = Field(default=None, ge=0)


class BulkMarkupRequest(BaseModel):
    brand: str = Field(..., description="Nama brand/provider, atau 'ALL' untuk semua")
    percent: float = Field(..., ge=0, le=500, description="Persentase markup dari modal")
    min_profit: int = Field(..., ge=0, description="Minimal profit dalam Rupiah")


def _normalize_optional_promo_integer(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if re.fullmatch(r"[+-]?\d+", stripped):
            return int(stripped)
        if re.fullmatch(r"[+-]?\d{1,3}(?:[.,]\d{3})+", stripped):
            return int(re.sub(r"[.,]", "", stripped))
        raise ValueError("harus berupa angka bulat")
    return value


def _normalize_optional_promo_decimal(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        rupiah_like = (
            bool(re.search(r"[^\d.,-]", stripped))
            or bool(re.fullmatch(r"-?\d{1,3}(\.\d{3})+", stripped))
            or "," in stripped
        )
        normalized = re.sub(r"[^\d-]", "", stripped) if rupiah_like else stripped
        if not normalized or normalized == "-" or "-" in normalized[1:]:
            raise ValueError("harus berupa angka")
        return normalized
    return value


PROMO_ACTIVE_DAY_ALIASES = {
    "sun": 0,
    "sunday": 0,
    "min": 0,
    "minggu": 0,
    "mon": 1,
    "monday": 1,
    "sen": 1,
    "senin": 1,
    "tue": 2,
    "tuesday": 2,
    "sel": 2,
    "selasa": 2,
    "wed": 3,
    "wednesday": 3,
    "rab": 3,
    "rabu": 3,
    "thu": 4,
    "thursday": 4,
    "kam": 4,
    "kamis": 4,
    "fri": 5,
    "friday": 5,
    "jum": 5,
    "jumat": 5,
    "sat": 6,
    "saturday": 6,
    "sab": 6,
    "sabtu": 6,
}


def _normalize_active_days_for_schema(value: Any) -> Any:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed
        except json.JSONDecodeError:
            value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        return value
    return value


def _normalize_active_day_item(value: Any) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("Hari aktif tidak boleh kosong dan harus berupa angka 0-6")
    key = str(value).strip().lower()
    if key in PROMO_ACTIVE_DAY_ALIASES:
        day = PROMO_ACTIVE_DAY_ALIASES[key]
    elif isinstance(value, int) or re.fullmatch(r"[+-]?\d+", key):
        day = int(value)
    else:
        raise ValueError("Hari aktif harus berupa angka 0-6")
    if day < 0 or day > 6:
        raise ValueError("Hari aktif harus berada pada rentang 0-6")
    return day


def _normalize_promo_relation_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("harus berupa ID angka bulat, bukan boolean")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\+?\d+", value.strip()):
        parsed = int(value.strip())
    else:
        raise ValueError("harus berupa ID angka bulat")
    if parsed <= 0:
        raise ValueError("harus berupa ID positif")
    return parsed


def _normalize_promo_string_code(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("harus berupa kode teks")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("tidak boleh kosong")
    if len(cleaned) > 64:
        raise ValueError("maksimal 64 karakter")
    return cleaned


PromoRelationId = Annotated[int, BeforeValidator(_normalize_promo_relation_id)]
PromoStringCode = Annotated[str, BeforeValidator(_normalize_promo_string_code)]
PromoActiveDay = Annotated[int, BeforeValidator(_normalize_active_day_item)]


class PromoCustomerSegment(str, Enum):
    ALL = "all"
    MEMBERS_ONLY = "members_only"
    GUESTS_ONLY = "guests_only"
    NEW = "new"
    EXISTING = "existing"
    SPECIFIC = "specific"


def _normalize_customer_segment_for_schema(value: Any) -> Any:
    if value is None or isinstance(value, PromoCustomerSegment):
        return value
    if not isinstance(value, str):
        raise ValueError("Segmentasi pelanggan tidak dikenal")
    cleaned = value.strip().lower()
    if cleaned == "all_customers":
        return PromoCustomerSegment.ALL.value
    canonical = {item.value for item in PromoCustomerSegment}
    if cleaned not in canonical:
        raise ValueError("Segmentasi pelanggan tidak dikenal")
    return cleaned


PromoCustomerSegmentValue = Annotated[
    PromoCustomerSegment,
    BeforeValidator(_normalize_customer_segment_for_schema),
]


class PromoNumericRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, validate_default=True)

    @field_validator(
        "usage_limit",
        "quota_daily",
        "max_per_customer",
        "max_per_customer_daily",
        "max_per_phone",
        "max_per_target",
        "stackable",
        "exclusive",
        "max_promotions_per_order",
        "priority",
        "show_on_website",
        "display_order",
        "allow_external_cta",
        "active",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def normalize_optional_integer(cls, value: Any) -> Any:
        return _normalize_optional_promo_integer(value)

    @field_validator(
        "discount_value",
        "max_discount",
        "minimum_transaction",
        "special_price",
        "budget_limit",
        "price_override",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def normalize_optional_decimal(cls, value: Any) -> Any:
        return _normalize_optional_promo_decimal(value)

    @field_validator(
        "target_option_ids",
        "excluded_target_option_ids",
        "customer_ids",
        "placements",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def normalize_optional_array(cls, value: Any) -> Any:
        return [] if value is None or value == "" else value

    @field_validator("active_days", mode="before", check_fields=False)
    @classmethod
    def normalize_active_days(cls, value: Any) -> Any:
        return _normalize_active_days_for_schema(value)


class PromoCreateRequest(PromoNumericRequest):
    title: str = Field(..., min_length=1)
    internal_code: Optional[str] = Field(default=None, max_length=64)
    code: Optional[str] = None
    promo_type: Optional[str] = None
    lifecycle_status: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    internal_description: Optional[str] = None
    customer_description: Optional[str] = None
    admin_notes: Optional[str] = None
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    image_url: Optional[str] = None
    rule_type: str = Field(default="content")
    target_scope: str = Field(default="all")
    target_value: Optional[str] = None
    target_option_ids: list[PromoRelationId] = Field(default_factory=list)
    excluded_target_option_ids: list[PromoRelationId] = Field(default_factory=list)
    discount_type: Optional[str] = None
    calculation_type: Optional[str] = None
    discount_value: Optional[Decimal] = Field(default=None, ge=0)
    max_discount: Optional[Decimal] = Field(default=None, ge=0)
    minimum_transaction: Optional[Decimal] = Field(default=None, ge=0)
    special_price: Optional[Decimal] = Field(default=None, ge=0)
    rounding_rule: str = "none"
    usage_limit: Optional[int] = Field(default=0, ge=0)
    quota_daily: Optional[int] = Field(default=0, ge=0)
    payment_methods: Optional[list[PromoStringCode]] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    max_per_customer: Optional[int] = Field(default=0, ge=0)
    max_per_customer_daily: Optional[int] = Field(default=0, ge=0)
    max_per_phone: Optional[int] = Field(default=0, ge=0)
    max_per_target: Optional[int] = Field(default=0, ge=0)
    eligibility_rules: Optional[Dict[str, Any]] = None
    customer_segment: PromoCustomerSegmentValue = PromoCustomerSegment.ALL
    customer_ids: list[PromoRelationId] = Field(default_factory=list)
    stackable: Optional[int] = Field(default=1, ge=0, le=1)
    exclusive: Optional[int] = Field(default=0, ge=0, le=1)
    max_promotions_per_order: Optional[int] = Field(default=1, ge=1, le=5)
    priority: Optional[int] = Field(default=0, ge=0)
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    timezone: str = "Asia/Jakarta"
    active_days: list[PromoActiveDay] = Field(default_factory=list)
    daily_start_time: Optional[str] = None
    daily_end_time: Optional[str] = None
    show_on_website: Optional[int] = Field(default=1, ge=0, le=1)
    placements: list[PromoStringCode] = Field(default_factory=lambda: ["promo_cards"])
    display_order: Optional[int] = Field(default=0, ge=0)
    allow_external_cta: Optional[int] = Field(default=0, ge=0, le=1)
    active: Optional[int] = Field(default=1, ge=0, le=1)


class PromoUpdateRequest(PromoNumericRequest):
    title: Optional[str] = None
    internal_code: Optional[str] = Field(default=None, max_length=64)
    code: Optional[str] = None
    promo_type: Optional[str] = None
    lifecycle_status: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    internal_description: Optional[str] = None
    customer_description: Optional[str] = None
    admin_notes: Optional[str] = None
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    image_url: Optional[str] = None
    rule_type: Optional[str] = None
    target_scope: Optional[str] = None
    target_value: Optional[str] = None
    target_option_ids: Optional[list[PromoRelationId]] = None
    excluded_target_option_ids: Optional[list[PromoRelationId]] = None
    discount_type: Optional[str] = None
    calculation_type: Optional[str] = None
    discount_value: Optional[Decimal] = Field(default=None, ge=0)
    max_discount: Optional[Decimal] = Field(default=None, ge=0)
    minimum_transaction: Optional[Decimal] = Field(default=None, ge=0)
    special_price: Optional[Decimal] = Field(default=None, ge=0)
    rounding_rule: Optional[str] = None
    usage_limit: Optional[int] = Field(default=None, ge=0)
    quota_daily: Optional[int] = Field(default=None, ge=0)
    payment_methods: Optional[list[PromoStringCode]] = None
    budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    max_per_customer: Optional[int] = Field(default=None, ge=0)
    max_per_customer_daily: Optional[int] = Field(default=None, ge=0)
    max_per_phone: Optional[int] = Field(default=None, ge=0)
    max_per_target: Optional[int] = Field(default=None, ge=0)
    eligibility_rules: Optional[Dict[str, Any]] = None
    customer_segment: Optional[PromoCustomerSegmentValue] = None
    customer_ids: Optional[list[PromoRelationId]] = None
    stackable: Optional[int] = Field(default=None, ge=0, le=1)
    exclusive: Optional[int] = Field(default=None, ge=0, le=1)
    max_promotions_per_order: Optional[int] = Field(default=None, ge=1, le=5)
    priority: Optional[int] = Field(default=None, ge=0)
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    timezone: Optional[str] = None
    active_days: Optional[list[PromoActiveDay]] = None
    daily_start_time: Optional[str] = None
    daily_end_time: Optional[str] = None
    show_on_website: Optional[int] = Field(default=None, ge=0, le=1)
    placements: Optional[list[PromoStringCode]] = None
    display_order: Optional[int] = Field(default=None, ge=0)
    allow_external_cta: Optional[int] = Field(default=None, ge=0, le=1)
    active: Optional[int] = Field(default=None, ge=0, le=1)


class PromoSimulationRequest(PromoCreateRequest):
    sku: str = Field(..., min_length=1)
    method: str = Field(default="QRIS", min_length=1)
    customer_id: Optional[int] = None
    customer_phone: Optional[str] = None
    target_id: Optional[str] = None
    simulation_at: Optional[str] = None
    voucher_code: Optional[str] = None
    price_override: Optional[Decimal] = Field(default=None, ge=0)


class PromoTargetPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_option_ids: list[PromoRelationId] = Field(default_factory=list)
    excluded_target_option_ids: list[PromoRelationId] = Field(default_factory=list)


class OrderStatusUpdateRequest(BaseModel):
    payment_status: Optional[str] = None
    topup_status: Optional[str] = None
    sn: Optional[str] = None
    note: Optional[str] = None


class OrderRefundRequest(BaseModel):
    note: Optional[str] = None


class OrderRetryRequest(BaseModel):
    reason: Optional[str] = None


class CustomerBlockRequest(BaseModel):
    phone: Optional[str] = None
    target_id: Optional[str] = None
    reason: str = Field(..., min_length=3)


class CustomerWalletAdjustRequest(BaseModel):
    phone: str = Field(..., min_length=8)
    amount: float = Field(..., gt=0)
    entry_type: str = Field(default="CREDIT")
    note: Optional[str] = None
    idempotency_key: Optional[str] = None


class DigiflazzDepositRequest(BaseModel):
    amount: int = Field(..., ge=10000)
    bank: str = Field(..., min_length=2, max_length=50)
    owner_name: str = Field(..., min_length=2, max_length=120)


class TripayEwalletRequest(BaseModel):
    wallet_type: str = Field(default="DANA", min_length=2, max_length=30)
    mobile_phone: str = Field(..., min_length=8, max_length=30)


class SupportTicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    admin_note: Optional[str] = None


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


PROMO_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,63}$")
PROMO_TYPES = {"banner", "automatic", "voucher", "special_price", "payment_method"}
PROMO_LIFECYCLE_STATUSES = {"draft", "active", "paused", "ended", "disabled", "archived"}
PROMO_CALCULATION_TYPES = {"percent", "fixed", "special_price"}
PROMO_ROUNDING_RULES = {"none", "floor_100", "floor_500", "floor_1000", "ceil_100", "ceil_500", "ceil_1000"}
PROMO_CUSTOMER_SEGMENT_ALIASES = {item.value: item.value for item in PromoCustomerSegment}
PROMO_CUSTOMER_SEGMENT_ALIASES["all_customers"] = PromoCustomerSegment.ALL.value
PROMO_CUSTOMER_SEGMENTS = {item.value for item in PromoCustomerSegment}
PROMO_CUSTOMER_SEGMENT_OPTIONS = (
    {
        "value": "all",
        "label": "Semua pelanggan",
        "description": "Berlaku untuk member maupun tamu.",
        "aliases": ["all_customers"],
    },
    {
        "value": "members_only",
        "label": "Khusus member terdaftar",
        "description": "Promo hanya dapat digunakan oleh pelanggan yang sudah masuk ke akun aktif.",
        "aliases": [],
    },
    {
        "value": "guests_only",
        "label": "Khusus guest",
        "description": "Promo hanya dapat digunakan tanpa login akun.",
        "aliases": [],
    },
    {
        "value": "new",
        "label": "Pembeli pertama",
        "description": "Guest atau member yang belum pernah memiliki transaksi berhasil.",
        "aliases": [],
    },
    {
        "value": "existing",
        "label": "Pelanggan lama",
        "description": "Guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.",
        "aliases": [],
    },
    {
        "value": "specific",
        "label": "Pelanggan tertentu",
        "description": "Hanya ID akun customer yang dipilih admin.",
        "aliases": [],
    },
)
PROMO_PLACEMENTS = {"home_banner", "promo_cards", "product", "checkout"}
PROMO_PAYMENT_METHOD_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,63}$")
# Local codes accepted by checkout/admin without making a provider request while
# a promo is being saved. Keep WALLET because it is an internal LIXAFA channel.
PROMO_PAYMENT_METHOD_CODES = {
    "QRIS",
    "QRISC",
    "QRIS2",
    "OVO",
    "DANA",
    "SHOPEEPAY",
    "LINKAJA",
    "WALLET",
    "BRIVA",
    "BNIVA",
    "MANDIRIVA",
    "BCAVA",
    "CIMBVA",
    "PERMATAVA",
    "MYBVA",
    "MUAMALATVA",
    "BSIVA",
    "SMSVA",
    "ALFAMART",
    "ALFAMIDI",
    "INDOMARET",
}


def _clean_promo_code(value: Optional[str], *, field_name: str) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    normalized = cleaned.upper()
    if not PROMO_CODE_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} hanya boleh berisi huruf, angka, underscore, atau tanda hubung (2-64 karakter)",
        )
    return normalized


def _clean_customer_segment(value: Optional[str], *, strict: bool = True) -> str:
    cleaned = (_clean_text(value) or "all").lower()
    canonical = PROMO_CUSTOMER_SEGMENT_ALIASES.get(cleaned)
    if canonical:
        return canonical
    if strict:
        raise HTTPException(status_code=400, detail="Segmentasi pelanggan tidak dikenal")
    # Preserve unknown historical values in read responses instead of making
    # an old row impossible to inspect from the admin dashboard.
    return cleaned


def _customer_segment_metadata(value: Optional[str], *, target_count: int = 0) -> Dict[str, Any]:
    canonical = _clean_customer_segment(value, strict=False)
    option = next(
        (item for item in PROMO_CUSTOMER_SEGMENT_OPTIONS if item["value"] == canonical),
        None,
    )
    label = str((option or {}).get("label") or canonical or "Semua pelanggan")
    description = str((option or {}).get("description") or "Segmentasi lama yang belum dikenal.")
    summary = f"{label}: {target_count} pelanggan dipilih" if canonical == "specific" else label
    return {
        "value": canonical,
        "label": label,
        "description": description,
        "target_count": max(int(target_count or 0), 0),
        "summary": summary,
    }


def _canonical_eligibility_for_write(value: Any) -> Dict[str, Any]:
    try:
        return canonicalize_eligibility_rules(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Aturan eligibility tidak valid: {exc}") from exc


def _eligibility_json(value: Dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _eligibility_for_audit(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    sanitized = json.loads(json.dumps(value))
    for condition in sanitized.get("conditions", []):
        if condition.get("field") != "normalized_phone":
            continue
        configured = condition.get("value")
        condition["value"] = {
            "redacted": True,
            "count": len(configured) if isinstance(configured, list) else (1 if configured else 0),
        }
    return sanitized


def _eligibility_response(
    raw_value: Any,
    *,
    customer_segment: str,
    customer_ids: list[int],
) -> Dict[str, Any]:
    if raw_value is None:
        effective = legacy_eligibility_rules(customer_segment, customer_ids)
        return {
            "eligibility_rules": effective,
            "eligibility_rules_stored": None,
            "eligibility_rules_storage": None,
            "eligibility_source": "legacy_adapter",
            "eligibility_legacy_segment": customer_segment,
            "eligibility_valid": True,
            "eligibility_errors": [],
            "eligibility_summary": eligibility_rules_summary(effective),
        }
    try:
        effective = canonicalize_eligibility_rules(raw_value)
    except (TypeError, ValueError) as exc:
        try:
            parsed = json.loads(str(raw_value))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        return {
            "eligibility_rules": parsed if isinstance(parsed, dict) else None,
            "eligibility_rules_stored": parsed if isinstance(parsed, dict) else raw_value,
            "eligibility_rules_storage": raw_value,
            "eligibility_source": "invalid_explicit",
            "eligibility_legacy_segment": None,
            "eligibility_valid": False,
            "eligibility_errors": [str(exc)],
            "eligibility_summary": "Aturan eligibility tidak valid; promo akan ditolak sampai diperbaiki.",
        }
    return {
        "eligibility_rules": effective,
        "eligibility_rules_stored": effective,
        "eligibility_rules_storage": raw_value,
        "eligibility_source": "explicit",
        "eligibility_legacy_segment": None,
        "eligibility_valid": True,
        "eligibility_errors": [],
        "eligibility_summary": eligibility_rules_summary(effective),
    }


def _masked_customer_phone(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    prefix = digits[:3]
    return f"{prefix}{'*' * max(len(digits) - 7, 3)}{digits[-4:]}"


def _rupiah_value(value: Any, *, field_name: str, allow_none: bool = True) -> Optional[int]:
    if value is None or value == "":
        return None if allow_none else 0
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} bukan nilai Rupiah yang valid") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise HTTPException(status_code=400, detail=f"{field_name} harus berupa Rupiah bulat dan tidak negatif")
    return int(number)


def _clean_promo_type(value: Optional[str], *, rule_type: Optional[str], code: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "").lower()
    if cleaned:
        if cleaned not in PROMO_TYPES:
            raise HTTPException(status_code=400, detail="Jenis promo tidak dikenal")
        return cleaned
    if (_clean_text(rule_type) or "content").lower() == "content":
        return "banner"
    return "voucher" if _clean_text(code) else "automatic"


def _clean_lifecycle_status(value: Optional[str], *, active: Optional[int] = None) -> str:
    cleaned = (_clean_text(value) or "").lower()
    if cleaned:
        if cleaned not in PROMO_LIFECYCLE_STATUSES:
            raise HTTPException(status_code=400, detail="Status lifecycle promo tidak dikenal")
        return cleaned
    if active is not None:
        return "active" if bool(active) else "disabled"
    return "draft"


def _clean_calculation_type(value: Optional[str], *, discount_type: Optional[str], promo_type: str) -> Optional[str]:
    cleaned = (_clean_text(value) or _clean_text(discount_type) or "").lower()
    if promo_type == "banner":
        return None
    if promo_type == "special_price" and not cleaned:
        return "special_price"
    if cleaned not in PROMO_CALCULATION_TYPES:
        raise HTTPException(status_code=400, detail="Jenis perhitungan wajib percent, fixed, atau special_price")
    return cleaned


def _clean_timezone(value: Optional[str]) -> str:
    cleaned = _clean_text(value) or "Asia/Jakarta"
    if cleaned == "Asia/Jakarta":
        return cleaned
    try:
        ZoneInfo(cleaned)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Zona waktu tidak valid") from exc
    return cleaned


def _clean_active_days(values: Any) -> list[int]:
    if values in (None, ""):
        return []
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except json.JSONDecodeError:
            values = [part.strip() for part in values.split(",") if part.strip()]
    result: list[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            day = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Hari aktif harus berupa angka 0-6") from exc
        if day < 0 or day > 6:
            raise HTTPException(status_code=400, detail="Hari aktif harus berada pada rentang 0-6")
        if day not in result:
            result.append(day)
    return sorted(result)


def _clean_placements(values: Any) -> list[str]:
    if values in (None, ""):
        return []
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except json.JSONDecodeError:
            values = [part.strip() for part in values.split(",") if part.strip()]
    normalized = [str(value or "").strip().lower() for value in values if str(value or "").strip()]
    unknown = sorted(set(normalized) - PROMO_PLACEMENTS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Placement promo tidak dikenal: {', '.join(unknown)}")
    return list(dict.fromkeys(normalized))


def _clean_daily_time(value: Optional[str], *, field_name: str) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    try:
        parsed = datetime.strptime(cleaned, "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} harus memakai format HH:MM") from exc
    return parsed.strftime("%H:%M")


def _validate_promo_cta(value: Optional[str], *, allow_external: bool) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    if any(ord(char) < 32 for char in cleaned) or "\\" in cleaned or cleaned.startswith("//"):
        raise HTTPException(status_code=400, detail="CTA URL tidak aman")
    if cleaned.startswith("#"):
        if not re.fullmatch(r"#[A-Za-z0-9_-]{1,80}", cleaned):
            raise HTTPException(status_code=400, detail="Anchor CTA tidak valid")
        return cleaned
    if cleaned.startswith("game:"):
        target = cleaned[5:].strip()
        if not target or len(target) > 120 or any(char in target for char in '<>"\''):
            raise HTTPException(status_code=400, detail="Target game CTA tidak valid")
        return f"game:{target}"
    if cleaned.startswith("modal:"):
        modal_id = cleaned[6:].strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", modal_id):
            raise HTTPException(status_code=400, detail="Target modal CTA tidak valid")
        return f"modal:{modal_id}"
    if cleaned.startswith("/"):
        return cleaned
    parsed = urlsplit(cleaned)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        hostname = (parsed.hostname or "").lower()
        if hostname in {"localhost", "127.0.0.1"} or hostname.endswith(".ngrok-free.app") or hostname.endswith(".ngrok.app"):
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            if parsed.fragment:
                path += f"#{parsed.fragment}"
            return path
        if not allow_external:
            raise HTTPException(status_code=400, detail="CTA eksternal harus diizinkan secara eksplisit")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=400, detail="CTA eksternal tidak boleh berisi credential")
        return cleaned
    raise HTTPException(status_code=400, detail="CTA harus berupa path internal, anchor, game, modal, atau URL HTTPS yang diizinkan")


def _clean_promo_rule_type(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "content").lower()
    return cleaned if cleaned in {"content", "price"} else "content"


def _clean_target_scope(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "all").lower()
    return cleaned if cleaned in {"all", "category", "provider", "sku"} else "all"


def _clean_discount_type(value: Optional[str]) -> Optional[str]:
    cleaned = (_clean_text(value) or "").lower()
    if not cleaned:
        return None
    return cleaned if cleaned in {"percent", "fixed"} else None


def _normalize_payment_methods(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = re.split(r"[,;\s]+", values)
    elif isinstance(values, list):
        raw_values = values
    else:
        raw_values = []

    normalized: list[str] = []
    for value in raw_values:
        cleaned = str(value or "").strip().upper()
        if not cleaned or cleaned in normalized:
            continue
        normalized.append(cleaned)
    return normalized


def _validate_payment_method_codes(values: Any) -> list[str]:
    normalized = _normalize_payment_methods(values)
    malformed = [code for code in normalized if not PROMO_PAYMENT_METHOD_CODE_RE.fullmatch(code)]
    if malformed:
        raise HTTPException(
            status_code=400,
            detail=f"Kode metode pembayaran tidak valid: {', '.join(malformed)}",
        )
    unavailable = [code for code in normalized if code not in PROMO_PAYMENT_METHOD_CODES]
    if unavailable:
        raise HTTPException(
            status_code=400,
            detail=f"Metode pembayaran tidak tersedia untuk promo: {', '.join(unavailable)}",
        )
    return normalized


def _payment_methods_json(values: Any) -> str:
    return json.dumps(_normalize_payment_methods(values))


def _payment_methods_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return _normalize_payment_methods(raw_value)
    try:
        parsed = json.loads(str(raw_value or "[]"))
        return _normalize_payment_methods(parsed)
    except json.JSONDecodeError:
        return _normalize_payment_methods(str(raw_value or ""))


def _parse_datetime(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(APP_TIMEZONE).replace(tzinfo=None)
        return parsed.isoformat(timespec="seconds")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Tanggal dan waktu promo tidak valid") from exc


def _validate_promo_schedule(
    starts_at: Optional[str],
    ends_at: Optional[str],
    daily_start_time: Optional[str] = None,
    daily_end_time: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    parsed_start = _parse_datetime(starts_at)
    parsed_end = _parse_datetime(ends_at)
    if parsed_start and parsed_end:
        start_value = datetime.fromisoformat(parsed_start)
        end_value = datetime.fromisoformat(parsed_end)
        if end_value <= start_value:
            raise HTTPException(status_code=400, detail="Waktu berakhir harus setelah waktu mulai")
    daily_start = _clean_daily_time(daily_start_time, field_name="Jam mulai harian")
    daily_end = _clean_daily_time(daily_end_time, field_name="Jam selesai harian")
    if bool(daily_start) != bool(daily_end):
        raise HTTPException(status_code=400, detail="Jam aktif harian harus memiliki waktu mulai dan selesai")
    if daily_start and daily_end and daily_start == daily_end:
        raise HTTPException(status_code=400, detail="Jam mulai dan selesai harian tidak boleh sama")
    return parsed_start, parsed_end, daily_start, daily_end


def _format_datetime(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _to_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=APP_TIMEZONE).astimezone(timezone.utc)
    except ValueError:
        return None


def _can_reconcile_digiflazz_status(order_type: Any, created_at: Any) -> bool:
    if str(order_type or "PREPAID").upper() == "POSTPAID":
        return True
    created = _to_utc_datetime(created_at)
    if created is None:
        return False
    return datetime.now(timezone.utc) - created < timedelta(days=MAX_DIGIFLAZZ_PREPAID_STATUS_LOOKUP_AGE_DAYS)


def _promo_runtime_meta(
    *,
    starts_at: Any,
    ends_at: Any,
    active: bool,
    show_on_website: bool,
    lifecycle_status: Optional[str] = None,
    usage_limit: int = 0,
    capacity_count: int = 0,
    budget_limit: float = 0,
    discount_committed: float = 0,
) -> Dict[str, Any]:
    lifecycle = str(lifecycle_status or "").strip().lower()
    lifecycle_meta = {
        "draft": ("draft", "Draft"),
        "paused": ("paused", "Dijeda"),
        "ended": ("ended", "Diakhiri"),
        "disabled": ("disabled", "Dinonaktifkan"),
        "archived": ("archived", "Diarsipkan"),
    }
    if lifecycle in lifecycle_meta:
        runtime_status, runtime_label = lifecycle_meta[lifecycle]
        return {
            "runtime_status": runtime_status,
            "runtime_label": runtime_label,
            "visible_on_website_now": False,
        }
    if not active:
        return {"runtime_status": "disabled", "runtime_label": "Dinonaktifkan", "visible_on_website_now": False}

    now_utc = datetime.now(timezone.utc)
    starts_at_utc = _to_utc_datetime(starts_at)
    ends_at_utc = _to_utc_datetime(ends_at)

    if starts_at_utc and now_utc < starts_at_utc:
        return {
            "runtime_status": "scheduled",
            "runtime_label": "Akan datang",
            "visible_on_website_now": False,
        }
    if ends_at_utc and now_utc > ends_at_utc:
        return {
            "runtime_status": "expired",
            "runtime_label": "Expired",
            "visible_on_website_now": False,
        }
    if usage_limit > 0 and capacity_count >= usage_limit:
        return {"runtime_status": "quota_exhausted", "runtime_label": "Kuota Habis", "visible_on_website_now": False}
    if budget_limit > 0 and discount_committed >= budget_limit:
        return {"runtime_status": "budget_exhausted", "runtime_label": "Budget Habis", "visible_on_website_now": False}

    return {
        "runtime_status": "live",
        "runtime_label": "Aktif",
        "visible_on_website_now": bool(show_on_website),
    }


def _timestamp_from_unix(value: Any) -> Optional[str]:
    try:
        numeric = int(value or 0)
        if numeric <= 0:
            return None
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _today_wib() -> str:
    return datetime.now(APP_TIMEZONE).date().isoformat()


def _is_configured(value: Any) -> bool:
    return bool(str(value or "").strip())


def _credential_item(label: str, value: Any) -> Dict[str, Any]:
    return {"label": label, "configured": _is_configured(value)}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _count_map(rows: list[tuple[Any, ...]]) -> Dict[str, int]:
    return {str(row[0] or "UNKNOWN"): _int_value(row[1]) for row in rows}


def _monitor_order_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "id": row[0],
        "phone": row[1],
        "nominal": row[2],
        "payment_status": row[3] or "",
        "topup_status": row[4] or "",
        "provider_retry_count": _int_value(row[5]),
        "provider_last_error": row[6] or "",
        "invoice_url": row[7] or "",
        "created_at": _format_datetime(row[8]),
    }


def _promo_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    active = bool(row[25])
    show_on_website = bool(row[24])
    usage_limit = _int_value(row[14])
    budget_limit = float(row[16] or 0)
    discount_spent = float(row[29] or 0) if len(row) > 29 else 0.0
    usage_count = _int_value(row[28]) if len(row) > 28 else 0
    lifecycle_status = str(row[32] or "") if len(row) > 32 else ("active" if active else "disabled")
    capacity_count = _int_value(row[59]) if len(row) > 59 else usage_count
    runtime_meta = _promo_runtime_meta(
        starts_at=row[22],
        ends_at=row[23],
        active=active,
        show_on_website=show_on_website,
        lifecycle_status=lifecycle_status,
        usage_limit=usage_limit,
        capacity_count=capacity_count,
        budget_limit=budget_limit,
        discount_committed=discount_spent,
    )
    promo = {
        "id": row[0],
        "title": row[1] or "",
        "code": row[2] or "",
        "description": row[3] or "",
        "badge": row[4] or "",
        "cta_text": row[5] or "",
        "cta_url": row[6] or "",
        "image_url": row[7] or "",
        "rule_type": row[8] or "content",
        "target_scope": row[9] or "all",
        "target_value": row[10] or "",
        "discount_type": row[11] or "",
        "discount_value": float(row[12] or 0),
        "max_discount": float(row[13] or 0),
        "usage_limit": usage_limit,
        "usage_count": usage_count,
        "usage_remaining": max(usage_limit - usage_count, 0) if usage_limit else None,
        "payment_methods": _payment_methods_list(row[15]),
        "budget_limit": budget_limit,
        "discount_spent": discount_spent,
        "budget_remaining": max(budget_limit - discount_spent, 0) if budget_limit else None,
        "max_per_customer": _int_value(row[17]),
        "max_per_phone": _int_value(row[18]),
        "max_per_target": _int_value(row[19]),
        "stackable": bool(row[20]),
        "priority": _int_value(row[21]),
        "starts_at": _format_datetime(row[22]),
        "ends_at": _format_datetime(row[23]),
        "show_on_website": show_on_website,
        "active": active,
        **runtime_meta,
        "created_at": _format_datetime(row[26]),
        "updated_at": _format_datetime(row[27]),
    }
    if len(row) > 58:
        try:
            active_days = json.loads(str(row[45] or "[]"))
        except json.JSONDecodeError:
            active_days = []
        try:
            placements = json.loads(str(row[50] or "[]"))
        except json.JSONDecodeError:
            placements = []
        promo.update(
            {
                "internal_code": row[30] or "",
                "promo_type": row[31] or ("banner" if promo["rule_type"] == "content" else ("voucher" if promo["code"] else "automatic")),
                "lifecycle_status": lifecycle_status,
                "status": lifecycle_status,
                "rules_version": row[33] or "legacy_v1",
                "internal_description": row[34] or "",
                "customer_description": row[35] or promo["description"],
                "admin_notes": row[36] or "",
                "calculation_type": row[37] or promo["discount_type"],
                "minimum_transaction": float(row[38] or 0),
                "special_price": float(row[39]) if row[39] is not None else None,
                "rounding_rule": row[40] or "none",
                "quota_daily": _int_value(row[41]),
                "max_per_customer_daily": _int_value(row[42]),
                "customer_segment": _clean_customer_segment(row[43], strict=False),
                "timezone": row[44] or "Asia/Jakarta",
                "active_days": active_days if isinstance(active_days, list) else [],
                "daily_start_time": row[46] or "",
                "daily_end_time": row[47] or "",
                "exclusive": bool(row[48]),
                "max_promotions_per_order": max(_int_value(row[49]), 1),
                "placements": placements if isinstance(placements, list) else [],
                "display_order": _int_value(row[51]),
                "allow_external_cta": bool(row[52]),
                "legacy_compatible": bool(row[53]),
                "archived_at": _format_datetime(row[54]),
                "paused_at": _format_datetime(row[55]),
                "ended_at": _format_datetime(row[56]),
                "created_by": row[57] or "",
                "updated_by": row[58] or "",
                "capacity_count": capacity_count,
                "reserved_count": max(capacity_count - usage_count, 0),
                "eligibility_rules_raw": row[60] if len(row) > 60 else None,
            }
        )
    return promo


def _validate_promo_rules(
    *,
    rule_type: str,
    code: Optional[str],
    discount_type: Optional[str],
    discount_value: Optional[Union[Decimal, float]],
    promo_type: Optional[str] = None,
    calculation_type: Optional[str] = None,
    special_price: Optional[Union[Decimal, float]] = None,
    minimum_transaction: Optional[Union[Decimal, float]] = 0,
    max_discount: Optional[Union[Decimal, float]] = 0,
    usage_limit: Optional[int] = 0,
    budget_limit: Optional[float] = 0,
    max_per_customer: Optional[int] = 0,
    max_per_phone: Optional[int] = 0,
    max_per_target: Optional[int] = 0,
) -> None:
    resolved_type = _clean_promo_type(promo_type, rule_type=rule_type, code=code)
    resolved_calculation = calculation_type or discount_type
    if code and rule_type != "price":
        raise HTTPException(status_code=400, detail="Promo berkode harus memakai tipe Diskon Harga")
    if resolved_type == "voucher" and not code:
        raise HTTPException(status_code=400, detail="Promo voucher wajib memiliki kode voucher")
    if resolved_type == "automatic" and code:
        raise HTTPException(status_code=400, detail="Diskon otomatis tidak boleh meminta kode voucher")
    if resolved_type == "payment_method" and rule_type != "price":
        raise HTTPException(status_code=400, detail="Promo metode pembayaran harus memotong harga")
    if resolved_type != "banner" or rule_type == "price":
        if resolved_calculation not in PROMO_CALCULATION_TYPES:
            raise HTTPException(status_code=400, detail="Jenis diskon wajib percent, fixed, atau special_price")
        if resolved_calculation == "special_price":
            if special_price is None or Decimal(str(special_price)) < 0:
                raise HTTPException(status_code=400, detail="Harga khusus wajib nol atau lebih")
        elif discount_value is None or Decimal(str(discount_value)) <= 0:
            raise HTTPException(status_code=400, detail="Nilai diskon wajib lebih dari 0")
        if resolved_calculation == "percent":
            percent = Decimal(str(discount_value or 0))
            if percent != percent.to_integral_value():
                raise HTTPException(status_code=400, detail="Diskon persen harus berupa angka bulat 1-100")
            if percent > 100:
                raise HTTPException(status_code=400, detail="Diskon persen harus berada pada rentang 1-100%")
    for value, label in (
        (minimum_transaction, "Minimum transaksi"),
        (max_discount, "Maksimum diskon"),
        (budget_limit, "Budget promo"),
    ):
        if Decimal(str(value or 0)) < 0:
            raise HTTPException(status_code=400, detail=f"{label} tidak boleh negatif")


def _validate_promo_target(
    *,
    target_scope: str,
    target_value: Optional[str],
    included_options: list[Dict[str, Any]],
) -> None:
    if target_scope != "all" and not included_options and not _clean_text(target_value):
        raise HTTPException(
            status_code=400,
            detail="Target promo wajib dipilih ketika cakupan bukan semua produk",
        )


async def _ensure_unique_promo_codes(
    *,
    internal_code: Optional[str],
    voucher_code: Optional[str],
    exclude_id: Optional[int] = None,
    connection: Optional[AsyncConnection] = None,
) -> None:
    clauses = ["archived_at IS NULL"]
    params: Dict[str, Any] = {}
    if exclude_id is not None:
        clauses.append("id<>:exclude_id")
        params["exclude_id"] = exclude_id
    suffix = " AND ".join(clauses)
    if internal_code:
        rows = await db_query(
            f"SELECT id FROM promos WHERE UPPER(TRIM(internal_code))=UPPER(TRIM(:code)) AND {suffix} LIMIT 1",
            {**params, "code": internal_code},
            connection=connection,
        )
        if rows:
            raise HTTPException(status_code=409, detail="Kode internal promo sudah digunakan")
    if voucher_code:
        rows = await db_query(
            f"SELECT id FROM promos WHERE UPPER(TRIM(code))=UPPER(TRIM(:code)) AND {suffix} LIMIT 1",
            {**params, "code": voucher_code},
            connection=connection,
        )
        if rows:
            raise HTTPException(status_code=409, detail="Kode voucher sudah digunakan")


async def _validated_relation_options(option_ids: list[int]) -> list[Dict[str, Any]]:
    normalized_ids = list(dict.fromkeys(int(value) for value in option_ids if int(value) > 0))
    if not normalized_ids:
        return []
    placeholders = ", ".join(f":option_{index}" for index in range(len(normalized_ids)))
    params = {f"option_{index}": value for index, value in enumerate(normalized_ids)}
    rows = await db_query(
        f"""
        SELECT id, target_type, target_key, label, COALESCE(active, 1)
        FROM promotion_target_catalog
        WHERE id IN ({placeholders})
        """,
        params,
    )
    if len(rows) != len(normalized_ids):
        raise HTTPException(status_code=400, detail="Satu atau beberapa target promo tidak ditemukan")
    by_id = {
        int(row[0]): {
            "id": int(row[0]),
            "target_type": row[1],
            "target_key": row[2],
            "label": row[3],
            "active": bool(row[4]),
        }
        for row in rows
    }
    return [by_id[option_id] for option_id in normalized_ids]


async def _validated_customer_ids(customer_ids: list[int]) -> list[int]:
    normalized_ids = list(dict.fromkeys(int(value) for value in customer_ids if int(value) > 0))
    if not normalized_ids:
        return []
    placeholders = ", ".join(f":customer_{index}" for index in range(len(normalized_ids)))
    params = {f"customer_{index}": value for index, value in enumerate(normalized_ids)}
    rows = await db_query(f"SELECT id FROM customer_accounts WHERE id IN ({placeholders}) AND active=1", params)
    found = {int(row[0]) for row in rows}
    if found != set(normalized_ids):
        raise HTTPException(status_code=400, detail="Satu atau beberapa customer target tidak ditemukan/aktif")
    return normalized_ids


async def _replace_promo_relations(
    promo_id: int,
    *,
    included_ids: Optional[list[int]] = None,
    excluded_ids: Optional[list[int]] = None,
    customer_ids: Optional[list[int]] = None,
    connection: Optional[AsyncConnection] = None,
) -> None:
    if included_ids is not None or excluded_ids is not None:
        included = included_ids or []
        excluded = excluded_ids or []
        if set(included) & set(excluded):
            raise HTTPException(status_code=400, detail="Target include dan exclude tidak boleh sama")
        await db_execute(
            "DELETE FROM promotion_targets WHERE promo_id=:promo_id",
            {"promo_id": promo_id},
            connection=connection,
        )
        for option_id in included:
            await db_execute(
                "INSERT INTO promotion_targets (promo_id, target_option_id, excluded) VALUES (:promo_id, :option_id, 0)",
                {"promo_id": promo_id, "option_id": option_id},
                connection=connection,
            )
        for option_id in excluded:
            await db_execute(
                "INSERT INTO promotion_targets (promo_id, target_option_id, excluded) VALUES (:promo_id, :option_id, 1)",
                {"promo_id": promo_id, "option_id": option_id},
                connection=connection,
            )
    if customer_ids is not None:
        await db_execute(
            "DELETE FROM promotion_customer_targets WHERE promo_id=:promo_id",
            {"promo_id": promo_id},
            connection=connection,
        )
        for customer_id in customer_ids:
            await db_execute(
                "INSERT INTO promotion_customer_targets (promo_id, customer_id) VALUES (:promo_id, :customer_id)",
                {"promo_id": promo_id, "customer_id": customer_id},
                connection=connection,
            )


async def _promo_relations_by_ids(promo_ids: list[int]) -> Dict[int, Dict[str, Any]]:
    relations: Dict[int, Dict[str, Any]] = {
        promo_id: {"targets": [], "exclusions": [], "customer_ids": []} for promo_id in promo_ids
    }
    if not promo_ids:
        return relations
    placeholders = ", ".join(f":promo_{index}" for index in range(len(promo_ids)))
    params = {f"promo_{index}": value for index, value in enumerate(promo_ids)}
    rows = await db_query(
        f"""
        SELECT pt.promo_id, pt.excluded, c.id, c.target_type, c.target_key, c.label, COALESCE(c.active, 1)
        FROM promotion_targets pt
        JOIN promotion_target_catalog c ON c.id=pt.target_option_id
        WHERE pt.promo_id IN ({placeholders})
        ORDER BY pt.promo_id, pt.excluded, c.target_type, c.label
        """,
        params,
    )
    for promo_id, excluded, option_id, target_type, target_key, label, active in rows:
        item = {
            "id": int(option_id),
            "target_type": target_type,
            "target_key": target_key,
            "label": label,
            "active": bool(active),
        }
        relations[int(promo_id)]["exclusions" if excluded else "targets"].append(item)
    customer_rows = await db_query(
        f"SELECT promo_id, customer_id FROM promotion_customer_targets WHERE promo_id IN ({placeholders})",
        params,
    )
    for promo_id, customer_id in customer_rows:
        relations[int(promo_id)]["customer_ids"].append(int(customer_id))
    return relations


def _legacy_target_from_options(options: list[Dict[str, Any]]) -> tuple[str, str]:
    if not options:
        return "all", ""
    target_types = {str(item["target_type"]) for item in options}
    if len(target_types) == 1:
        target_type = next(iter(target_types))
        return target_type, ",".join(str(item["target_key"]) for item in options)
    # A legacy reader must fail closed for structured mixed targets.
    return "sku", "__structured_v2__"


def _normalize_role(role: Optional[str]) -> str:
    cleaned = (_clean_text(role) or "support").lower()
    return cleaned if cleaned in ROLE_PERMISSIONS else "support"


def _normalize_permissions(role: str, permissions: Optional[list[str]] = None) -> list[str]:
    if role == "owner":
        return list(ROLE_PERMISSIONS["owner"])
    if permissions is None:
        return list(ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["support"]))
    return [permission for permission in permissions if permission in PERMISSIONS]


def _parse_permissions(raw_value: Any, role: str) -> list[str]:
    if raw_value:
        try:
            parsed = json.loads(str(raw_value))
            if isinstance(parsed, list):
                normalized = [item for item in parsed if item in PERMISSIONS]
                if normalized:
                    if role == "owner":
                        return list(dict.fromkeys([*normalized, *ROLE_PERMISSIONS["owner"]]))
                    return normalized
        except json.JSONDecodeError:
            pass
    return list(ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["support"]))


def _admin_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    admin_id, username, role, permissions, active, created_at, updated_at = row
    normalized_role = _normalize_role(role)
    return {
        "id": admin_id,
        "username": username,
        "role": normalized_role,
        "permissions": _parse_permissions(permissions, normalized_role),
        "active": bool(active),
        "created_at": _format_datetime(created_at),
        "updated_at": _format_datetime(updated_at),
    }


def _clean_slug(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    slug = re.sub(r"[^a-z0-9-]+", "-", cleaned.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or None


def _clean_page_type(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "general").lower()
    return cleaned if cleaned in {"general", "promo"} else "general"


def _clean_accent_color(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "gold").lower()
    return cleaned if cleaned in {"gold", "green", "blue", "red"} else "gold"


def _normalize_media_path(value: str) -> str:
    return value.replace("\\", "/")


def _build_static_url(relative_path: str) -> str:
    return "/" + _normalize_media_path(relative_path).lstrip("/")


PAYMENT_STATUSES = {"UNPAID", "PAID", "EXPIRED", "CANCELED", "FAILED", "REFUNDED", "REFUND"}
TOPUP_STATUSES = {"PENDING_PAYMENT", "PROCESSING", "PENDING_PROVIDER", "SUCCESS", "FAILED"}
TERMINAL_PAYMENT_STATUSES = {"EXPIRED", "CANCELED", "FAILED", "REFUNDED", "REFUND"}
TERMINAL_TOPUP_STATUSES = {"SUCCESS", "FAILED"}


def _validate_admin_order_transition(
    before: Dict[str, Any],
    *,
    payment_status: Optional[str],
    topup_status: Optional[str],
    reason: str,
) -> None:
    current_payment = str(before.get("payment_status") or "").upper()
    current_topup = str(before.get("topup_status") or "").upper()
    next_payment = str(payment_status or current_payment or "").upper()
    next_topup = str(topup_status or current_topup or "").upper()

    if (payment_status or topup_status) and not reason:
        raise HTTPException(status_code=400, detail="Alasan perubahan status wajib diisi")

    if current_payment == "PAID" and next_payment in {"UNPAID", "EXPIRED", "CANCELED", "FAILED"}:
        raise HTTPException(status_code=409, detail="Payment PAID tidak boleh diturunkan")
    if current_payment == "REFUNDED" and next_payment != "REFUNDED":
        raise HTTPException(status_code=409, detail="Order REFUNDED tidak boleh diubah ke status pembayaran lain")
    if current_payment in {"EXPIRED", "CANCELED", "FAILED", "REFUND"} and next_payment != current_payment:
        raise HTTPException(status_code=409, detail="Status pembayaran terminal tidak boleh diubah manual")

    if current_topup == "SUCCESS" and next_topup != "SUCCESS":
        raise HTTPException(status_code=409, detail="Order SUCCESS tidak boleh diturunkan atau dikirim ulang")
    if current_topup == "FAILED" and next_topup not in {"FAILED"}:
        raise HTTPException(status_code=409, detail="Retry order FAILED harus lewat endpoint retry")

    if next_payment == "UNPAID" and next_topup in {"PROCESSING", "PENDING_PROVIDER", "SUCCESS"}:
        raise HTTPException(status_code=409, detail="Order belum dibayar tidak boleh masuk proses provider")


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_phone(value: Optional[str]) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isdigit() or ch == "+")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def _write_audit_log(
    admin: Dict[str, Any],
    *,
    action: str,
    entity_type: str,
    entity_id: Any,
    before: Any = None,
    after: Any = None,
    ip_address: Optional[str] = None,
    connection: Optional[AsyncConnection] = None,
) -> None:
    await db_execute(
        """
        INSERT INTO audit_logs (
            actor, action, entity_type, entity_id,
            before_json, after_json, ip_address
        )
        VALUES (
            :actor, :action, :entity_type, :entity_id,
            :before_json, :after_json, :ip_address
        )
        """,
        {
            "actor": admin.get("username") or admin.get("sub") or "admin",
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id or ""),
            "before_json": _json_dump(before) if before is not None else None,
            "after_json": _json_dump(after) if after is not None else None,
            "ip_address": ip_address,
        },
        connection=connection,
    )


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
    idempotency_key: Optional[str] = None,
    actor: Optional[str] = None,
) -> float:
    return await wallet_add_entry(
        customer_id=customer_id,
        entry_type=entry_type,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
        idempotency_key=idempotency_key,
        actor=actor,
    )


def _date_bound(value: Optional[str], *, end_of_day: bool = False) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    normalized = cleaned.replace("T", " ")
    if len(normalized) == 10 and normalized.count("-") == 2:
        return f"{normalized} {'23:59:59' if end_of_day else '00:00:00'}"
    return normalized


def _order_select_sql() -> str:
    return """
        SELECT
            t.id,
            t.phone,
            t.target_id,
            t.nickname,
            t.nominal,
            COALESCE(t.amount, 0),
            COALESCE(t.payment_status, ''),
            COALESCE(t.topup_status, ''),
            t.sn,
            t.note,
            t.created_at,
            COALESCE(t.price, p.price, 0),
            COALESCE(t.product_cost, p.cost_price, 0),
            COALESCE(t.payment_fee, 0),
            t.promo_code,
            t.payment_method,
            t.invoice_url,
            COALESCE(t.provider_retry_count, 0),
            t.provider_last_error,
            t.refund_status,
            t.refund_note,
            t.refunded_at,
            COALESCE(t.status_updated_at, t.created_at),
            COALESCE(p.name, t.nominal),
            COALESCE(p.provider, ''),
            COALESCE(p.category, ''),
            COALESCE(t.order_type, 'PREPAID'),
            t.payment_reference,
            t.payment_name,
            t.pay_code,
            t.pay_url,
            t.qr_url,
            t.payment_expired_at,
            t.provider_rc,
            t.provider_price,
            t.provider_selling_price,
            t.provider_last_balance,
            t.provider_payload
        FROM topup t
        LEFT JOIN products p ON p.sku = t.nominal
    """


def _order_filters(
    *,
    q: Optional[str] = None,
    payment_status: Optional[str] = None,
    topup_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    clauses: list[str] = []
    params: Dict[str, Any] = {}

    cleaned_q = _clean_text(q)
    if cleaned_q:
        clauses.append(
            """
            (
                LOWER(COALESCE(t.id, '')) LIKE :q OR
                LOWER(COALESCE(t.phone, '')) LIKE :q OR
                LOWER(COALESCE(t.target_id, '')) LIKE :q OR
                LOWER(COALESCE(t.nominal, '')) LIKE :q OR
                LOWER(COALESCE(p.name, '')) LIKE :q
            )
            """
        )
        params["q"] = f"%{cleaned_q.lower()}%"

    cleaned_payment = _clean_text(payment_status)
    if cleaned_payment:
        clauses.append("COALESCE(t.payment_status, '') = :payment_status")
        params["payment_status"] = cleaned_payment.upper()

    cleaned_topup = _clean_text(topup_status)
    if cleaned_topup:
        clauses.append("COALESCE(t.topup_status, '') = :topup_status")
        params["topup_status"] = cleaned_topup.upper()

    start = _date_bound(date_from)
    if start:
        clauses.append("t.created_at >= :date_from")
        params["date_from"] = start

    end = _date_bound(date_to, end_of_day=True)
    if end:
        clauses.append("t.created_at <= :date_to")
        params["date_to"] = end

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _order_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    amount = _float_value(row[5])
    product_cost = _float_value(row[12])
    payment_status = row[6] or ""
    topup_status = row[7] or ""
    estimated_profit = amount - product_cost if payment_status == "PAID" else 0.0
    realized_profit = amount - product_cost if payment_status == "PAID" and topup_status == "SUCCESS" else 0.0

    return {
        "id": row[0],
        "phone": row[1] or "",
        "target_id": row[2] or "",
        "nickname": row[3] or "",
        "nominal": row[4] or "",
        "amount": round(amount, 2),
        "payment_status": payment_status,
        "topup_status": topup_status,
        "sn": row[8] or "",
        "note": row[9] or "",
        "created_at": _format_datetime(row[10]),
        "price": round(_float_value(row[11]), 2),
        "product_cost": round(product_cost, 2),
        "payment_fee": round(_float_value(row[13]), 2),
        "promo_code": row[14] or "",
        "payment_method": row[15] or "",
        "invoice_url": row[16] or "",
        "provider_retry_count": _int_value(row[17]),
        "provider_last_error": row[18] or "",
        "refund_status": row[19] or "",
        "refund_note": row[20] or "",
        "refunded_at": _format_datetime(row[21]),
        "status_updated_at": _format_datetime(row[22]),
        "product_name": row[23] or row[4] or "",
        "provider": row[24] or "",
        "category": row[25] or "",
        "order_type": row[26] or "PREPAID",
        "payment_reference": row[27] or "",
        "payment_name": row[28] or "",
        "pay_code": row[29] or "",
        "pay_url": row[30] or "",
        "qr_url": row[31] or "",
        "payment_expired_at": _format_datetime(row[32]),
        "provider_rc": row[33] or "",
        "provider_price": round(_float_value(row[34]), 2),
        "provider_selling_price": round(_float_value(row[35]), 2),
        "provider_last_balance": round(_float_value(row[36]), 2),
        "provider_payload": row[37] or "",
        "estimated_profit": round(estimated_profit, 2),
        "realized_profit": round(realized_profit, 2),
    }


async def _fetch_orders(
    *,
    q: Optional[str] = None,
    payment_status: Optional[str] = None,
    topup_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    where_sql, params = _order_filters(
        q=q,
        payment_status=payment_status,
        topup_status=topup_status,
        date_from=date_from,
        date_to=date_to,
    )
    params["limit"] = max(1, min(int(limit or 100), 5000))
    rows = await db_query(
        f"""
        {_order_select_sql()}
        {where_sql}
        ORDER BY t.created_at DESC
        LIMIT :limit
        """,
        params,
    )
    return [_order_dict(row) for row in rows]


async def _fetch_order_detail(order_id: str) -> Optional[Dict[str, Any]]:
    rows = await db_query(
        f"""
        {_order_select_sql()}
        WHERE t.id=:id
        LIMIT 1
        """,
        {"id": order_id},
    )
    if not rows:
        return None
    return _order_dict(rows[0])


def _finance_summary(orders: list[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "orders": len(orders),
        "paid_orders": 0,
        "success_orders": 0,
        "failed_orders": 0,
        "pending_orders": 0,
        "refunded_orders": 0,
        "revenue": 0.0,
        "success_revenue": 0.0,
        "product_cost": 0.0,
        "payment_fee": 0.0,
        "estimated_profit": 0.0,
        "realized_profit": 0.0,
        "refund_amount": 0.0,
    }
    status_counts: Dict[str, int] = {}
    product_map: Dict[str, Dict[str, Any]] = {}

    for order in orders:
        payment_status = order["payment_status"]
        topup_status = order["topup_status"]
        status_counts[payment_status or "UNKNOWN"] = status_counts.get(payment_status or "UNKNOWN", 0) + 1

        if payment_status == "PAID":
            summary["paid_orders"] += 1
            summary["revenue"] += order["amount"]
            summary["payment_fee"] += order["payment_fee"]
            summary["estimated_profit"] += order["estimated_profit"]
        if payment_status == "REFUNDED":
            summary["refunded_orders"] += 1
            summary["refund_amount"] += order["amount"]

        if topup_status == "SUCCESS":
            summary["success_orders"] += 1
            if payment_status == "PAID":
                summary["success_revenue"] += order["amount"]
                summary["product_cost"] += order["product_cost"]
                summary["realized_profit"] += order["realized_profit"]
        elif topup_status == "FAILED":
            summary["failed_orders"] += 1
        else:
            summary["pending_orders"] += 1

        product_key = order["nominal"] or "-"
        product = product_map.setdefault(
            product_key,
            {
                "sku": product_key,
                "name": order["product_name"] or product_key,
                "provider": order["provider"],
                "orders": 0,
                "revenue": 0.0,
                "profit": 0.0,
            },
        )
        product["orders"] += 1
        if payment_status == "PAID":
            product["revenue"] += order["amount"]
            product["profit"] += order["realized_profit"]

    for key, value in list(summary.items()):
        if isinstance(value, float):
            summary[key] = round(value, 2)

    top_products = sorted(product_map.values(), key=lambda item: item["revenue"], reverse=True)[:10]
    for product in top_products:
        product["revenue"] = round(product["revenue"], 2)
        product["profit"] = round(product["profit"], 2)

    summary["success_rate"] = round((summary["success_orders"] / summary["paid_orders"]) * 100, 2) if summary["paid_orders"] else 0
    summary["failure_rate"] = round((summary["failed_orders"] / summary["paid_orders"]) * 100, 2) if summary["paid_orders"] else 0

    return {**summary, "status_counts": status_counts, "top_products": top_products}


async def _ensure_default_admin() -> None:
    username = settings.default_admin_username or "admin"
    password = settings.default_admin_password

    if not password:
        return

    admin_count = await db_query("SELECT COUNT(*) FROM admin")
    if admin_count and int(admin_count[0][0] or 0) > 0:
        return

    existing = await db_query("SELECT id FROM admin WHERE username=:username", {"username": username})
    if existing:
        return

    await db_execute(
        """
        INSERT INTO admin (username, password, role, permissions, active)
        VALUES (:username, :password, 'owner', :permissions, 1)
        """,
        {
            "username": username,
            "password": hash_password(password),
            "permissions": json.dumps(ROLE_PERMISSIONS["owner"]),
        },
    )


async def _require_admin(token: Optional[str] = Header(None, alias="token")) -> Dict[str, Any]:
    if token is None:
        raise HTTPException(status_code=401, detail="Token admin diperlukan")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token admin tidak valid")

    admin_id = payload.get("admin_id")
    username = payload.get("sub")
    if admin_id:
        row = await db_query(
            "SELECT id, username, role, permissions, active, created_at, updated_at FROM admin WHERE id=:id",
            {"id": admin_id},
        )
    else:
        row = await db_query(
            "SELECT id, username, role, permissions, active, created_at, updated_at FROM admin WHERE username=:username",
            {"username": username},
        )

    if not row:
        raise HTTPException(status_code=401, detail="Admin tidak ditemukan")

    admin = _admin_dict(row[0])
    if not admin["active"]:
        raise HTTPException(status_code=403, detail="Akun admin nonaktif")

    payload.update(admin)
    return payload


def _require_permission(permission: str):
    async def checker(admin: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
        if permission not in admin.get("permissions", []):
            raise HTTPException(status_code=403, detail="Akses admin tidak mencukupi")
        return admin

    return checker


@router.get("/health")
async def admin_health() -> Dict[str, Any]:
    return {"status": "ok", "service": "lixafa-admin"}


@router.get("/api/me")
async def get_current_admin(admin: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    return {
        "id": admin["id"],
        "username": admin["username"],
        "role": admin["role"],
        "permissions": admin["permissions"],
        "permission_labels": PERMISSIONS,
    }


@router.get("/api/roles")
async def get_admin_roles(_: Dict[str, Any] = Depends(_require_permission("admins:manage"))) -> Dict[str, Any]:
    return {"roles": ROLE_PERMISSIONS, "permissions": PERMISSIONS}


@router.get("/api/provider-status")
async def get_provider_status(_: Dict[str, Any] = Depends(_require_permission("api:monitor"))) -> Dict[str, Any]:
    runtime_settings = await get_provider_runtime_settings()
    provider_payload = build_provider_admin_payload(runtime_settings)
    payment_rows = await db_query(
        "SELECT COALESCE(payment_status, 'UNKNOWN'), COUNT(*) FROM topup GROUP BY COALESCE(payment_status, 'UNKNOWN')"
    )
    topup_rows = await db_query(
        "SELECT COALESCE(topup_status, 'UNKNOWN'), COUNT(*) FROM topup GROUP BY COALESCE(topup_status, 'UNKNOWN')"
    )
    summary_rows = await db_query(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN payment_status='UNPAID' THEN 1 ELSE 0 END) AS waiting_payment,
            SUM(CASE WHEN payment_status='PAID' AND COALESCE(topup_status, '') IN ('', 'PROCESSING') THEN 1 ELSE 0 END) AS engine_queue,
            SUM(CASE WHEN topup_status='PENDING_PROVIDER' THEN 1 ELSE 0 END) AS pending_provider,
            SUM(CASE WHEN topup_status='SUCCESS' THEN 1 ELSE 0 END) AS success_orders,
            SUM(CASE WHEN topup_status='FAILED' THEN 1 ELSE 0 END) AS failed_orders,
            SUM(CASE WHEN COALESCE(provider_retry_count, 0) > 0 AND COALESCE(topup_status, '') != 'FAILED' THEN 1 ELSE 0 END) AS retrying_provider
        FROM topup
        """
    )
    summary = summary_rows[0] if summary_rows else (0, 0, 0, 0, 0, 0, 0)

    recent_rows = await db_query(
        """
        SELECT id, phone, nominal, payment_status, topup_status,
               COALESCE(provider_retry_count, 0), provider_last_error, invoice_url, created_at
        FROM topup
        WHERE payment_status IN ('UNPAID', 'PAID')
           OR topup_status IN ('PROCESSING', 'PENDING_PROVIDER', 'FAILED')
           OR provider_last_error IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 12
        """
    )

    app_base_url = settings.app_base_url.rstrip("/")
    balance_response = await get_digiflazz_balance()
    balance_data = balance_response.get("data") if isinstance(balance_response, dict) else {}
    if not isinstance(balance_data, dict):
        balance_data = {}
    channel_response = await get_payment_channels()
    channel_data = channel_response.get("data") if isinstance(channel_response, dict) else []
    channel_count = len(channel_data) if isinstance(channel_data, list) else 0
    low_balance_threshold = _float_value(runtime_settings.get("digiflazz_low_balance_threshold") or 50000)
    digiflazz_deposit = _float_value(balance_data.get("deposit"))
    last_product_sync_rows = await db_query(
        "SELECT value, updated_at FROM site_settings WHERE key='provider.digiflazz_last_product_sync' LIMIT 1"
    )
    last_product_sync = {}
    if last_product_sync_rows:
        try:
            last_product_sync = json.loads(last_product_sync_rows[0][0] or "{}")
        except json.JSONDecodeError:
            last_product_sync = {"raw": last_product_sync_rows[0][0] or ""}
        last_product_sync["updated_at"] = _format_datetime(last_product_sync_rows[0][1])

    return {
        **provider_payload,
        "app_base_url": app_base_url,
        "app_env": settings.app_env,
        "callback_url": f"{app_base_url}/callback",
        "provider_live": {
            "digiflazz_balance": {
                "deposit": digiflazz_deposit,
                "message": balance_data.get("message") or "",
                "low_balance": bool(digiflazz_deposit and digiflazz_deposit < low_balance_threshold),
                "threshold": low_balance_threshold,
            },
            "tripay_channels": {
                "count": channel_count,
                "message": channel_response.get("message") if isinstance(channel_response, dict) else "",
            },
            "digiflazz_product_sync": last_product_sync,
        },
        "engine": {
            "poll_interval_seconds": settings.engine_poll_interval_seconds,
            "max_provider_retry": MAX_PROVIDER_RETRY,
            "status_label": "Aktif saat aplikasi berjalan",
        },
        "counts": {
            "payment_status": _count_map(payment_rows),
            "topup_status": _count_map(topup_rows),
        },
        "metrics": [
            {"label": "Total Order", "value": _int_value(summary[0])},
            {"label": "Belum Bayar", "value": _int_value(summary[1])},
            {"label": "Queue Engine", "value": _int_value(summary[2])},
            {"label": "Menunggu Provider", "value": _int_value(summary[3])},
            {"label": "Retry Provider", "value": _int_value(summary[6])},
            {"label": "Sukses", "value": _int_value(summary[4])},
            {"label": "Gagal", "value": _int_value(summary[5])},
        ],
        "flow": [
            {
                "title": "1. Order dibuat",
                "description": "Website membuat order dan meminta invoice ke Tripay.",
            },
            {
                "title": "2. Callback Tripay",
                "description": "Tripay mengirim status PAID lalu order masuk queue engine.",
            },
            {
                "title": "3. Engine polling",
                "description": "Engine membaca PROCESSING dan meneruskan transaksi ke Digiflazz.",
            },
            {
                "title": "4. Status Digiflazz",
                "description": "Engine/webhook memperbarui status SUCCESS, FAILED, atau retry provider.",
            },
        ],
        "recent_orders": [_monitor_order_dict(row) for row in recent_rows],
    }


@router.put("/api/provider-settings")
async def update_provider_settings(
    payload: ProviderSettingsUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if values.get("active_payment_provider") not in {None, "", "tripay"}:
        raise HTTPException(status_code=400, detail="Provider pembayaran belum didukung")
    if values.get("active_topup_provider") not in {None, "", "digiflazz"}:
        raise HTTPException(status_code=400, detail="Provider topup belum didukung")
    if values.get("backup_topup_provider") not in {None, "", "digiflazz"}:
        raise HTTPException(status_code=400, detail="Provider cadangan belum didukung")
    if any(_clean_text(values.get(key)) for key in SECRET_PROVIDER_KEYS):
        raise HTTPException(
            status_code=400,
            detail="Kredensial provider hanya boleh dikonfigurasi melalui environment variables",
        )

    await update_provider_runtime_settings(values, admin["username"])
    runtime_settings = await get_provider_runtime_settings()
    return {
        "success": True,
        "message": "Pengaturan API transaksi berhasil disimpan",
        "config": build_provider_admin_payload(runtime_settings),
    }


@router.get("/api/provider/digiflazz/balance")
async def admin_digiflazz_balance(_: Dict[str, Any] = Depends(_require_permission("api:monitor"))) -> Dict[str, Any]:
    response = await get_digiflazz_balance()
    return {"success": bool(response.get("data")), "response": response}


@router.post("/api/provider/digiflazz/deposit")
async def admin_digiflazz_deposit(
    payload: DigiflazzDepositRequest,
    admin: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    response = await create_digiflazz_deposit_ticket(payload.amount, payload.bank, payload.owner_name)
    await _write_audit_log(
        admin,
        action="provider.digiflazz_deposit",
        entity_type="provider",
        entity_id="digiflazz",
        before=None,
        after=response,
    )
    return {"success": True, "response": response}


@router.get("/api/provider/tripay/channels")
async def admin_tripay_channels(_: Dict[str, Any] = Depends(_require_permission("api:monitor"))) -> Dict[str, Any]:
    response = await get_payment_channels()
    return {"success": bool(response.get("success")), "response": response}


@router.get("/api/provider/tripay/transactions")
async def admin_tripay_transactions(
    reference: Optional[str] = Query(default=None),
    merchant_ref: Optional[str] = Query(default=None),
    method: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    _: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    response = await list_merchant_transactions(
        reference=reference,
        merchant_ref=merchant_ref,
        method=method,
        status=status,
        page=page,
        per_page=per_page,
    )
    return {"success": bool(response.get("success")), "response": response}


@router.post("/api/provider/tripay/ewallet/link")
async def admin_tripay_ewallet_link(
    payload: TripayEwalletRequest,
    admin: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    response = await link_ewallet(payload.wallet_type, payload.mobile_phone)
    await _write_audit_log(admin, action="provider.tripay_ewallet_link", entity_type="provider", entity_id=payload.mobile_phone, after=response)
    return {"success": bool(response.get("success")), "response": response}


@router.post("/api/provider/tripay/ewallet/unlink")
async def admin_tripay_ewallet_unlink(
    payload: TripayEwalletRequest,
    admin: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    response = await unlink_ewallet(payload.wallet_type, payload.mobile_phone)
    await _write_audit_log(admin, action="provider.tripay_ewallet_unlink", entity_type="provider", entity_id=payload.mobile_phone, after=response)
    return {"success": bool(response.get("success")), "response": response}


@router.post("/api/provider/tripay/ewallet/detail")
async def admin_tripay_ewallet_detail(
    payload: TripayEwalletRequest,
    _: Dict[str, Any] = Depends(_require_permission("api:monitor")),
) -> Dict[str, Any]:
    response = await get_ewallet_detail(payload.wallet_type, payload.mobile_phone)
    return {"success": bool(response.get("success")), "response": response}


@router.get("/api/admins")
async def get_admin_accounts(_: Dict[str, Any] = Depends(_require_permission("admins:manage"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        "SELECT id, username, role, permissions, active, created_at, updated_at FROM admin ORDER BY username"
    )
    return [_admin_dict(row) for row in rows]


@router.post("/api/admins")
async def create_admin_account(
    payload: AdminCreateRequest,
    _: Dict[str, Any] = Depends(_require_permission("admins:manage")),
) -> Dict[str, Any]:
    role = _normalize_role(payload.role)
    permissions = _normalize_permissions(role, payload.permissions)

    existing = await db_query("SELECT id FROM admin WHERE username=:username", {"username": payload.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username admin sudah digunakan")

    await db_execute(
        """
        INSERT INTO admin (username, password, role, permissions, active)
        VALUES (:username, :password, :role, :permissions, :active)
        """,
        {
            "username": payload.username.strip(),
            "password": hash_password(payload.password),
            "role": role,
            "permissions": json.dumps(permissions),
            "active": payload.active,
        },
    )
    return {"success": True, "message": "Admin berhasil dibuat"}


@router.put("/api/admins/{admin_id}")
async def update_admin_account(
    admin_id: int,
    payload: AdminUpdateRequest,
    current_admin: Dict[str, Any] = Depends(_require_permission("admins:manage")),
) -> Dict[str, Any]:
    existing = await db_query(
        "SELECT id, username, role, permissions, active, created_at, updated_at FROM admin WHERE id=:id",
        {"id": admin_id},
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")

    updates: list[str] = []
    values: Dict[str, Any] = {"id": admin_id}

    if payload.password is not None:
        updates.append("password=:password")
        values["password"] = hash_password(payload.password)

    if payload.role is not None or payload.permissions is not None:
        role = _normalize_role(payload.role or existing[0][2])
        permissions = _normalize_permissions(role, payload.permissions)
        updates.append("role=:role")
        updates.append("permissions=:permissions")
        values["role"] = role
        values["permissions"] = json.dumps(permissions)

    if payload.active is not None:
        if admin_id == current_admin["id"] and payload.active == 0:
            raise HTTPException(status_code=400, detail="Tidak bisa menonaktifkan akun sendiri")
        updates.append("active=:active")
        values["active"] = payload.active

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")

    await db_execute(
        f"UPDATE admin SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        values,
    )
    return {"success": True, "message": "Admin berhasil diperbarui"}


@router.delete("/api/admins/{admin_id}")
async def delete_admin_account(
    admin_id: int,
    current_admin: Dict[str, Any] = Depends(_require_permission("admins:manage")),
) -> Dict[str, Any]:
    if admin_id == current_admin["id"]:
        raise HTTPException(status_code=400, detail="Tidak bisa menghapus akun sendiri")
    await db_execute("DELETE FROM admin WHERE id=:id", {"id": admin_id})
    return {"success": True, "message": "Admin berhasil dihapus"}


@router.get("/api/site-settings")
async def get_site_settings(_: Dict[str, Any] = Depends(_require_permission("settings:manage"))) -> Dict[str, Any]:
    rows = await db_query("SELECT key, value FROM site_settings")
    settings_map = {row[0]: row[1] for row in rows}
    return {
        "site_name": settings_map.get("site_name") or settings.app_name,
        "logo_url": settings_map.get("logo_url") or "",
        "footer_text": settings_map.get("footer_text") or "",
    }


@router.put("/api/site-settings")
async def update_site_settings(
    payload: SiteSettingsUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("settings:manage")),
) -> Dict[str, Any]:
    values = {
        "site_name": payload.site_name,
        "logo_url": payload.logo_url,
        "footer_text": payload.footer_text,
    }
    for key, raw_value in values.items():
        if raw_value is None:
            continue
        value = raw_value.strip()
        await db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, :updated_by)
            ON CONFLICT(key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            {"key": key, "value": value, "updated_by": admin["username"]},
        )
    return {"success": True, "message": "Pengaturan website berhasil diperbarui"}


@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Form(default="logos"),
    _: Dict[str, Any] = Depends(_require_permission("settings:manage")),
) -> Dict[str, Any]:
    allowed_folders = {"logos", "content", "promos", "products"}
    folder_name = folder if folder in allowed_folders else "logos"
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Format file tidak didukung")

    content = await file.read()
    max_size_bytes = 5 * 1024 * 1024
    if len(content) > max_size_bytes:
        raise HTTPException(status_code=400, detail="Ukuran file maksimal 5MB")

    upload_dir = Path("web") / "uploads" / "admin" / folder_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    file_path = upload_dir / filename
    file_path.write_bytes(content)
    return {"url": _build_static_url(str(file_path))}


def _page_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "id": row[0],
        "slug": row[1],
        "title": row[2],
        "excerpt": row[3] or "",
        "content": row[4] or "",
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
        "active": bool(row[17]),
        "show_on_website": bool(row[18]),
        "created_by": row[19] or "",
        "updated_by": row[20] or "",
        "created_at": _format_datetime(row[21]),
        "updated_at": _format_datetime(row[22]),
    }


@router.get("/api/pages")
async def get_site_pages(_: Dict[str, Any] = Depends(_require_permission("content:manage"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, slug, title, excerpt, content, image_url,
               page_type, badge, cta_text, cta_url, secondary_cta_text, secondary_cta_url,
               promo_code, highlight_title, highlight_items, terms_text, accent_color,
               active, show_on_website, created_by, updated_by, created_at, updated_at
        FROM site_pages
        ORDER BY updated_at DESC
        """
    )
    return [_page_dict(row) for row in rows]


@router.post("/api/pages")
async def create_site_page(
    payload: SitePageCreateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("content:manage")),
) -> Dict[str, Any]:
    slug = _clean_slug(payload.slug)
    if not slug:
        raise HTTPException(status_code=400, detail="Slug halaman tidak valid")

    await db_execute(
        """
        INSERT INTO site_pages (
            slug, title, excerpt, content, image_url, page_type, badge, cta_text, cta_url,
            secondary_cta_text, secondary_cta_url, promo_code, highlight_title, highlight_items,
            terms_text, accent_color, active, show_on_website, created_by, updated_by
        )
        VALUES (
            :slug, :title, :excerpt, :content, :image_url, :page_type, :badge, :cta_text, :cta_url,
            :secondary_cta_text, :secondary_cta_url, :promo_code, :highlight_title, :highlight_items,
            :terms_text, :accent_color, :active, :show_on_website, :created_by, :updated_by
        )
        """,
        {
            "slug": slug,
            "title": payload.title.strip(),
            "excerpt": _clean_text(payload.excerpt),
            "content": payload.content or "",
            "image_url": _clean_text(payload.image_url),
            "page_type": _clean_page_type(payload.page_type),
            "badge": _clean_text(payload.badge),
            "cta_text": _clean_text(payload.cta_text),
            "cta_url": _clean_text(payload.cta_url),
            "secondary_cta_text": _clean_text(payload.secondary_cta_text),
            "secondary_cta_url": _clean_text(payload.secondary_cta_url),
            "promo_code": _clean_text(payload.promo_code),
            "highlight_title": _clean_text(payload.highlight_title),
            "highlight_items": payload.highlight_items or "",
            "terms_text": payload.terms_text or "",
            "accent_color": _clean_accent_color(payload.accent_color),
            "active": payload.active,
            "show_on_website": payload.show_on_website,
            "created_by": admin["username"],
            "updated_by": admin["username"],
        },
    )
    return {"success": True, "message": "Halaman berhasil dibuat", "slug": slug}


@router.put("/api/pages/{page_id}")
async def update_site_page(
    page_id: int,
    payload: SitePageUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("content:manage")),
) -> Dict[str, Any]:
    existing = await db_query("SELECT id FROM site_pages WHERE id=:id", {"id": page_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Halaman tidak ditemukan")

    updates: list[str] = []
    values: Dict[str, Any] = {"id": page_id, "updated_by": admin["username"]}
    field_values = {
        "slug": _clean_slug(payload.slug) if payload.slug is not None else None,
        "title": payload.title.strip() if payload.title is not None else None,
        "excerpt": _clean_text(payload.excerpt) if payload.excerpt is not None else None,
        "content": payload.content if payload.content is not None else None,
        "image_url": _clean_text(payload.image_url) if payload.image_url is not None else None,
        "page_type": _clean_page_type(payload.page_type) if payload.page_type is not None else None,
        "badge": _clean_text(payload.badge) if payload.badge is not None else None,
        "cta_text": _clean_text(payload.cta_text) if payload.cta_text is not None else None,
        "cta_url": _clean_text(payload.cta_url) if payload.cta_url is not None else None,
        "secondary_cta_text": _clean_text(payload.secondary_cta_text) if payload.secondary_cta_text is not None else None,
        "secondary_cta_url": _clean_text(payload.secondary_cta_url) if payload.secondary_cta_url is not None else None,
        "promo_code": _clean_text(payload.promo_code) if payload.promo_code is not None else None,
        "highlight_title": _clean_text(payload.highlight_title) if payload.highlight_title is not None else None,
        "highlight_items": payload.highlight_items if payload.highlight_items is not None else None,
        "terms_text": payload.terms_text if payload.terms_text is not None else None,
        "accent_color": _clean_accent_color(payload.accent_color) if payload.accent_color is not None else None,
        "active": payload.active,
        "show_on_website": payload.show_on_website,
    }

    for column, value in field_values.items():
        if value is None and getattr(payload, column, None) is None:
            continue
        if column == "slug" and not value:
            raise HTTPException(status_code=400, detail="Slug halaman tidak valid")
        updates.append(f"{column}=:{column}")
        values[column] = value

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")

    await db_execute(
        f"UPDATE site_pages SET {', '.join(updates)}, updated_by=:updated_by, updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        values,
    )
    return {"success": True, "message": "Halaman berhasil diperbarui"}


@router.delete("/api/pages/{page_id}")
async def delete_site_page(page_id: int, _: Dict[str, Any] = Depends(_require_permission("content:manage"))) -> Dict[str, Any]:
    await db_execute("DELETE FROM site_pages WHERE id=:id", {"id": page_id})
    return {"success": True, "message": "Halaman berhasil dihapus"}


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(payload: AdminLoginRequest) -> Dict[str, Any]:
    await _ensure_default_admin()
    row = await db_query(
        "SELECT id, password, role, permissions, active FROM admin WHERE username=:username",
        {"username": payload.username},
    )
    if not row:
        raise HTTPException(status_code=401, detail="Kredensial tidak valid")

    admin_id, password_hash, role, raw_permissions, active = row[0]
    if not active:
        raise HTTPException(status_code=403, detail="Akun admin nonaktif")

    if not verify_password(payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Kredensial tidak valid")

    normalized_role = _normalize_role(role)
    permissions = _parse_permissions(raw_permissions, normalized_role)
    token = create_access_token(
        {
            "admin_id": admin_id,
            "sub": payload.username,
            "role": normalized_role,
            "permissions": permissions,
        }
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.jwt_access_token_expire_minutes,
        "username": payload.username,
        "role": normalized_role,
        "permissions": permissions,
        "token": token,
        "message": "Login berhasil",
    }


@router.get("/api/products")
async def get_admin_products(_: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT
            p.sku,
            p.provider,
            p.name,
            p.cost_price,
            p.price,
            p.active,
            p.category,
            p.description,
            p.image_url,
            p.logo_url,
            p.promo_title,
            p.promo_text,
            p.promo_badge,
            p.promo_url,
            COALESCE(p.display_order, 0),
            COALESCE(p.product_type, 'prepaid'),
            p.brand,
            p.provider_type,
            COALESCE(p.buyer_product_status, 1),
            COALESCE(p.seller_product_status, 1),
            COALESCE(p.stock, 0),
            COALESCE(p.unlimited_stock, 0),
            COALESCE(p.multi, 0),
            p.start_cut_off,
            p.end_cut_off,
            COALESCE(p.admin_fee, 0),
            COALESCE(p.commission, 0),
            p.provider_description,
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
        ORDER BY COALESCE(p.category, ''), COALESCE(p.provider, ''), COALESCE(p.display_order, 0), p.name
        """
    )

    grouped: Dict[str, Any] = {}
    for row in rows:
        (
            sku,
            provider,
            name,
            cost_price,
            price,
            active,
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
            brand,
            provider_type,
            buyer_product_status,
            seller_product_status,
            stock,
            unlimited_stock,
            multi,
            start_cut_off,
            end_cut_off,
            admin_fee,
            commission,
            provider_description,
            order_count,
            success_count,
        ) = row
        cat_name = category or "Lainnya"
        provider_name = provider or "Lainnya"
        grouped.setdefault(cat_name, {}).setdefault(provider_name, []).append(
            {
                "sku": sku,
                "provider": provider_name,
                "name": name,
                "cost": float(cost_price or 0),
                "cost_price": float(cost_price or 0),
                "price": float(price or 0),
                "profit": round(float(price or 0) - float(cost_price or 0), 2),
                "active": bool(active),
                "category": cat_name,
                "description": description or "",
                "image_url": image_url or "",
                "logo_url": logo_url or "",
                "promo_title": promo_title or "",
                "promo_text": promo_text or "",
                "promo_badge": promo_badge or "",
                "promo_url": promo_url or "",
                "display_order": _int_value(display_order),
                "product_type": product_type or "prepaid",
                "brand": brand or "",
                "provider_type": provider_type or "",
                "buyer_product_status": bool(buyer_product_status),
                "seller_product_status": bool(seller_product_status),
                "stock": _int_value(stock),
                "unlimited_stock": bool(unlimited_stock),
                "multi": bool(multi),
                "start_cut_off": start_cut_off or "",
                "end_cut_off": end_cut_off or "",
                "admin_fee": float(admin_fee or 0),
                "commission": float(commission or 0),
                "provider_description": provider_description or "",
                "order_count": _int_value(order_count),
                "success_count": _int_value(success_count),
            }
        )

    return grouped


@router.post("/api/products")
async def create_product(payload: ProductCreateRequest, _: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    provider = payload.provider.strip()
    name = payload.name.strip()
    sku = (payload.sku or f"{provider}-{name}").strip()
    cost_price = payload.cost_price if payload.cost_price is not None else payload.cost
    price = payload.price
    category = payload.category.strip() or "Lainnya"
    active = payload.active

    await db_execute(
        """
        INSERT INTO products (
            sku, provider, name, cost_price, price, active, category,
            description, image_url, logo_url, promo_title, promo_text,
            promo_badge, promo_url, display_order
        )
        VALUES (
            :sku, :provider, :name, :cost_price, :price, :active, :category,
            :description, :image_url, :logo_url, :promo_title, :promo_text,
            :promo_badge, :promo_url, :display_order
        )
        ON CONFLICT(sku) DO UPDATE SET
            provider = EXCLUDED.provider,
            name = EXCLUDED.name,
            cost_price = EXCLUDED.cost_price,
            price = EXCLUDED.price,
            active = EXCLUDED.active,
            category = EXCLUDED.category,
            description = EXCLUDED.description,
            image_url = EXCLUDED.image_url,
            logo_url = EXCLUDED.logo_url,
            promo_title = EXCLUDED.promo_title,
            promo_text = EXCLUDED.promo_text,
            promo_badge = EXCLUDED.promo_badge,
            promo_url = EXCLUDED.promo_url,
            display_order = EXCLUDED.display_order
        """,
        {
            "sku": sku,
            "provider": provider,
            "name": name,
            "cost_price": cost_price,
            "price": price,
            "active": active,
            "category": category,
            "description": _clean_text(payload.description),
            "image_url": _clean_text(payload.image_url),
            "logo_url": _clean_text(payload.logo_url),
            "promo_title": _clean_text(payload.promo_title),
            "promo_text": _clean_text(payload.promo_text),
            "promo_badge": _clean_text(payload.promo_badge),
            "promo_url": _clean_text(payload.promo_url),
            "display_order": payload.display_order,
        },
    )
    return {"success": True, "message": "Produk berhasil disimpan", "sku": sku}


@router.put("/api/products/{sku}")
async def update_product(sku: str, payload: ProductUpdateRequest, _: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    cost_price = payload.cost_price if payload.cost_price is not None else payload.cost
    price = payload.price

    updates: list[str] = []
    values: Dict[str, Any] = {"sku": sku}

    if payload.provider is not None:
        provider = _clean_text(payload.provider)
        if not provider:
            raise HTTPException(status_code=400, detail="Provider produk wajib diisi")
        updates.append("provider=:provider")
        values["provider"] = provider

    if payload.name is not None:
        name = _clean_text(payload.name)
        if not name:
            raise HTTPException(status_code=400, detail="Nama produk wajib diisi")
        updates.append("name=:name")
        values["name"] = name

    if cost_price is not None:
        updates.append("cost_price=:cost_price")
        values["cost_price"] = cost_price
    if price is not None:
        updates.append("price=:price")
        values["price"] = price
    if payload.category is not None:
        updates.append("category=:category")
        values["category"] = _clean_text(payload.category) or "Lainnya"
    if payload.active is not None:
        updates.append("active=:active")
        values["active"] = payload.active
    for field in [
        "description",
        "image_url",
        "logo_url",
        "promo_title",
        "promo_text",
        "promo_badge",
        "promo_url",
    ]:
        if getattr(payload, field) is not None:
            updates.append(f"{field}=:{field}")
            values[field] = _clean_text(getattr(payload, field))
    if payload.display_order is not None:
        updates.append("display_order=:display_order")
        values["display_order"] = payload.display_order

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")

    existing_price_rows = await db_query(
        "SELECT cost_price, price FROM products WHERE sku=:sku",
        {"sku": sku},
    )
    if not existing_price_rows:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")
    effective_cost = _float_value(values.get("cost_price", existing_price_rows[0][0]))
    effective_price = _float_value(values.get("price", existing_price_rows[0][1]))
    if effective_price < effective_cost:
        raise HTTPException(status_code=400, detail="Harga jual tidak boleh lebih rendah dari harga modal")

    await db_execute(f"UPDATE products SET {', '.join(updates)} WHERE sku=:sku", values)
    return {"success": True, "message": "Produk berhasil diperbarui"}


@router.put("/api/products/{sku}/toggle")
async def toggle_product(sku: str, _: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    row = await db_query("SELECT active FROM products WHERE sku=:sku", {"sku": sku})
    if not row:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

    new_active = 0 if bool(row[0][0]) else 1
    await db_execute("UPDATE products SET active=:active WHERE sku=:sku", {"active": new_active, "sku": sku})
    return {"success": True, "active": bool(new_active)}


@router.delete("/api/products/{sku}")
async def delete_product(sku: str, _: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    await db_execute("DELETE FROM products WHERE sku=:sku", {"sku": sku})
    return {"success": True, "message": "Produk berhasil dihapus"}


@router.get("/api/orders")
async def get_orders(
    q: Optional[str] = Query(default=None),
    payment_status: Optional[str] = Query(default=None),
    topup_status: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: Dict[str, Any] = Depends(_require_permission("orders:view")),
) -> list[Dict[str, Any]]:
    return await _fetch_orders(
        q=q,
        payment_status=payment_status,
        topup_status=topup_status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get("/api/orders/{order_id}")
async def get_order_detail(order_id: str, _: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    order = await _fetch_order_detail(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    return order


@router.post("/api/orders/{order_id}/sync-tripay")
async def sync_order_tripay(
    order_id: str,
    admin: Dict[str, Any] = Depends(_require_permission("orders:manage")),
) -> Dict[str, Any]:
    before = await _fetch_order_detail(order_id)
    if not before:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    if str(before.get("topup_status") or "").upper() in {"SUCCESS", "FAILED"}:
        raise HTTPException(status_code=409, detail="Order terminal tidak dapat disinkronkan ulang ke Tripay")
    reference = before.get("payment_reference")
    if not reference:
        raise HTTPException(status_code=400, detail="Order belum memiliki reference Tripay")

    response = await get_transaction_detail(reference)
    if not isinstance(response, dict) or not response.get("success"):
        response = await check_transaction_status(reference)
    tripay_data = response.get("data") if isinstance(response, dict) else {}
    if not isinstance(tripay_data, dict):
        tripay_data = {}
    status = str(tripay_data.get("status") or before.get("payment_status") or "").upper()
    expired_value = tripay_data.get("expired_time") or tripay_data.get("expired_at")
    values = {
        "id": order_id,
        "payment_status": status if status in PAYMENT_STATUSES else before.get("payment_status"),
        "payment_reference": tripay_data.get("reference") or reference,
        "payment_name": tripay_data.get("payment_name") or tripay_data.get("payment_method") or "",
        "pay_code": str(tripay_data.get("pay_code") or ""),
        "pay_url": tripay_data.get("pay_url") or "",
        "qr_url": tripay_data.get("qr_url") or "",
        "qr_string": tripay_data.get("qr_string") or "",
        "invoice_url": tripay_data.get("checkout_url") or tripay_data.get("pay_url") or "",
        "payment_expired_at": _timestamp_from_unix(expired_value) if expired_value else None,
        "tripay_payload": _json_dump(tripay_data or response),
    }
    updated = await db_execute_rowcount(
        """
        UPDATE topup
        SET payment_status=:payment_status,
            payment_reference=:payment_reference,
            payment_name=COALESCE(NULLIF(:payment_name, ''), payment_name),
            pay_code=COALESCE(NULLIF(:pay_code, ''), pay_code),
            pay_url=COALESCE(NULLIF(:pay_url, ''), pay_url),
            qr_url=COALESCE(NULLIF(:qr_url, ''), qr_url),
            qr_string=COALESCE(NULLIF(:qr_string, ''), qr_string),
            invoice_url=COALESCE(NULLIF(:invoice_url, ''), invoice_url),
            payment_expired_at=COALESCE(:payment_expired_at, payment_expired_at),
            topup_status=CASE
                WHEN :payment_status='PAID' AND topup_status='PENDING_PAYMENT' THEN 'PROCESSING'
                WHEN :payment_status IN ('EXPIRED', 'FAILED') THEN 'FAILED'
                ELSE topup_status
            END,
            tripay_payload=:tripay_payload,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
          AND COALESCE(topup_status, '') NOT IN ('SUCCESS', 'FAILED')
        """,
        values,
    )
    after = await _fetch_order_detail(order_id)
    if not updated:
        raise HTTPException(status_code=409, detail="Order berubah menjadi terminal saat sinkronisasi Tripay")
    await _write_audit_log(admin, action="order.sync_tripay", entity_type="topup", entity_id=order_id, before=before, after={"order": after, "tripay": response})
    return {"success": True, "order": after, "tripay": response}


@router.post("/api/orders/{order_id}/sync-provider")
async def sync_order_provider(
    order_id: str,
    admin: Dict[str, Any] = Depends(_require_permission("orders:manage")),
) -> Dict[str, Any]:
    before = await _fetch_order_detail(order_id)
    if not before:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    if before.get("payment_status") != "PAID":
        raise HTTPException(status_code=400, detail="Order belum dibayar")
    if str(before.get("topup_status") or "").upper() in {"SUCCESS", "FAILED"}:
        raise HTTPException(status_code=409, detail="Order terminal tidak dapat disinkronkan ulang ke provider")

    provider_rows = await db_query(
        "SELECT COALESCE(provider_outcome, 'NOT_SENT') FROM topup WHERE id=:id",
        {"id": order_id},
    )
    provider_outcome = str(provider_rows[0][0] or "NOT_SENT").upper() if provider_rows else "NOT_SENT"
    if provider_outcome not in {"SENT_UNKNOWN", "PENDING_PROVIDER"}:
        raise HTTPException(status_code=409, detail="Order belum memiliki outcome provider yang aman untuk disinkronkan")
    if not _can_reconcile_digiflazz_status(before.get("order_type"), before.get("created_at")):
        raise HTTPException(
            status_code=409,
            detail="Cek status Digiflazz prabayar diblokir setelah 90 hari untuk mencegah transaksi baru",
        )
    lease_id = await claim_provider_status_reconciliation(order_id)
    if not lease_id:
        raise HTTPException(
            status_code=409,
            detail="Status provider baru saja diperiksa; tunggu interval rekonsiliasi sebelum mencoba lagi",
        )

    try:
        if str(before.get("order_type") or "PREPAID").upper() == "POSTPAID":
            response = await cek_status_postpaid(before["nominal"], before["target_id"], order_id)
        else:
            response = await cek_status_digiflazz(before["nominal"], before["target_id"], order_id)
    except Exception:
        await release_provider_status_reconciliation(order_id, lease_id)
        raise
    data = digiflazz_response_data(response)
    classification = classify_digiflazz_transaction_response(response)
    if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
        next_status, next_outcome, provider_error = "SUCCESS", "SUCCESS", None
    elif classification == DIGIFLAZZ_OUTCOME_PENDING:
        next_status, next_outcome, provider_error = "PENDING_PROVIDER", "PENDING_PROVIDER", None
    elif classification == DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE:
        next_status, next_outcome = "FAILED", "FAILED"
        provider_error = data.get("message") or "Provider menyatakan transaksi gagal"
    else:
        updated = await db_execute_rowcount(
            """
            UPDATE topup
            SET provider_last_check_at=CURRENT_TIMESTAMP,
                provider_claim_id=NULL,
                provider_claimed_at=NULL,
                provider_claim_expires_at=NULL,
                provider_payload=:provider_payload,
                status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
              AND provider_claim_id=:lease_id
              AND provider_claim_expires_at > CURRENT_TIMESTAMP
              AND payment_status='PAID'
              AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
              AND COALESCE(provider_outcome, '') IN ('SENT_UNKNOWN', 'PENDING_PROVIDER')
            """,
            {
                "id": order_id,
                "lease_id": lease_id,
                "provider_payload": _json_dump(response),
            },
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="Order berubah atau lease rekonsiliasi tidak lagi dimiliki; hasil lookup lama diabaikan",
            )
        after = await _fetch_order_detail(order_id)
        await _write_audit_log(admin, action="order.sync_provider", entity_type="topup", entity_id=order_id, before=before, after={"order": after, "provider": response})
        return {"success": True, "order": after, "provider": response, "reconciled": False}

    updated = await db_execute_rowcount(
        """
        UPDATE topup
        SET topup_status=:topup_status,
            provider_outcome=:provider_outcome,
            provider_last_error=:provider_last_error,
            sn=COALESCE(:sn, sn),
            note=COALESCE(:note, note),
            provider_rc=:provider_rc,
            provider_price=:provider_price,
            provider_selling_price=:provider_selling_price,
            provider_last_balance=:provider_last_balance,
            provider_last_check_at=CURRENT_TIMESTAMP,
            provider_claim_id=NULL,
            provider_claimed_at=NULL,
            provider_claim_expires_at=NULL,
            provider_payload=:provider_payload,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
          AND provider_claim_id=:lease_id
          AND provider_claim_expires_at > CURRENT_TIMESTAMP
          AND payment_status='PAID'
          AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
          AND COALESCE(provider_outcome, '') IN ('SENT_UNKNOWN', 'PENDING_PROVIDER')
        """,
        {
            "id": order_id,
            "lease_id": lease_id,
            "topup_status": next_status,
            "provider_outcome": next_outcome,
            "provider_last_error": provider_error,
            "sn": data.get("sn"),
            "note": data.get("message"),
            "provider_rc": data.get("rc") or "",
            "provider_price": _float_value(data.get("price")),
            "provider_selling_price": _float_value(data.get("selling_price")),
            "provider_last_balance": _float_value(data.get("buyer_last_saldo")),
            "provider_payload": _json_dump(response),
        },
    )
    if not updated:
        raise HTTPException(
            status_code=409,
            detail="Order berubah atau lease rekonsiliasi tidak lagi dimiliki; hasil lookup lama diabaikan",
        )
    if next_outcome == "SUCCESS":
        await finalize_order_promotions(order_id)
    elif next_outcome == "FAILED":
        await release_order_promotions(order_id, include_redeemed=True)
    after = await _fetch_order_detail(order_id)
    await _write_audit_log(admin, action="order.sync_provider", entity_type="topup", entity_id=order_id, before=before, after={"order": after, "provider": response})
    return {"success": True, "order": after, "provider": response}


@router.post("/api/orders/{order_id}/retry")
async def retry_order(
    order_id: str,
    payload: Optional[OrderRetryRequest] = Body(default=None),
    admin: Dict[str, Any] = Depends(_require_permission("orders:manage")),
) -> Dict[str, Any]:
    before = await _fetch_order_detail(order_id)
    if not before:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    if str(before.get("refund_status") or "").upper() == "PAYMENT_RECEIVED_AFTER_CANCEL":
        raise HTTPException(
            status_code=409,
            detail="Pembayaran diterima setelah pembatalan; fulfillment diblokir sampai review refund manual selesai",
        )
    if before["payment_status"] != "PAID":
        raise HTTPException(status_code=400, detail="Order belum dibayar, tidak bisa retry provider")
    current_topup_status = str(before.get("topup_status") or "").upper()
    if current_topup_status == "SUCCESS":
        raise HTTPException(status_code=409, detail="Order SUCCESS tidak boleh dikirim ulang ke provider")

    provider_rows = await db_query(
        "SELECT COALESCE(provider_outcome, 'NOT_SENT'), COALESCE(order_type, 'PREPAID'), nominal, target_id FROM topup WHERE id=:id",
        {"id": order_id},
    )
    provider_outcome = str(provider_rows[0][0] or "NOT_SENT").upper() if provider_rows else "NOT_SENT"
    if provider_outcome == "SENT_UNKNOWN":
        order_type = str(provider_rows[0][1] or "PREPAID").upper()
        if not _can_reconcile_digiflazz_status(order_type, before.get("created_at")):
            raise HTTPException(
                status_code=409,
                detail="Cek status Digiflazz prabayar diblokir setelah 90 hari untuk mencegah transaksi baru",
            )
        lease_id = await claim_provider_status_reconciliation(order_id)
        if not lease_id:
            raise HTTPException(
                status_code=409,
                detail="Status provider baru saja diperiksa atau masih dikunci oleh rekonsiliasi lain",
            )
        try:
            if order_type == "POSTPAID":
                provider_response = await cek_status_postpaid(provider_rows[0][2], provider_rows[0][3], order_id)
            else:
                provider_response = await cek_status_digiflazz(provider_rows[0][2], provider_rows[0][3], order_id)
        except Exception:
            await release_provider_status_reconciliation(order_id, lease_id)
            raise
        provider_data = digiflazz_response_data(provider_response)
        classification = classify_digiflazz_transaction_response(provider_response)
        if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
            reconciled_status, reconciled_outcome, reconciled_error = "SUCCESS", "SUCCESS", None
        elif classification == DIGIFLAZZ_OUTCOME_PENDING:
            reconciled_status, reconciled_outcome, reconciled_error = "PENDING_PROVIDER", "PENDING_PROVIDER", None
        elif classification == DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE:
            reconciled_status, reconciled_outcome = "FAILED", "FAILED"
            reconciled_error = provider_data.get("message") or "Provider menyatakan transaksi gagal"
        else:
            updated = await db_execute_rowcount(
                """
                UPDATE topup
                SET provider_last_check_at=CURRENT_TIMESTAMP,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    provider_payload=:payload,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND provider_claim_id=:lease_id
                  AND provider_claim_expires_at > CURRENT_TIMESTAMP
                  AND payment_status='PAID'
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                  AND COALESCE(provider_outcome, '')='SENT_UNKNOWN'
                """,
                {
                    "id": order_id,
                    "lease_id": lease_id,
                    "payload": _json_dump(provider_response),
                },
            )
            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail="Order berubah atau lease rekonsiliasi tidak lagi dimiliki; hasil lookup lama diabaikan",
                )
            after = await _fetch_order_detail(order_id)
            await _write_audit_log(
                admin,
                action="order.retry.reconcile_unknown",
                entity_type="topup",
                entity_id=order_id,
                before=before,
                after={"order": after, "provider": provider_response, "reconciled": False},
            )
            raise HTTPException(
                status_code=409,
                detail="Status provider belum dapat dipastikan. Retry external diblokir untuk mencegah duplicate transaksi.",
            )

        updated = await db_execute_rowcount(
            """
            UPDATE topup
            SET topup_status=:topup_status,
                provider_outcome=:provider_outcome,
                provider_last_error=:provider_last_error,
                provider_rc=:provider_rc,
                provider_price=:provider_price,
                provider_selling_price=:provider_selling_price,
                provider_last_balance=:provider_last_balance,
                provider_last_check_at=CURRENT_TIMESTAMP,
                provider_payload=:provider_payload,
                provider_claim_id=NULL,
                provider_claimed_at=NULL,
                provider_claim_expires_at=NULL,
                status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
              AND provider_claim_id=:lease_id
              AND provider_claim_expires_at > CURRENT_TIMESTAMP
              AND payment_status='PAID'
              AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
              AND COALESCE(provider_outcome, '')='SENT_UNKNOWN'
            """,
            {
                "id": order_id,
                "lease_id": lease_id,
                "topup_status": reconciled_status,
                "provider_outcome": reconciled_outcome,
                "provider_last_error": reconciled_error,
                "provider_rc": provider_data.get("rc") or "",
                "provider_price": _float_value(provider_data.get("price")),
                "provider_selling_price": _float_value(provider_data.get("selling_price")),
                "provider_last_balance": _float_value(provider_data.get("buyer_last_saldo")),
                "provider_payload": _json_dump(provider_response),
            },
        )
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="Order berubah atau lease rekonsiliasi tidak lagi dimiliki; hasil lookup lama diabaikan",
            )
        if reconciled_outcome == "SUCCESS":
            await finalize_order_promotions(order_id)
        elif reconciled_outcome == "FAILED":
            await release_order_promotions(order_id, include_redeemed=True)
        after = await _fetch_order_detail(order_id)
        await _write_audit_log(
            admin,
            action="order.retry.reconcile_unknown",
            entity_type="topup",
            entity_id=order_id,
            before=before,
            after={"order": after, "provider": provider_response, "reconciled": True},
        )
        raise HTTPException(
            status_code=409,
            detail="Outcome provider telah direkonsiliasi. Kirim retry baru hanya setelah meninjau status terbaru.",
        )

    if current_topup_status in {"PROCESSING", "PENDING_PROVIDER"}:
        raise HTTPException(status_code=409, detail="Order masih diproses, tunggu hasil provider sebelum retry")

    retry_reason = _clean_text(payload.reason if payload else None) or "Retry manual dari dashboard admin"

    updated = await db_execute_rowcount(
        """
        UPDATE topup
        SET topup_status='PROCESSING',
            provider_outcome='NOT_SENT',
            provider_retry_count=0,
            provider_last_error=NULL,
            provider_claim_id=NULL,
            provider_claimed_at=NULL,
            provider_claim_expires_at=NULL,
            note=:note,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id AND topup_status='FAILED' AND COALESCE(provider_outcome, 'NOT_SENT')='NOT_SENT'
        """,
        {"id": order_id, "note": retry_reason},
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Status order berubah; refresh dan tinjau hasil provider sebelum retry")
    after = await _fetch_order_detail(order_id)
    await _write_audit_log(
        admin,
        action="order.retry",
        entity_type="topup",
        entity_id=order_id,
        before=before,
        after={**(after or {}), "retry_reason": retry_reason},
    )
    return {"success": True, "message": "Order dimasukkan ulang ke queue engine", "order": after}


@router.put("/api/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    payload: OrderStatusUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("orders:manage")),
) -> Dict[str, Any]:
    before = await _fetch_order_detail(order_id)
    if not before:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")

    updates: list[str] = []
    values: Dict[str, Any] = {"id": order_id}

    payment_status = _clean_text(payload.payment_status)
    if payment_status:
        payment_status = payment_status.upper()
        if payment_status not in PAYMENT_STATUSES:
            raise HTTPException(status_code=400, detail="Payment status tidak valid")
        updates.append("payment_status=:payment_status")
        values["payment_status"] = payment_status

    topup_status = _clean_text(payload.topup_status)
    if topup_status:
        topup_status = topup_status.upper()
        if topup_status not in TOPUP_STATUSES:
            raise HTTPException(status_code=400, detail="Topup status tidak valid")
        updates.append("topup_status=:topup_status")
        values["topup_status"] = topup_status

    if payload.sn is not None:
        updates.append("sn=:sn")
        values["sn"] = _clean_text(payload.sn)

    if payload.note is not None:
        updates.append("note=:note")
        values["note"] = _clean_text(payload.note)

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada status yang diperbarui")

    reason = _clean_text(payload.note)
    _validate_admin_order_transition(before, payment_status=payment_status, topup_status=topup_status, reason=reason)

    await db_execute(
        f"UPDATE topup SET {', '.join(updates)}, status_updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        values,
    )
    after = await _fetch_order_detail(order_id)
    await _write_audit_log(
        admin,
        action="order.status_update",
        entity_type="topup",
        entity_id=order_id,
        before=before,
        after={**(after or {}), "reason": reason},
    )
    return {"success": True, "message": "Status order berhasil diperbarui", "order": after}


@router.post("/api/orders/{order_id}/refund")
async def refund_order(
    order_id: str,
    payload: OrderRefundRequest,
    admin: Dict[str, Any] = Depends(_require_permission("orders:manage")),
) -> Dict[str, Any]:
    before = await _fetch_order_detail(order_id)
    if not before:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
    late_payment_after_cancel = (
        str(before.get("refund_status") or "").upper() == "PAYMENT_RECEIVED_AFTER_CANCEL"
    )
    if before["payment_status"] not in {"PAID", "REFUNDED"} and not late_payment_after_cancel:
        raise HTTPException(
            status_code=400,
            detail="Hanya order berstatus PAID atau pembayaran setelah pembatalan yang bisa ditandai refund",
        )

    note = _clean_text(payload.note) or "Refund manual dari dashboard admin"
    refund_rows = await db_query(
        "SELECT customer_id, payment_method, amount FROM topup WHERE id=:id",
        {"id": order_id},
    )
    await db_execute(
        """
        UPDATE topup
        SET payment_status='REFUNDED',
            refund_status='REFUNDED',
            refund_note=:note,
            refunded_at=CURRENT_TIMESTAMP,
            topup_status=CASE
                WHEN topup_status='SUCCESS' THEN topup_status
                ELSE 'FAILED'
            END,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
        """,
        {"id": order_id, "note": note},
    )
    if before["payment_status"] != "REFUNDED" and refund_rows:
        customer_id, payment_method, amount = refund_rows[0]
        if customer_id and str(payment_method or "").upper() == "WALLET":
            await _wallet_add_entry(
                customer_id=int(customer_id),
                entry_type="CREDIT",
                amount=_float_value(amount),
                reference_type="refund",
                reference_id=order_id,
                idempotency_key=f"refund:{order_id}",
                actor=admin.get("username") or "admin",
                note=note,
            )
    after = await _fetch_order_detail(order_id)
    await _write_audit_log(admin, action="order.refund", entity_type="topup", entity_id=order_id, before=before, after=after)
    return {"success": True, "message": "Order ditandai refund", "order": after}


@router.get("/api/dashboard-summary")
async def dashboard_summary(_: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    today = _today_wib()
    today_orders = await _fetch_orders(date_from=today, date_to=today, limit=5000)
    total_orders = await _fetch_orders(limit=5000)
    operational_rows = await db_query(
        """
        SELECT
            SUM(CASE WHEN payment_status='UNPAID' THEN 1 ELSE 0 END) AS waiting_payment,
            SUM(CASE WHEN payment_status='PAID' AND COALESCE(topup_status, '') IN ('', 'PROCESSING') THEN 1 ELSE 0 END) AS processing_orders,
            SUM(CASE WHEN topup_status='PENDING_PROVIDER' THEN 1 ELSE 0 END) AS pending_provider,
            SUM(CASE WHEN topup_status='FAILED' THEN 1 ELSE 0 END) AS failed_orders,
            SUM(CASE WHEN COALESCE(provider_retry_count, 0) > 0 THEN 1 ELSE 0 END) AS retried_orders
        FROM topup
        """
    )
    operational = operational_rows[0] if operational_rows else (0, 0, 0, 0, 0)
    support_rows = await db_query(
        """
        SELECT
            SUM(CASE WHEN status IN ('OPEN', 'IN_PROGRESS') THEN 1 ELSE 0 END),
            SUM(CASE WHEN priority IN ('HIGH', 'URGENT') AND status NOT IN ('RESOLVED', 'CLOSED') THEN 1 ELSE 0 END)
        FROM support_tickets
        """
    )
    support = support_rows[0] if support_rows else (0, 0)
    asset_rows = await db_query(
        """
        SELECT
            (SELECT COUNT(*) FROM products WHERE active=1) AS active_products,
            (SELECT COUNT(DISTINCT provider) FROM products WHERE active=1) AS active_providers,
            (SELECT COUNT(*) FROM customer_accounts WHERE active=1) AS active_customers,
            (SELECT COALESCE(SUM(balance), 0) FROM wallet_accounts) AS wallet_liability
        """
    )
    assets = asset_rows[0] if asset_rows else (0, 0, 0, 0)
    promo_rows = await db_query("SELECT starts_at, ends_at, active, show_on_website FROM promos")
    active_promos = sum(
        1
        for row in promo_rows
        if _promo_runtime_meta(
            starts_at=row[0],
            ends_at=row[1],
            active=bool(row[2]),
            show_on_website=bool(row[3]),
        )["visible_on_website_now"]
    )
    return {
        "today": _finance_summary(today_orders),
        "total": _finance_summary(total_orders),
        "operational": {
            "waiting_payment": _int_value(operational[0]),
            "processing_orders": _int_value(operational[1]),
            "pending_provider": _int_value(operational[2]),
            "failed_orders": _int_value(operational[3]),
            "retried_orders": _int_value(operational[4]),
            "open_tickets": _int_value(support[0]),
            "urgent_tickets": _int_value(support[1]),
            "active_products": _int_value(assets[0]),
            "active_providers": _int_value(assets[1]),
            "active_promos": active_promos,
            "active_customers": _int_value(assets[2]),
            "wallet_liability": round(_float_value(assets[3]), 2),
        },
    }


@router.get("/api/revenue-today")
async def revenue_today(_: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    today = _today_wib()
    summary = _finance_summary(await _fetch_orders(date_from=today, date_to=today, limit=5000))
    return {"revenue": summary["revenue"], "count": summary["paid_orders"], "period": "today"}


@router.get("/api/revenue-total")
async def revenue_total(_: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    summary = _finance_summary(await _fetch_orders(limit=5000))
    return {"revenue": summary["revenue"], "count": summary["paid_orders"], "period": "all"}


@router.get("/api/profit-today")
async def profit_today(_: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    today = _today_wib()
    summary = _finance_summary(await _fetch_orders(date_from=today, date_to=today, limit=5000))
    return {"profit": summary["realized_profit"], "period": "today"}


@router.get("/api/profit-total")
async def profit_total(_: Dict[str, Any] = Depends(_require_permission("orders:view"))) -> Dict[str, Any]:
    summary = _finance_summary(await _fetch_orders(limit=5000))
    return {"profit": summary["realized_profit"], "period": "all"}


@router.get("/api/reports/finance")
async def finance_report(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    _: Dict[str, Any] = Depends(_require_permission("finance:view")),
) -> Dict[str, Any]:
    orders = await _fetch_orders(date_from=date_from, date_to=date_to, limit=5000)
    return {
        "period": {"date_from": date_from or "", "date_to": date_to or ""},
        "summary": _finance_summary(orders),
        "orders": orders[:250],
    }


@router.get("/api/reports/export")
async def export_transactions(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    _: Dict[str, Any] = Depends(_require_permission("finance:view")),
) -> Response:
    orders = await _fetch_orders(date_from=date_from, date_to=date_to, limit=5000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "order_id",
            "created_at",
            "phone",
            "target_id",
            "sku",
            "product_name",
            "provider",
            "payment_method",
            "payment_status",
            "topup_status",
            "amount",
            "price",
            "product_cost",
            "payment_fee",
            "realized_profit",
            "promo_code",
            "sn",
            "note",
        ]
    )
    for order in orders:
        writer.writerow(
            [
                order["id"],
                order["created_at"],
                order["phone"],
                order["target_id"],
                order["nominal"],
                order["product_name"],
                order["provider"],
                order["payment_method"],
                order["payment_status"],
                order["topup_status"],
                order["amount"],
                order["price"],
                order["product_cost"],
                order["payment_fee"],
                order["realized_profit"],
                order["promo_code"],
                order["sn"],
                order["note"],
            ]
        )

    filename = "lixafa-transactions.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/customers")
async def get_customers(
    q: Optional[str] = Query(default=None),
    _: Dict[str, Any] = Depends(_require_permission("customers:manage")),
) -> list[Dict[str, Any]]:
    clauses = ["t.phone IS NOT NULL", "t.phone != ''"]
    params: Dict[str, Any] = {}
    cleaned_q = _clean_text(q)
    if cleaned_q:
        clauses.append("(t.phone LIKE :q OR t.target_id LIKE :q)")
        params["q"] = f"%{cleaned_q}%"

    rows = await db_query(
        f"""
        SELECT
            t.phone,
            COUNT(*) AS order_count,
            SUM(CASE WHEN t.payment_status='PAID' THEN 1 ELSE 0 END) AS paid_count,
            SUM(CASE WHEN t.topup_status='SUCCESS' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN t.topup_status='FAILED' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN t.payment_status='PAID' THEN COALESCE(t.amount, 0) ELSE 0 END) AS total_spend,
            MAX(t.created_at) AS last_order_at,
            (
                SELECT cb.reason
                FROM customer_blocks cb
                WHERE cb.active=1 AND cb.phone=t.phone
                ORDER BY cb.created_at DESC
                LIMIT 1
            ) AS block_reason,
            (
                SELECT ca.id
                FROM customer_accounts ca
                WHERE ca.phone=t.phone
                LIMIT 1
            ) AS customer_id,
            (
                SELECT ca.name
                FROM customer_accounts ca
                WHERE ca.phone=t.phone
                LIMIT 1
            ) AS customer_name,
            (
                SELECT ca.email
                FROM customer_accounts ca
                WHERE ca.phone=t.phone
                LIMIT 1
            ) AS customer_email,
            (
                SELECT wa.balance
                FROM customer_accounts ca
                LEFT JOIN wallet_accounts wa ON wa.customer_id=ca.id
                WHERE ca.phone=t.phone
                LIMIT 1
            ) AS wallet_balance
        FROM topup t
        WHERE {" AND ".join(clauses)}
        GROUP BY t.phone
        ORDER BY last_order_at DESC
        LIMIT 250
        """,
        params,
    )

    customers = [
        {
            "phone": row[0],
            "order_count": _int_value(row[1]),
            "paid_count": _int_value(row[2]),
            "success_count": _int_value(row[3]),
            "failed_count": _int_value(row[4]),
            "total_spend": round(_float_value(row[5]), 2),
            "last_order_at": _format_datetime(row[6]),
            "blocked": bool(row[7]),
            "block_reason": row[7] or "",
            "customer_id": row[8],
            "customer_name": row[9] or "",
            "customer_email": row[10] or "",
            "wallet_balance": round(_float_value(row[11]), 2),
        }
        for row in rows
    ]

    seen_phones = {item["phone"] for item in customers}
    account_rows = await db_query(
        """
        SELECT ca.id, ca.name, ca.phone, ca.email, COALESCE(wa.balance, 0), ca.created_at
        FROM customer_accounts ca
        LEFT JOIN wallet_accounts wa ON wa.customer_id=ca.id
        ORDER BY ca.created_at DESC
        LIMIT 250
        """
    )
    for row in account_rows:
        if row[2] in seen_phones:
            continue
        customers.append(
            {
                "phone": row[2],
                "order_count": 0,
                "paid_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "total_spend": 0,
                "last_order_at": _format_datetime(row[5]),
                "blocked": False,
                "block_reason": "",
                "customer_id": row[0],
                "customer_name": row[1] or "",
                "customer_email": row[3] or "",
                "wallet_balance": round(_float_value(row[4]), 2),
            }
        )
    return customers


@router.post("/api/customers/block")
async def block_customer(
    payload: CustomerBlockRequest,
    admin: Dict[str, Any] = Depends(_require_permission("customers:manage")),
) -> Dict[str, Any]:
    phone = _clean_text(payload.phone)
    target_id = _clean_text(payload.target_id)
    reason = _clean_text(payload.reason)
    if not phone and not target_id:
        raise HTTPException(status_code=400, detail="Nomor HP atau target ID wajib diisi")
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan blacklist wajib diisi")

    if phone:
        await db_execute(
            "UPDATE customer_blocks SET active=0, updated_at=CURRENT_TIMESTAMP WHERE phone=:phone AND active=1",
            {"phone": phone},
        )
    if target_id:
        await db_execute(
            "UPDATE customer_blocks SET active=0, updated_at=CURRENT_TIMESTAMP WHERE target_id=:target_id AND active=1",
            {"target_id": target_id},
        )

    await db_execute(
        """
        INSERT INTO customer_blocks (phone, target_id, reason, active, created_by)
        VALUES (:phone, :target_id, :reason, 1, :created_by)
        """,
        {"phone": phone, "target_id": target_id, "reason": reason, "created_by": admin["username"]},
    )
    await _write_audit_log(
        admin,
        action="customer.block",
        entity_type="customer",
        entity_id=phone or target_id,
        after={"phone": phone, "target_id": target_id, "reason": reason},
    )
    return {"success": True, "message": "Customer berhasil diblokir"}


@router.delete("/api/customers/block/{phone}")
async def unblock_customer(
    phone: str,
    admin: Dict[str, Any] = Depends(_require_permission("customers:manage")),
) -> Dict[str, Any]:
    await db_execute(
        "UPDATE customer_blocks SET active=0, updated_at=CURRENT_TIMESTAMP WHERE phone=:phone AND active=1",
        {"phone": phone},
    )
    await _write_audit_log(admin, action="customer.unblock", entity_type="customer", entity_id=phone)
    return {"success": True, "message": "Customer berhasil dibuka blokirnya"}


@router.post("/api/customers/wallet-adjust")
async def adjust_customer_wallet(
    payload: CustomerWalletAdjustRequest,
    admin: Dict[str, Any] = Depends(_require_permission("customers:manage")),
) -> Dict[str, Any]:
    phone = _normalize_phone(payload.phone)
    rows = await db_query(
        "SELECT id, name, phone, email FROM customer_accounts WHERE phone=:phone",
        {"phone": phone},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Akun customer belum terdaftar")

    customer_id = int(rows[0][0])
    note = _clean_text(payload.note)
    if not note:
        raise HTTPException(status_code=400, detail="Alasan adjustment wallet wajib diisi")
    idempotency_key = _clean_text(payload.idempotency_key) or str(uuid4())
    before_balance = await _wallet_balance(customer_id)
    next_balance = await _wallet_add_entry(
        customer_id=customer_id,
        entry_type=payload.entry_type,
        amount=payload.amount,
        reference_type="admin_adjustment",
        reference_id=idempotency_key,
        idempotency_key=f"admin_adjustment:{idempotency_key}",
        actor=admin.get("username") or "admin",
        note=note,
    )
    await _write_audit_log(
        admin,
        action="wallet.adjust",
        entity_type="customer",
        entity_id=customer_id,
        before={"balance": before_balance},
        after={
            "balance": next_balance,
            "amount": payload.amount,
            "entry_type": payload.entry_type.upper(),
            "phone": phone,
            "reason": note,
            "idempotency_key": idempotency_key,
        },
    )
    return {"success": True, "message": "Saldo wallet berhasil diperbarui", "balance": round(next_balance, 2)}


@router.get("/api/support-tickets")
async def get_support_tickets(_: Dict[str, Any] = Depends(_require_permission("customers:manage"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, customer_id, name, phone, email, order_id, subject, message,
               status, priority, admin_note, created_at, updated_at
        FROM support_tickets
        ORDER BY created_at DESC
        LIMIT 150
        """
    )
    return [
        {
            "id": row[0],
            "customer_id": row[1],
            "name": row[2] or "",
            "phone": row[3] or "",
            "email": row[4] or "",
            "order_id": row[5] or "",
            "subject": row[6] or "",
            "message": row[7] or "",
            "status": row[8] or "",
            "priority": row[9] or "",
            "admin_note": row[10] or "",
            "created_at": _format_datetime(row[11]),
            "updated_at": _format_datetime(row[12]),
        }
        for row in rows
    ]


@router.put("/api/support-tickets/{ticket_id}")
async def update_support_ticket(
    ticket_id: int,
    payload: SupportTicketUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("customers:manage")),
) -> Dict[str, Any]:
    before_rows = await db_query(
        "SELECT id, status, priority, admin_note FROM support_tickets WHERE id=:id",
        {"id": ticket_id},
    )
    if not before_rows:
        raise HTTPException(status_code=404, detail="Ticket tidak ditemukan")

    updates: list[str] = []
    values: Dict[str, Any] = {"id": ticket_id}
    if payload.status is not None:
        status = (_clean_text(payload.status) or "OPEN").upper()
        if status not in {"OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"}:
            raise HTTPException(status_code=400, detail="Status ticket tidak valid")
        updates.append("status=:status")
        values["status"] = status
    if payload.priority is not None:
        priority = (_clean_text(payload.priority) or "NORMAL").upper()
        if priority not in {"LOW", "NORMAL", "HIGH", "URGENT"}:
            raise HTTPException(status_code=400, detail="Priority ticket tidak valid")
        updates.append("priority=:priority")
        values["priority"] = priority
    if payload.admin_note is not None:
        updates.append("admin_note=:admin_note")
        values["admin_note"] = _clean_text(payload.admin_note)

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data ticket yang diperbarui")

    await db_execute(
        f"UPDATE support_tickets SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=:id",
        values,
    )
    after_rows = await db_query(
        "SELECT id, status, priority, admin_note FROM support_tickets WHERE id=:id",
        {"id": ticket_id},
    )
    await _write_audit_log(
        admin,
        action="support_ticket.update",
        entity_type="support_ticket",
        entity_id=ticket_id,
        before=before_rows[0],
        after=after_rows[0] if after_rows else None,
    )
    return {"success": True, "message": "Ticket berhasil diperbarui"}


@router.get("/api/notification-outbox")
async def get_notification_outbox(_: Dict[str, Any] = Depends(_require_permission("audit:view"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, channel, recipient, subject, body, status,
               reference_type, reference_id, error, created_at, sent_at
        FROM notification_outbox
        ORDER BY created_at DESC
        LIMIT 150
        """
    )
    return [
        {
            "id": row[0],
            "channel": row[1] or "",
            "recipient": row[2] or "",
            "subject": row[3] or "",
            "body": row[4] or "",
            "status": row[5] or "",
            "reference_type": row[6] or "",
            "reference_id": row[7] or "",
            "error": row[8] or "",
            "created_at": _format_datetime(row[9]),
            "sent_at": _format_datetime(row[10]),
        }
        for row in rows
    ]


@router.get("/api/webhook-events")
async def get_webhook_events(_: Dict[str, Any] = Depends(_require_permission("api:monitor"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, provider, event_type, reference_id, signature_valid,
               payload, response_status, message, created_at
        FROM webhook_events
        ORDER BY created_at DESC
        LIMIT 100
        """
    )
    return [
        {
            "id": row[0],
            "provider": row[1] or "",
            "event_type": row[2] or "",
            "reference_id": row[3] or "",
            "signature_valid": bool(row[4]),
            "payload": row[5] or "",
            "response_status": row[6] or "",
            "message": row[7] or "",
            "created_at": _format_datetime(row[8]),
        }
        for row in rows
    ]


@router.get("/api/audit-logs")
async def get_audit_logs(_: Dict[str, Any] = Depends(_require_permission("audit:view"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, actor, action, entity_type, entity_id,
               before_json, after_json, ip_address, created_at
        FROM audit_logs
        ORDER BY created_at DESC
        LIMIT 150
        """
    )
    return [
        {
            "id": row[0],
            "actor": row[1] or "",
            "action": row[2] or "",
            "entity_type": row[3] or "",
            "entity_id": row[4] or "",
            "before_json": row[5] or "",
            "after_json": row[6] or "",
            "ip_address": row[7] or "",
            "created_at": _format_datetime(row[8]),
        }
        for row in rows
    ]


@router.post("/bulk-markup")
async def bulk_markup(payload: BulkMarkupRequest, _: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    brand = payload.brand.upper()
    percent = payload.percent
    min_profit = payload.min_profit

    if brand == "ALL":
        rows = await db_query("SELECT sku, cost_price, price FROM products")
    else:
        rows = await db_query(
            "SELECT sku, cost_price, price FROM products WHERE UPPER(provider)=:brand",
            {"brand": brand},
        )

    updated = 0
    for sku, cost_price, current_price in rows:
        cost = float(cost_price or 0)
        if cost <= 0:
            continue
        new_price = max(float(current_price or cost), cost + max(min_profit, int(cost * percent / 100)))
        await db_execute("UPDATE products SET price=:price WHERE sku=:sku", {"price": new_price, "sku": sku})
        updated += 1

    return {"success": True, "message": f"Harga berhasil diperbarui untuk {updated} produk"}


@router.post("/sync-products")
async def sync_products(_: Dict[str, Any] = Depends(_require_permission("products:manage"))) -> Dict[str, Any]:
    result = await sync_digiflazz_products()
    return {
        "success": True,
        "message": result["message"],
        "processed": result["processed"],
        "errors": result["errors"],
    }


@router.get("/api/promos")
async def get_promos(_: Dict[str, Any] = Depends(_require_permission("promos:manage"))) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, title, code, description, badge, cta_text, cta_url, image_url,
               rule_type, target_scope, target_value, discount_type, discount_value,
               max_discount, COALESCE(usage_limit, 0), payment_methods,
               COALESCE(budget_limit, 0), COALESCE(max_per_customer, 0),
               COALESCE(max_per_phone, 0), COALESCE(max_per_target, 0),
               COALESCE(stackable, 1), COALESCE(priority, 0),
               starts_at, ends_at, show_on_website, active,
               created_at, updated_at,
               (
                   SELECT COUNT(*) FROM promotion_redemptions pr
                   WHERE pr.promo_id=promos.id AND pr.status='REDEEMED'
               ) + CASE WHEN COALESCE(code, '')='' THEN 0 ELSE (
                   SELECT COUNT(*)
                   FROM topup t
                   WHERE UPPER(COALESCE(t.promo_code, ''))=UPPER(COALESCE(promos.code, ''))
                     AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED', 'REFUND')
                     AND NOT EXISTS (
                         SELECT 1 FROM promotion_redemptions pr2
                         WHERE pr2.promo_id=promos.id AND pr2.order_id=t.id
                     )
               ) END AS usage_count,
               COALESCE((
                   SELECT SUM(COALESCE(pr.discount_amount, 0)) FROM promotion_redemptions pr
                   WHERE pr.promo_id=promos.id AND pr.status='REDEEMED'
               ), 0) + CASE WHEN COALESCE(code, '')='' THEN 0 ELSE COALESCE((
                   SELECT SUM(COALESCE(t.promo_discount_amount, 0))
                   FROM topup t
                   WHERE UPPER(COALESCE(t.promo_code, ''))=UPPER(COALESCE(promos.code, ''))
                     AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED', 'REFUND')
                     AND NOT EXISTS (
                         SELECT 1 FROM promotion_redemptions pr2
                         WHERE pr2.promo_id=promos.id AND pr2.order_id=t.id
                     )
               ), 0) END AS discount_spent
               , internal_code, promo_type, lifecycle_status, rules_version,
               internal_description, customer_description, admin_notes, calculation_type,
               COALESCE(minimum_transaction, 0), special_price, COALESCE(rounding_rule, 'none'),
               COALESCE(quota_daily, 0), COALESCE(max_per_customer_daily, 0),
               COALESCE(customer_segment, 'all'), COALESCE(timezone, 'Asia/Jakarta'),
               active_days, daily_start_time, daily_end_time, COALESCE(exclusive, 0),
               COALESCE(max_promotions_per_order, 2), placements, COALESCE(display_order, 0),
               COALESCE(allow_external_cta, 0), COALESCE(legacy_compatible, 1),
               archived_at, paused_at, ended_at, created_by, updated_by,
               (
                   SELECT COUNT(*)
                   FROM promotion_redemptions pr
                   WHERE pr.promo_id=promos.id AND pr.status IN ('RESERVED', 'REDEEMED')
               ) + CASE
                   WHEN COALESCE(code, '') = '' THEN 0
                   ELSE (
                       SELECT COUNT(*)
                       FROM topup t
                       WHERE UPPER(COALESCE(t.promo_code, '')) = UPPER(COALESCE(promos.code, ''))
                         AND COALESCE(t.payment_status, '') NOT IN ('CANCELED', 'FAILED', 'EXPIRED', 'REFUNDED', 'REFUND')
                         AND NOT EXISTS (
                             SELECT 1 FROM promotion_redemptions pr2
                             WHERE pr2.promo_id=promos.id AND pr2.order_id=t.id
                         )
                   )
               END AS capacity_count,
               eligibility_rules
        FROM promos
        WHERE archived_at IS NULL
        ORDER BY COALESCE(priority, 0) DESC, created_at DESC
        """
    )
    promos = [_promo_dict(row) for row in rows]
    relations = await _promo_relations_by_ids([int(promo["id"]) for promo in promos])
    for promo in promos:
        relation = relations.get(int(promo["id"]), {"targets": [], "exclusions": [], "customer_ids": []})
        promo.update(relation)
        promo.update(
            _eligibility_response(
                promo.pop("eligibility_rules_raw", None),
                customer_segment=str(promo.get("customer_segment") or "all"),
                customer_ids=list(relation["customer_ids"]),
            )
        )
        promo["target_option_ids"] = [item["id"] for item in relation["targets"]]
        promo["excluded_target_option_ids"] = [item["id"] for item in relation["exclusions"]]
        segment_meta = _customer_segment_metadata(
            promo.get("customer_segment"),
            target_count=len(relation["customer_ids"]),
        )
        promo["customer_segment_label"] = segment_meta["label"]
        promo["customer_segment_description"] = segment_meta["description"]
        promo["customer_target_count"] = segment_meta["target_count"]
        promo["customer_segment_summary"] = segment_meta["summary"]
    return promos


@router.get("/api/promos/options")
async def get_promo_options(_: Dict[str, Any] = Depends(_require_permission("promos:manage"))) -> Dict[str, Any]:
    catalog_rows = await db_query(
        """
        SELECT id, target_type, target_key, label, COALESCE(active, 1)
        FROM promotion_target_catalog
        ORDER BY target_type, label
        """
    )
    target_options = [
        {
            "id": int(row[0]),
            "target_type": row[1],
            "value": row[2],
            "target_key": row[2],
            "label": row[3],
            "active": bool(row[4]),
        }
        for row in catalog_rows
    ]
    catalog_lookup = {
        (str(item["target_type"]), str(item["target_key"]).casefold()): item for item in target_options
    }
    rows = await db_query(
        """
        SELECT sku, provider, name, category, COALESCE(active, 0)
        FROM products
        ORDER BY category, provider, name
        """
    )

    categories: dict[str, Dict[str, str]] = {}
    providers: dict[str, Dict[str, str]] = {}
    skus: list[Dict[str, Any]] = []
    for sku, provider, name, category, active in rows:
        normalized_provider = normalized_provider_name(provider, name, category)
        normalized_category = normalized_category_name(category, normalized_provider)
        if normalized_category:
            catalog_item = catalog_lookup.get(("category", str(category or normalized_category).casefold()), {})
            categories[normalized_category] = {
                "id": catalog_item.get("id"),
                "value": normalized_category,
                "label": normalized_category,
            }
        if normalized_provider:
            catalog_item = catalog_lookup.get(("provider", str(provider or normalized_provider).casefold()), {})
            providers[normalized_provider] = {
                "id": catalog_item.get("id"),
                "value": normalized_provider,
                "label": normalized_provider,
            }
        sku_catalog = catalog_lookup.get(("sku", str(sku or "").casefold()), {})
        skus.append(
            {
                "id": sku_catalog.get("id"),
                "value": sku,
                "label": f"{name or sku} ({sku})",
                "sku": sku,
                "name": name or "",
                "provider": normalized_provider,
                "category": normalized_category,
                "active": bool(active),
            }
        )

    payment_methods = [
        {"value": "QRIS", "label": "QRIS"},
        {"value": "DANA", "label": "DANA"},
        {"value": "OVO", "label": "OVO"},
        {"value": "SHOPEEPAY", "label": "ShopeePay"},
        {"value": "WALLET", "label": "Wallet LIXAFA"},
    ]
    try:
        response = await get_payment_channels()
        data = response.get("data") if isinstance(response, dict) else []
        if isinstance(data, dict):
            data = data.get("data") or data.get("channels") or []
        if isinstance(data, list) and data:
            seen = set()
            payment_methods = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or item.get("method") or "").strip().upper()
                if not code or code in seen:
                    continue
                seen.add(code)
                payment_methods.append(
                    {
                        "value": code,
                        "label": item.get("name") or item.get("payment_name") or code,
                        "group": item.get("group") or item.get("type") or "",
                    }
                )
            payment_methods.append({"value": "WALLET", "label": "Wallet LIXAFA", "group": "Saldo LIXAFA"})
    except Exception:
        pass

    customer_rows = await db_query(
        """
        SELECT id, COALESCE(name, ''), COALESCE(phone, ''), COALESCE(active, 0)
        FROM customer_accounts
        ORDER BY COALESCE(active, 0) DESC, COALESCE(name, ''), id
        LIMIT 500
        """
    )
    simulation_customer_options: list[Dict[str, Any]] = []
    for customer_id, customer_name, customer_phone, customer_active in customer_rows:
        masked_phone = _masked_customer_phone(customer_phone)
        name = str(customer_name or "").strip() or f"Customer #{int(customer_id)}"
        status_label = "aktif" if bool(customer_active) else "nonaktif"
        option = {
            "id": int(customer_id),
            "value": int(customer_id),
            "label": f"{name} · {masked_phone or 'nomor tidak tersedia'} ({status_label})",
            "name": name,
            "phone_masked": masked_phone,
            "active": bool(customer_active),
        }
        simulation_customer_options.append(option)
    customer_options = [dict(option) for option in simulation_customer_options if option["active"]]

    return {
        "target_scopes": [
            {"value": "all", "label": "Semua produk"},
            {"value": "category", "label": "Kategori"},
            {"value": "provider", "label": "Provider/Game"},
            {"value": "sku", "label": "Produk/SKU tertentu"},
        ],
        "categories": sorted(categories.values(), key=lambda item: item["label"].lower()),
        "providers": sorted(providers.values(), key=lambda item: item["label"].lower()),
        "skus": skus,
        "target_options": target_options,
        "payment_methods": payment_methods,
        "promo_types": [
            {"value": "banner", "label": "Konten Banner"},
            {"value": "automatic", "label": "Diskon Otomatis"},
            {"value": "voucher", "label": "Kode Voucher"},
            {"value": "special_price", "label": "Harga Khusus Produk"},
            {"value": "payment_method", "label": "Promo Metode Pembayaran"},
        ],
        "placements": [
            {"value": "home_banner", "label": "Banner promo beranda"},
            {"value": "promo_cards", "label": "Kartu promo"},
            {"value": "product", "label": "Kartu produk"},
            {"value": "checkout", "label": "Checkout"},
        ],
        "customer_segments": [dict(option) for option in PROMO_CUSTOMER_SEGMENT_OPTIONS],
        "eligibility": eligibility_options_metadata(),
        "customer_options": customer_options,
        "simulation_customer_options": simulation_customer_options,
    }


@router.get("/api/promos/customer-options")
async def get_promo_customer_options(
    q: str = Query(default="", max_length=120),
    include_inactive: bool = Query(default=False),
    ids: str = Query(default="", max_length=1000),
    limit: int = Query(default=50, ge=1, le=100),
    _: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    requested_ids: list[int] = []
    if ids.strip():
        for raw_value in ids.split(","):
            cleaned = raw_value.strip()
            if not cleaned or not cleaned.isdigit() or int(cleaned) <= 0:
                raise HTTPException(status_code=400, detail="Daftar ID customer tidak valid")
            customer_id = int(cleaned)
            if customer_id not in requested_ids:
                requested_ids.append(customer_id)
        if len(requested_ids) > 100:
            raise HTTPException(status_code=400, detail="Maksimal 100 ID customer dapat dimuat sekaligus")

    effective_limit = min(max(limit, len(requested_ids)), 100)
    params: Dict[str, Any] = {"row_limit": effective_limit + 1}
    id_clause = ""
    if requested_ids:
        placeholders = []
        for index, customer_id in enumerate(requested_ids):
            key = f"customer_id_{index}"
            placeholders.append(f":{key}")
            params[key] = customer_id
        id_clause = f"id IN ({', '.join(placeholders)})"

    search = q.strip().casefold()
    search_clause = ""
    if search:
        params["search"] = f"%{search}%"
        search_parts = [
            "LOWER(COALESCE(name, '')) LIKE :search",
            "LOWER(COALESCE(phone, '')) LIKE :search",
            "LOWER(COALESCE(email, '')) LIKE :search",
            "LOWER(CAST(id AS TEXT)) LIKE :search",
        ]
        normalized_phone = normalize_phone(search)
        phone_variants: set[str] = set()
        if normalized_phone:
            phone_variants.add(normalized_phone)
            if normalized_phone.startswith("62"):
                phone_variants.add(f"0{normalized_phone[2:]}")
                phone_variants.add(f"+{normalized_phone}")
        for index, phone_variant in enumerate(sorted(phone_variants)):
            key = f"phone_search_{index}"
            params[key] = f"%{phone_variant}%"
            search_parts.append(f"COALESCE(phone, '') LIKE :{key}")
        search_clause = f"({' OR '.join(search_parts)})"

    conditions: list[str] = []
    if id_clause and search_clause:
        conditions.append(f"({id_clause} OR {search_clause})")
    elif id_clause:
        conditions.append(id_clause)
    elif search_clause:
        conditions.append(search_clause)
    if not include_inactive:
        conditions.append(f"(COALESCE(active, 0)=1{f' OR {id_clause}' if id_clause else ''})")
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    requested_order = f"CASE WHEN {id_clause} THEN 0 ELSE 1 END," if id_clause else ""
    rows = await db_query(
        f"""
        SELECT id, COALESCE(name, ''), COALESCE(phone, ''), COALESCE(active, 0)
        FROM customer_accounts
        {where_sql}
        ORDER BY {requested_order} COALESCE(name, ''), id
        LIMIT :row_limit
        """,
        params,
    )
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = []
    for customer_id, customer_name, customer_phone, customer_active in rows:
        masked_phone = _masked_customer_phone(customer_phone)
        name = str(customer_name or "").strip() or f"Customer #{int(customer_id)}"
        status_label = "aktif" if bool(customer_active) else "nonaktif"
        items.append(
            {
                "id": int(customer_id),
                "value": int(customer_id),
                "label": f"{name} · {masked_phone or 'nomor tidak tersedia'} ({status_label})",
                "name": name,
                "phone_masked": masked_phone,
                "active": bool(customer_active),
            }
        )
    return {
        "items": items,
        "has_more": has_more,
        "limit": effective_limit,
    }


def _option_matches_product(option: Dict[str, Any], product: Dict[str, Any]) -> bool:
    target_type = str(option.get("target_type") or "").lower()
    target_key = str(option.get("target_key") or "").strip().casefold()
    if target_type == "sku":
        return str(product.get("sku") or "").strip().casefold() == target_key
    if target_type == "provider":
        return str(product.get("provider") or "").strip().casefold() == target_key
    if target_type == "category":
        return str(product.get("category") or "").strip().casefold() == target_key
    return False


async def _affected_products_for_promo(promo: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT sku, provider, name, category, COALESCE(price, 0), COALESCE(cost_price, 0), COALESCE(active, 0)
        FROM products
        ORDER BY category, provider, name
        """
    )
    included = promo.get("targets") or []
    exclusions = promo.get("exclusions") or []
    products: list[Dict[str, Any]] = []
    for row in rows:
        product = {
            "sku": row[0],
            "provider": row[1] or "",
            "name": row[2] or row[0],
            "category": row[3] or "",
            "price": int(Decimal(str(row[4] or 0))),
            "cost_price": int(Decimal(str(row[5] or 0))),
            "active": bool(row[6]),
        }
        if included:
            allowed = any(_option_matches_product(option, product) for option in included)
        else:
            allowed = _promo_target_allows_product(
                str(promo.get("target_scope") or "all"),
                promo.get("target_value"),
                product,
            )
        if not allowed or any(_option_matches_product(option, product) for option in exclusions):
            continue
        products.append(product)
    return products


@router.get("/api/promos/manage")
async def manage_promos(
    q: str = "",
    status: Optional[str] = None,
    promo_type: Optional[str] = None,
    scope: Optional[str] = None,
    period: Optional[str] = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    items = await get_promos(admin)
    search = q.strip().casefold()
    if search:
        items = [
            item
            for item in items
            if search
            in " ".join(
                str(item.get(key) or "")
                for key in ("title", "internal_code", "code", "description", "target_value", "promo_type")
            ).casefold()
        ]
    if status:
        allowed_statuses = {part.strip().lower() for part in status.split(",") if part.strip()}
        items = [item for item in items if str(item.get("runtime_status") or "").lower() in allowed_statuses]
    if promo_type:
        allowed_types = {part.strip().lower() for part in promo_type.split(",") if part.strip()}
        items = [item for item in items if str(item.get("promo_type") or "").lower() in allowed_types]
    if scope:
        allowed_scopes = {part.strip().lower() for part in scope.split(",") if part.strip()}
        items = [item for item in items if str(item.get("target_scope") or "").lower() in allowed_scopes]
    if period:
        normalized_period = period.strip().lower()
        period_statuses = {
            "current": {"live", "quota_exhausted", "budget_exhausted"},
            "scheduled": {"scheduled"},
            "past": {"expired", "ended"},
        }
        if normalized_period in period_statuses:
            items = [item for item in items if item.get("runtime_status") in period_statuses[normalized_period]]

    sort_fields = {
        "title": "title",
        "code": "code",
        "promo_type": "promo_type",
        "status": "runtime_status",
        "starts_at": "starts_at",
        "ends_at": "ends_at",
        "usage": "usage_count",
        "discount": "discount_spent",
        "updated_at": "updated_at",
        "priority": "priority",
    }
    sort_key = sort_fields.get(sort, "updated_at")
    reverse = order.strip().lower() != "asc"
    items.sort(key=lambda item: (item.get(sort_key) is not None, item.get(sort_key) or ""), reverse=reverse)
    total = len(items)
    offset = (page - 1) * page_size
    page_items = items[offset : offset + page_size]
    for item in page_items:
        products = await _affected_products_for_promo(item)
        item["affected_product_count"] = len(products)
        item["product_preview"] = products[:5]
    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.post("/api/promos/target-preview")
async def preview_promo_targets(
    payload: PromoTargetPreviewRequest,
    _: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    included = await _validated_relation_options(payload.target_option_ids)
    excluded = await _validated_relation_options(payload.excluded_target_option_ids)
    if {item["id"] for item in included} & {item["id"] for item in excluded}:
        raise HTTPException(status_code=400, detail="Target include dan exclude tidak boleh sama")
    scope, value = _legacy_target_from_options(included)
    products = await _affected_products_for_promo(
        {"targets": included, "exclusions": excluded, "target_scope": scope, "target_value": value}
    )
    return {"count": len(products), "products": products[:50], "truncated": len(products) > 50}


@router.get("/api/promos/{promo_id:int}")
async def get_promo_detail(
    promo_id: int,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    promo = next((item for item in await get_promos(admin) if int(item["id"]) == promo_id), None)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    products = await _affected_products_for_promo(promo)
    promo["affected_product_count"] = len(products)
    promo["product_preview"] = products[:25]
    return promo


@router.get("/api/promos/{promo_id:int}/usage")
async def get_promo_usage(
    promo_id: int,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    promo = next((item for item in await get_promos(admin) if int(item["id"]) == promo_id), None)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    rows = await db_query(
        """
        SELECT pr.order_id, pr.status, pr.original_amount, pr.discount_amount, pr.final_amount,
               pr.payment_method, pr.product_sku, pr.voucher_code, pr.application_source,
               pr.reserved_at, pr.redeemed_at, pr.released_at, pr.customer_id,
               COALESCE(t.topup_status, ''), COALESCE(t.payment_status, '')
        FROM promotion_redemptions pr
        LEFT JOIN topup t ON t.id=pr.order_id
        WHERE pr.promo_id=:promo_id
        ORDER BY COALESCE(pr.redeemed_at, pr.reserved_at, pr.created_at) DESC
        LIMIT 500
        """,
        {"promo_id": promo_id},
    )
    usage = [
        {
            "order_id": row[0],
            "status": row[1],
            "transaction_value": int(Decimal(str(row[2] or 0))),
            "discount_amount": int(Decimal(str(row[3] or 0))),
            "final_amount": int(Decimal(str(row[4] or 0))),
            "payment_method": row[5] or "",
            "product_sku": row[6] or "",
            "voucher_code": row[7] or "",
            "application_source": row[8] or "",
            "reserved_at": _format_datetime(row[9]),
            "redeemed_at": _format_datetime(row[10]),
            "released_at": _format_datetime(row[11]),
            "customer_id": row[12],
            "topup_status": row[13],
            "payment_status": row[14],
        }
        for row in rows
    ]
    redeemed = [item for item in usage if item["status"] == "REDEEMED"]
    product_counts: Dict[str, int] = {}
    method_counts: Dict[str, int] = {}
    for item in redeemed:
        product_counts[item["product_sku"] or "-"] = product_counts.get(item["product_sku"] or "-", 0) + 1
        method_counts[item["payment_method"] or "-"] = method_counts.get(item["payment_method"] or "-", 0) + 1
    discount_total = sum(item["discount_amount"] for item in redeemed)
    transaction_total = sum(item["transaction_value"] for item in redeemed)
    return {
        "promo": promo,
        "summary": {
            "redeemed_count": len(redeemed),
            "reserved_count": sum(1 for item in usage if item["status"] == "RESERVED"),
            "released_count": sum(1 for item in usage if item["status"] == "RELEASED"),
            "transaction_total": transaction_total,
            "discount_total": discount_total,
            "average_discount": int(discount_total / len(redeemed)) if redeemed else 0,
            "top_products": sorted(product_counts.items(), key=lambda item: (-item[1], item[0]))[:10],
            "payment_methods": sorted(method_counts.items(), key=lambda item: (-item[1], item[0])),
        },
        "orders": usage,
    }


async def _simulate_payment_fee(amount: float, method: str) -> tuple[int, str]:
    normalized_method = (method or "QRIS").strip().upper()
    if amount <= 0 or normalized_method in {"FREE_PROMO", "WALLET"}:
        return 0, "free"
    try:
        response = await calculate_fee(normalized_method, int(amount))
        data = response.get("data") if isinstance(response, dict) else {}
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            fee = data.get("total_fee") or data.get("fee") or data.get("fee_merchant") or data.get("fee_customer")
            if fee is not None:
                return int(float(fee or 0)), "tripay"
    except Exception:
        pass
    return 0, "estimasi"


def _promo_target_allows_product(scope: str, target_value: Optional[str], product: Dict[str, Any]) -> bool:
    normalized_scope = _clean_target_scope(scope)
    normalized_target = (_clean_text(target_value) or "").lower()
    if normalized_scope == "all":
        return True
    if not normalized_target:
        return False
    if normalized_scope == "sku":
        return str(product.get("sku") or "").lower() == normalized_target
    if normalized_scope == "provider":
        return str(product.get("provider") or "").lower() == normalized_target
    if normalized_scope == "category":
        return str(product.get("category") or "").lower() == normalized_target
    return False


def _review_discount(promo: Dict[str, Any], price: int) -> tuple[int, int]:
    base = Decimal(price)
    calculation = str(promo.get("calculation_type") or promo.get("discount_type") or "").lower()
    value = Decimal(str(promo.get("discount_value") or 0))
    if calculation == "percent":
        discount = (base * value / Decimal("100")).quantize(Decimal("1"))
    elif calculation == "fixed":
        discount = value
    elif calculation == "special_price":
        special = Decimal(str(promo.get("special_price") or 0))
        discount = max(base - special, Decimal("0"))
    else:
        discount = Decimal("0")
    cap = Decimal(str(promo.get("max_discount") or 0))
    if cap > 0:
        discount = min(discount, cap)
    discount = min(max(discount, Decimal("0")), base)
    final = base - discount
    rounding = str(promo.get("rounding_rule") or "none")
    match = re.fullmatch(r"(floor|ceil)_(100|500|1000)", rounding)
    if match and final > 0:
        unit = Decimal(match.group(2))
        rounding_mode = "ROUND_FLOOR" if match.group(1) == "floor" else "ROUND_CEILING"
        final = (final / unit).to_integral_value(rounding=rounding_mode) * unit
        final = min(max(final, Decimal("0")), base)
        discount = base - final
    return int(discount), int(final)


def _periods_overlap(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    latest = datetime.max.replace(tzinfo=timezone.utc)
    first_start = _to_utc_datetime(first.get("starts_at")) or earliest
    first_end = _to_utc_datetime(first.get("ends_at")) or latest
    second_start = _to_utc_datetime(second.get("starts_at")) or earliest
    second_end = _to_utc_datetime(second.get("ends_at")) or latest
    return first_start <= second_end and second_start <= first_end


async def _build_promo_review(
    promo: Dict[str, Any],
    admin: Dict[str, Any],
) -> Dict[str, Any]:
    products = await _affected_products_for_promo(promo)
    methods = promo.get("payment_methods") or ["QRIS"]
    method = str(methods[0] or "QRIS")
    margins: list[Dict[str, Any]] = []
    max_discount = 0
    for product in products:
        discount, final_price = _review_discount(promo, int(product["price"]))
        payment_fee = calculate_admin_fee(final_price, method) if final_price > 0 else 0
        net_profit = final_price - int(product["cost_price"]) - payment_fee
        max_discount = max(max_discount, discount)
        margins.append(
            {
                "sku": product["sku"],
                "name": product["name"],
                "normal_price": product["price"],
                "final_price": final_price,
                "cost_price": product["cost_price"],
                "discount_amount": discount,
                "payment_fee_estimate": payment_fee,
                "net_profit_estimate": net_profit,
                "margin_percent": round((net_profit / product["price"] * 100), 2) if product["price"] else 0,
            }
        )
    margins.sort(key=lambda item: (item["net_profit_estimate"], item["sku"]))

    current_skus = {item["sku"] for item in products}
    conflicts: list[Dict[str, Any]] = []
    for other in await get_promos(admin):
        if int(other["id"]) == int(promo["id"]):
            continue
        if other.get("runtime_status") in {"disabled", "ended", "expired", "archived"}:
            continue
        if not _periods_overlap(promo, other):
            continue
        other_products = await _affected_products_for_promo(other)
        overlap = sorted(current_skus & {item["sku"] for item in other_products})
        if not overlap:
            continue
        first_methods = set(promo.get("payment_methods") or [])
        second_methods = set(other.get("payment_methods") or [])
        methods_overlap = not first_methods or not second_methods or bool(first_methods & second_methods)
        if not methods_overlap:
            continue
        conflicts.append(
            {
                "promo_id": other["id"],
                "title": other["title"],
                "runtime_status": other["runtime_status"],
                "priority": other.get("priority", 0),
                "same_priority": int(other.get("priority") or 0) == int(promo.get("priority") or 0),
                "exclusive": bool(other.get("exclusive") or promo.get("exclusive")),
                "overlap_product_count": len(overlap),
                "sample_skus": overlap[:10],
            }
        )

    blocking_errors: list[str] = []
    warnings: list[str] = []
    if promo.get("eligibility_valid") is False:
        blocking_errors.append("Aturan eligibility pelanggan tidak valid")
    if promo.get("promo_type") != "banner" and not products:
        blocking_errors.append("Tidak ada produk yang cocok dengan target promo")
    ends_at = _to_utc_datetime(promo.get("ends_at"))
    if ends_at and ends_at <= datetime.now(timezone.utc):
        blocking_errors.append("Periode promo sudah berakhir")
    below_cost = [item for item in margins if item["net_profit_estimate"] < 0]
    if below_cost:
        warnings.append(f"{len(below_cost)} produk diperkirakan rugi setelah diskon dan biaya pembayaran")
    if conflicts:
        warnings.append(f"Terdapat {len(conflicts)} promo lain dengan periode, target, dan metode yang tumpang tindih")
    usage_limit = int(promo.get("usage_limit") or 0)
    budget_limit = int(promo.get("budget_limit") or 0)
    estimated_max_cost = max_discount * usage_limit if usage_limit else None
    if estimated_max_cost is not None and budget_limit:
        estimated_max_cost = min(estimated_max_cost, budget_limit)
    elif estimated_max_cost is None and budget_limit:
        estimated_max_cost = budget_limit
    return {
        "eligible_for_activation": not blocking_errors,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "summary": {
            "promo_id": promo["id"],
            "title": promo["title"],
            "promo_type": promo.get("promo_type"),
            "period": {"starts_at": promo.get("starts_at"), "ends_at": promo.get("ends_at"), "timezone": promo.get("timezone")},
            "target_count": len(promo.get("targets") or []),
            "exclusion_count": len(promo.get("exclusions") or []),
            "affected_product_count": len(products),
            "payment_methods": promo.get("payment_methods") or [],
            "customer_segment": promo.get("customer_segment"),
            "customer_segment_label": promo.get("customer_segment_label")
            or _customer_segment_metadata(promo.get("customer_segment"))["label"],
            "customer_segment_summary": promo.get("customer_segment_summary")
            or _customer_segment_metadata(
                promo.get("customer_segment"),
                target_count=len(promo.get("customer_ids") or []),
            )["summary"],
            "eligibility_source": promo.get("eligibility_source"),
            "eligibility_summary": promo.get("eligibility_summary"),
            "eligibility_rules": promo.get("eligibility_rules"),
            "customer_target_count": len(promo.get("customer_ids") or []),
            "usage_limit": usage_limit,
            "budget_limit": budget_limit,
            "estimated_max_cost": estimated_max_cost,
            "lowest_margin": margins[0] if margins else None,
        },
        "margins": margins[:100],
        "conflicts": conflicts,
        "preview": {
            "title": promo["title"],
            "badge": promo.get("badge"),
            "description": promo.get("customer_description") or promo.get("description"),
            "cta_text": promo.get("cta_text"),
            "image_url": promo.get("image_url"),
            "placements": promo.get("placements") or [],
        },
    }


@router.get("/api/promos/{promo_id:int}/review")
async def review_promo(
    promo_id: int,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    promo = next((item for item in await get_promos(admin) if int(item["id"]) == promo_id), None)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    return await _build_promo_review(promo, admin)


@router.post("/api/promos/{promo_id:int}/duplicate")
async def duplicate_promo(
    promo_id: int,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    promo = next((item for item in await get_promos(admin) if int(item["id"]) == promo_id), None)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    fields = set(PromoCreateRequest.model_fields)
    data = {field: promo.get(field) for field in fields if field in promo}
    data.update(
        {
            "title": f"{promo['title']} (Salinan)",
            "internal_code": f"COPY-{promo_id}-{uuid4().hex[:8]}",
            "code": None,
            "promo_type": "automatic" if promo.get("promo_type") == "voucher" else promo.get("promo_type"),
            "lifecycle_status": "draft",
            "status": "draft",
            "active": 0,
            "show_on_website": 0,
            "target_option_ids": promo.get("target_option_ids", []),
            "excluded_target_option_ids": promo.get("excluded_target_option_ids", []),
            "customer_ids": promo.get("customer_ids", []),
        }
    )
    if promo.get("eligibility_source") == "explicit":
        data["eligibility_rules"] = promo.get("eligibility_rules")
    else:
        data.pop("eligibility_rules", None)
    if promo.get("eligibility_source") == "invalid_explicit":
        raise HTTPException(
            status_code=409,
            detail="Aturan eligibility promo tidak valid dan harus diperbaiki sebelum diduplikat",
        )
    # create_promo writes the copied row, all relations, and its create audit in
    # one transaction. A second post-commit audit here could otherwise fail and
    # report an error even though the duplicate already exists.
    result = await create_promo(PromoCreateRequest(**data), admin)
    result["message"] = "Promo berhasil diduplikat sebagai draft tanpa kode voucher"
    return result


@router.post("/api/promos/{promo_id:int}/actions/{action}")
async def promo_action(
    promo_id: int,
    action: str,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    normalized_action = action.strip().lower()
    transitions = {
        "pause": ("paused", 0),
        "activate": ("active", 1),
        "end": ("ended", 0),
        "disable": ("disabled", 0),
        "archive": ("archived", 0),
    }
    if normalized_action not in transitions:
        raise HTTPException(status_code=400, detail="Aksi promo tidak dikenal")
    promo = next((item for item in await get_promos(admin) if int(item["id"]) == promo_id), None)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    review = None
    if normalized_action == "activate":
        review = await _build_promo_review(promo, admin)
        if not review["eligible_for_activation"]:
            raise HTTPException(status_code=409, detail={"message": "Promo belum aman diaktifkan", "review": review})
    lifecycle, active = transitions[normalized_action]
    actor = admin.get("username") or admin.get("sub") or "admin"
    async with db_transaction() as connection:
        await db_execute(
            """
            UPDATE promos
            SET lifecycle_status=:lifecycle,
                active=:active,
                paused_at=CASE WHEN :lifecycle='paused' THEN CURRENT_TIMESTAMP ELSE NULL END,
                ended_at=CASE WHEN :lifecycle='ended' THEN CURRENT_TIMESTAMP ELSE ended_at END,
                archived_at=CASE WHEN :lifecycle='archived' THEN CURRENT_TIMESTAMP ELSE archived_at END,
                updated_by=:actor,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:promo_id
            """,
            {"promo_id": promo_id, "lifecycle": lifecycle, "active": active, "actor": actor},
            connection=connection,
        )
        await _write_audit_log(
            admin,
            action=f"promo.{normalized_action}",
            entity_type="promo",
            entity_id=promo_id,
            before={"lifecycle_status": promo.get("lifecycle_status"), "active": promo.get("active")},
            after={"lifecycle_status": lifecycle, "active": bool(active), "review": review},
            connection=connection,
        )
    return {
        "success": True,
        "status": lifecycle,
        "warnings": (review or {}).get("warnings", []),
        "message": f"Status promo diubah menjadi {lifecycle}",
    }


@router.post("/api/promos/simulate")
async def simulate_promo(
    payload: PromoSimulationRequest,
    _: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    canonical_sku = normalize_sku(payload.sku)
    row = await db_query(
        """
        SELECT sku, provider, name, category, price
        FROM products
        WHERE LOWER(TRIM(sku))=:sku
        """,
        {"sku": canonical_sku},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Produk simulasi tidak ditemukan")

    sku, provider, name, category, price = row[0]
    normalized_provider = normalized_provider_name(provider, name, category)
    product = {
        "sku": sku,
        "name": name or sku,
        "provider": normalized_provider,
        "category": normalized_category_name(category, normalized_provider),
        "price": int(Decimal(str(price or 0))),
    }
    base_price = int(
        Decimal(str(payload.price_override if payload.price_override is not None else price or 0))
    )
    code = _clean_promo_code(payload.code, field_name="Kode voucher")
    promo_type = _clean_promo_type(payload.promo_type, rule_type=payload.rule_type, code=code)
    rule_type = "content" if promo_type == "banner" else "price"
    if payload.promo_type is None:
        rule_type = _clean_promo_rule_type(payload.rule_type)
        promo_type = _clean_promo_type(None, rule_type=rule_type, code=code)
    calculation_type = _clean_calculation_type(
        payload.calculation_type,
        discount_type=payload.discount_type,
        promo_type=promo_type,
    )
    discount_value: Optional[Union[Decimal, int]] = payload.discount_value
    if calculation_type == "fixed":
        discount_value = _rupiah_value(payload.discount_value, field_name="Nilai diskon")
    max_discount = _rupiah_value(payload.max_discount, field_name="Maksimum diskon")
    minimum_transaction = _rupiah_value(payload.minimum_transaction, field_name="Minimum transaksi")
    special_price = _rupiah_value(payload.special_price, field_name="Harga khusus")
    budget_limit = _rupiah_value(payload.budget_limit, field_name="Budget promo")
    customer_segment = _clean_customer_segment(payload.customer_segment)
    eligibility_explicit = "eligibility_rules" in payload.model_fields_set
    if eligibility_explicit and payload.eligibility_rules is None:
        raise HTTPException(status_code=400, detail="Aturan eligibility eksplisit tidak boleh kosong")
    eligibility_rules = (
        _canonical_eligibility_for_write(payload.eligibility_rules)
        if eligibility_explicit
        else None
    )
    method = normalize_payment_code(payload.method or "QRIS")
    allowed_methods = _validate_payment_method_codes(payload.payment_methods)
    if promo_type == "payment_method" and not allowed_methods:
        raise HTTPException(status_code=400, detail="Promo metode pembayaran wajib memilih minimal satu metode")
    rounding_rule = str(payload.rounding_rule or "none").strip().lower()
    if rounding_rule not in PROMO_ROUNDING_RULES:
        raise HTTPException(status_code=400, detail="Aturan pembulatan promo tidak dikenal")
    starts_at, ends_at, daily_start, daily_end = _validate_promo_schedule(
        payload.starts_at,
        payload.ends_at,
        payload.daily_start_time,
        payload.daily_end_time,
    )
    lifecycle_status = _clean_lifecycle_status(
        payload.lifecycle_status or payload.status,
        active=payload.active,
    )
    included_options = await _validated_relation_options(payload.target_option_ids)
    excluded_options = await _validated_relation_options(payload.excluded_target_option_ids)
    if {item["id"] for item in included_options} & {item["id"] for item in excluded_options}:
        raise HTTPException(status_code=400, detail="Target include dan exclude tidak boleh sama")
    if eligibility_rules is not None:
        rule_customer_ids = eligibility_customer_ids(eligibility_rules)
        supplied_customer_ids = sorted({int(value) for value in payload.customer_ids if int(value) > 0})
        if supplied_customer_ids and supplied_customer_ids != rule_customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Daftar customer harus sama dengan kondisi customer_id pada aturan eligibility",
            )
        customer_ids = await _validated_customer_ids(rule_customer_ids)
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Preset pelanggan tertentu wajib memiliki kondisi customer_id",
            )
    else:
        customer_ids = (
            await _validated_customer_ids(payload.customer_ids)
            if customer_segment == "specific"
            else []
        )
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(status_code=400, detail="Segment pelanggan tertentu wajib memilih akun")

    if included_options:
        target_scope, target_value = _legacy_target_from_options(included_options)
    else:
        target_scope = _clean_target_scope(payload.target_scope)
        target_value = _clean_text(payload.target_value)
    _validate_promo_target(
        target_scope=target_scope,
        target_value=target_value,
        included_options=included_options,
    )
    _validate_promo_rules(
        rule_type=rule_type,
        code=code,
        discount_type=calculation_type,
        discount_value=discount_value,
        promo_type=promo_type,
        calculation_type=calculation_type,
        special_price=special_price,
        minimum_transaction=minimum_transaction,
        max_discount=max_discount,
        usage_limit=payload.usage_limit,
        budget_limit=budget_limit,
        max_per_customer=payload.max_per_customer,
        max_per_phone=payload.max_per_phone,
        max_per_target=payload.max_per_target,
    )

    if payload.customer_id is not None:
        account_rows = await db_query(
            """
            SELECT id, COALESCE(phone, ''), COALESCE(active, 0)
            FROM customer_accounts
            WHERE id=:customer_id
            LIMIT 1
            """,
            {"customer_id": int(payload.customer_id)},
        )
        if not account_rows:
            raise HTTPException(status_code=400, detail="Akun customer simulasi tidak ditemukan")
        account_id, account_phone, account_active = account_rows[0]
        identity = PromoIdentity(
            customer_id=int(account_id),
            phone=str(account_phone or ""),
            target_id=str(payload.target_id or ""),
            is_authenticated=True,
            account_active=bool(account_active),
        )
    else:
        identity = PromoIdentity(
            phone=str(payload.customer_phone or ""),
            target_id=str(payload.target_id or ""),
            is_authenticated=False,
            account_active=False,
        )
    moment = _to_utc_datetime(payload.simulation_at) if payload.simulation_at else datetime.now(timezone.utc)
    if payload.simulation_at and moment is None:
        raise HTTPException(status_code=400, detail="Waktu simulasi tidak valid")
    eligibility_profile = await eligibility_profile_for_identity(identity, now=moment)
    history = eligibility_profile.to_safe_dict()
    success_count = eligibility_profile.successful_order_count
    history["status"] = (
        "unknown" if success_count is None else ("existing" if success_count > 0 else "new")
    )
    history["success_order_count"] = success_count
    history["has_success_order"] = eligibility_profile.has_successful_order

    structured_targets = [
        {**item, "excluded": False} for item in included_options
    ] + [
        {**item, "excluded": True} for item in excluded_options
    ]
    simulated_promo: Dict[str, Any] = {
        "id": 0,
        "title": _clean_text(payload.title) or "Simulasi promo",
        "internal_code": _clean_text(payload.internal_code) or "SIMULATION",
        "code": code,
        "promo_type": promo_type,
        "rule_type": rule_type,
        # A draft must still be previewable. Simulation evaluates its rules as
        # active while retaining the configured lifecycle in diagnostics.
        "lifecycle_status": "active",
        "active": 1,
        "calculation_type": calculation_type,
        "discount_type": calculation_type,
        "discount_value": str(discount_value or 0),
        "max_discount": max_discount or 0,
        "minimum_transaction": minimum_transaction or 0,
        "special_price": special_price,
        "rounding_rule": rounding_rule,
        "usage_limit": int(payload.usage_limit or 0),
        "usage_count": 0,
        "quota_daily": int(payload.quota_daily or 0),
        "daily_usage_count": 0,
        "payment_methods": allowed_methods,
        "budget_limit": budget_limit or 0,
        "discount_spent": 0,
        "max_per_customer": int(payload.max_per_customer or 0),
        "max_per_customer_daily": int(payload.max_per_customer_daily or 0),
        "max_per_phone": int(payload.max_per_phone or 0),
        "max_per_target": int(payload.max_per_target or 0),
        "customer_usage_count": 0,
        "customer_daily_usage_count": 0,
        "phone_usage_count": 0,
        "target_usage_count": 0,
        "eligibility_rules": eligibility_rules,
        "customer_segment": customer_segment,
        "customer_targets": customer_ids,
        "target_scope": target_scope,
        "target_value": target_value,
        "targets": structured_targets,
        "stackable": int(payload.stackable or 0),
        "exclusive": int(payload.exclusive or 0),
        "max_promotions_per_order": int(payload.max_promotions_per_order or 1),
        "priority": int(payload.priority or 0),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": _clean_timezone(payload.timezone),
        "active_days": _clean_active_days(payload.active_days),
        "daily_start_time": daily_start,
        "daily_end_time": daily_end,
        "legacy_compatible": False,
        "rules_version": "v3" if eligibility_rules is not None else "v2",
    }
    requested_code = normalize_voucher_code(payload.voucher_code if payload.voucher_code is not None else code)
    context = build_context(
        product,
        base_price=base_price,
        promo_code=requested_code or None,
        payment_method=method,
        customer_segment=str(history["status"]),
        identity=identity,
        eligibility_profile=eligibility_profile,
        now=moment,
    )
    eligibility_evaluation = evaluate_promotion_eligibility(simulated_promo, context)
    quote = quote_promotions([simulated_promo], context)
    eligible = bool(quote.applied) and (quote.code_valid or not requested_code)
    internal_reason = None
    if not eligible and quote.rejections:
        internal_reason = str(quote.rejections[0].get("reason") or "promo_not_applicable")
    failure = code_failure_payload(quote) if internal_reason else None
    payment_fee, fee_source = await _simulate_payment_fee(quote.final_price, method)
    segment_reason_codes = {
        "member_login_required",
        "account_inactive",
        "guest_only",
        "new_customer_only",
        "existing_customer_only",
        "customer_not_targeted",
        "customer_not_selected",
        "customer_identity_required",
        "customer_segment",
        "eligibility_rule_not_met",
        "account_too_old",
        "first_purchase_only",
        "min_successful_orders_not_met",
        "min_successful_spend_not_met",
        "invalid_eligibility_rule",
    }
    segment_match: Optional[bool]
    if eligible:
        segment_match = True
    elif internal_reason in segment_reason_codes:
        segment_match = False
    else:
        segment_match = None
    effective_simulation_rules = eligibility_rules or legacy_eligibility_rules(customer_segment, customer_ids)
    configured_customer_targets = eligibility_customer_ids(effective_simulation_rules)
    specific_required = bool(configured_customer_targets)
    specific_match = (
        bool(identity.customer_id and identity.customer_id in set(configured_customer_targets))
        if specific_required
        else None
    )
    segment_meta = _customer_segment_metadata(customer_segment, target_count=len(customer_ids))
    return {
        "success": True,
        "eligible": eligible,
        "reason": "Promo berlaku." if eligible else str((failure or {}).get("message") or "Promo tidak berlaku."),
        "reason_code": None if eligible else (failure or {}).get("reason_code"),
        "internal_reason": internal_reason,
        "product": product,
        "method": "FREE_PROMO" if quote.final_price <= 0 else method,
        "base_price": quote.base_price,
        "discount_amount": quote.discount_amount,
        "final_price": quote.final_price,
        "payment_fee": int(payment_fee),
        "total": int(quote.final_price + payment_fee),
        "usage_count": 0,
        "usage_limit": int(payload.usage_limit or 0),
        "discount_spent": 0,
        "budget_limit": int(budget_limit or 0),
        "budget_remaining": int(budget_limit or 0) if budget_limit else None,
        "fee_source": fee_source,
        "conditions": list(eligibility_evaluation.conditions),
        "quote": quote.to_dict(include_rejections=True),
        "diagnostics": {
            "configured_lifecycle": lifecycle_status,
            "customer_segment": customer_segment,
            "customer_segment_label": segment_meta["label"],
            "segment_match": segment_match,
            "identity": {
                "status": identity.account_status,
                "is_authenticated": bool(identity.is_authenticated),
                "account_active": bool(identity.account_active),
                "customer_id": identity.customer_id,
                "phone_present": bool(identity.phone),
                "target_id_present": bool(identity.target_id),
            },
            "history": history,
            "eligibility": eligibility_evaluation.to_dict(),
            "specific_target": {
                "required": specific_required,
                "matched": specific_match,
                "selected_count": len(configured_customer_targets),
            },
        },
    }


@router.post("/api/promos")
async def create_promo(
    payload: PromoCreateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    title = _clean_text(payload.title)
    if not title:
        raise HTTPException(status_code=400, detail="Nama promo wajib diisi")

    internal_code = _clean_promo_code(
        payload.internal_code or f"PRM-{uuid4().hex[:10]}", field_name="Kode internal"
    )
    code = _clean_promo_code(payload.code, field_name="Kode voucher")
    promo_type = _clean_promo_type(payload.promo_type, rule_type=payload.rule_type, code=code)
    rule_type = "content" if promo_type == "banner" else "price"
    if payload.promo_type is None:
        rule_type = _clean_promo_rule_type(payload.rule_type)
        promo_type = _clean_promo_type(None, rule_type=rule_type, code=code)
    calculation_type = _clean_calculation_type(
        payload.calculation_type,
        discount_type=payload.discount_type,
        promo_type=promo_type,
    )
    discount_type = calculation_type
    discount_value: Optional[Union[Decimal, int]] = payload.discount_value
    if calculation_type == "fixed":
        discount_value = _rupiah_value(payload.discount_value, field_name="Nilai diskon")
    max_discount = _rupiah_value(payload.max_discount, field_name="Maksimum diskon")
    minimum_transaction = _rupiah_value(payload.minimum_transaction, field_name="Minimum transaksi")
    special_price = _rupiah_value(payload.special_price, field_name="Harga khusus")
    budget_limit = _rupiah_value(payload.budget_limit, field_name="Budget promo")
    if promo_type == "banner":
        discount_value = None
        max_discount = None
        minimum_transaction = None
        special_price = None
    lifecycle_status = _clean_lifecycle_status(
        payload.lifecycle_status or payload.status,
        active=payload.active,
    )
    active = 1 if lifecycle_status == "active" else 0
    starts_at, ends_at, daily_start, daily_end = _validate_promo_schedule(
        payload.starts_at,
        payload.ends_at,
        payload.daily_start_time,
        payload.daily_end_time,
    )
    timezone_name = _clean_timezone(payload.timezone)
    active_days = _clean_active_days(payload.active_days)
    placements = _clean_placements(payload.placements)
    cta_url = _validate_promo_cta(payload.cta_url, allow_external=bool(payload.allow_external_cta))
    rounding_rule = str(payload.rounding_rule or "none").strip().lower()
    if rounding_rule not in PROMO_ROUNDING_RULES:
        raise HTTPException(status_code=400, detail="Aturan pembulatan promo tidak dikenal")
    customer_segment = _clean_customer_segment(payload.customer_segment)
    eligibility_explicit = "eligibility_rules" in payload.model_fields_set
    if eligibility_explicit and payload.eligibility_rules is None:
        raise HTTPException(status_code=400, detail="Aturan eligibility eksplisit tidak boleh kosong")
    eligibility_rules = (
        _canonical_eligibility_for_write(payload.eligibility_rules)
        if eligibility_explicit
        else None
    )
    payment_methods_list = _validate_payment_method_codes(payload.payment_methods)
    if promo_type == "payment_method" and not payment_methods_list:
        raise HTTPException(status_code=400, detail="Promo metode pembayaran wajib memilih minimal satu metode")

    included_options = await _validated_relation_options(payload.target_option_ids)
    excluded_options = await _validated_relation_options(payload.excluded_target_option_ids)
    if {item["id"] for item in included_options} & {item["id"] for item in excluded_options}:
        raise HTTPException(status_code=400, detail="Target include dan exclude tidak boleh sama")
    if eligibility_rules is not None:
        rule_customer_ids = eligibility_customer_ids(eligibility_rules)
        supplied_customer_ids = sorted({int(value) for value in payload.customer_ids if int(value) > 0})
        if supplied_customer_ids and supplied_customer_ids != rule_customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Daftar customer harus sama dengan kondisi customer_id pada aturan eligibility",
            )
        customer_ids = await _validated_customer_ids(rule_customer_ids)
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Preset pelanggan tertentu wajib memiliki kondisi customer_id",
            )
    else:
        customer_ids = (
            await _validated_customer_ids(payload.customer_ids)
            if customer_segment == "specific"
            else []
        )
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(status_code=400, detail="Segment pelanggan tertentu wajib memilih akun")

    if included_options:
        target_scope, target_value = _legacy_target_from_options(included_options)
        legacy_compatible = 0
    else:
        target_scope = _clean_target_scope(payload.target_scope)
        target_value = _clean_text(payload.target_value)
        legacy_compatible = 0 if excluded_options else 1

    _validate_promo_target(
        target_scope=target_scope,
        target_value=target_value,
        included_options=included_options,
    )

    _validate_promo_rules(
        rule_type=rule_type,
        code=code,
        discount_type=discount_type,
        discount_value=discount_value,
        promo_type=promo_type,
        calculation_type=calculation_type,
        special_price=special_price,
        minimum_transaction=minimum_transaction,
        max_discount=max_discount,
        usage_limit=payload.usage_limit,
        budget_limit=budget_limit,
        max_per_customer=payload.max_per_customer,
        max_per_phone=payload.max_per_phone,
        max_per_target=payload.max_per_target,
    )
    customer_description = _clean_text(payload.customer_description) or _clean_text(payload.description)
    actor = admin.get("username") or admin.get("sub") or "admin"
    values: Dict[str, Any] = {
        "title": title,
        "internal_code": internal_code,
        "code": code,
        "promo_type": promo_type,
        "lifecycle_status": lifecycle_status,
        "description": customer_description,
        "internal_description": _clean_text(payload.internal_description),
        "customer_description": customer_description,
        "admin_notes": _clean_text(payload.admin_notes),
        "badge": _clean_text(payload.badge),
        "cta_text": _clean_text(payload.cta_text),
        "cta_url": cta_url,
        "image_url": _clean_text(payload.image_url),
        "rule_type": rule_type,
        "target_scope": target_scope,
        "target_value": target_value,
        "discount_type": discount_type,
        "calculation_type": calculation_type,
        "discount_value": str(discount_value) if isinstance(discount_value, Decimal) else discount_value,
        "max_discount": max_discount,
        "minimum_transaction": minimum_transaction,
        "special_price": special_price,
        "rounding_rule": rounding_rule,
        "usage_limit": int(payload.usage_limit or 0),
        "quota_daily": int(payload.quota_daily or 0),
        "payment_methods": json.dumps(payment_methods_list),
        "budget_limit": budget_limit,
        "max_per_customer": int(payload.max_per_customer or 0),
        "max_per_customer_daily": int(payload.max_per_customer_daily or 0),
        "max_per_phone": int(payload.max_per_phone or 0),
        "max_per_target": int(payload.max_per_target or 0),
        "eligibility_rules": _eligibility_json(eligibility_rules) if eligibility_rules is not None else None,
        "customer_segment": customer_segment,
        "stackable": int(payload.stackable or 0),
        "exclusive": int(payload.exclusive or 0),
        "max_promotions_per_order": int(payload.max_promotions_per_order or 1),
        "priority": int(payload.priority or 0),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": timezone_name,
        "active_days": json.dumps(active_days),
        "daily_start_time": daily_start,
        "daily_end_time": daily_end,
        "show_on_website": int(payload.show_on_website or 0),
        "placements": json.dumps(placements),
        "display_order": int(payload.display_order or 0),
        "allow_external_cta": int(payload.allow_external_cta or 0),
        "active": active,
        "rules_version": "v3" if eligibility_rules is not None else "v2",
        "legacy_compatible": 0 if eligibility_rules is not None else legacy_compatible,
        "created_by": actor,
        "updated_by": actor,
    }
    async with db_transaction() as connection:
        await _ensure_unique_promo_codes(
            internal_code=internal_code,
            voucher_code=code,
            connection=connection,
        )
        await db_execute(
            """
            INSERT INTO promos (
                title, internal_code, code, promo_type, lifecycle_status,
                description, internal_description, customer_description, admin_notes,
                badge, cta_text, cta_url, image_url, rule_type, target_scope, target_value,
                discount_type, calculation_type, discount_value, max_discount,
                minimum_transaction, special_price, rounding_rule,
                usage_limit, quota_daily, payment_methods, budget_limit,
                max_per_customer, max_per_customer_daily, max_per_phone, max_per_target,
                eligibility_rules, customer_segment, stackable, exclusive, max_promotions_per_order, priority,
                starts_at, ends_at, timezone, active_days, daily_start_time, daily_end_time,
                show_on_website, placements, display_order, allow_external_cta, active,
                rules_version, legacy_compatible, created_by, updated_by
            ) VALUES (
                :title, :internal_code, :code, :promo_type, :lifecycle_status,
                :description, :internal_description, :customer_description, :admin_notes,
                :badge, :cta_text, :cta_url, :image_url, :rule_type, :target_scope, :target_value,
                :discount_type, :calculation_type, :discount_value, :max_discount,
                :minimum_transaction, :special_price, :rounding_rule,
                :usage_limit, :quota_daily, :payment_methods, :budget_limit,
                :max_per_customer, :max_per_customer_daily, :max_per_phone, :max_per_target,
                :eligibility_rules, :customer_segment, :stackable, :exclusive, :max_promotions_per_order, :priority,
                :starts_at, :ends_at, :timezone, :active_days, :daily_start_time, :daily_end_time,
                :show_on_website, :placements, :display_order, :allow_external_cta, :active,
                :rules_version, :legacy_compatible, :created_by, :updated_by
            )
            """,
            values,
            connection=connection,
        )
        created_rows = await db_query(
            "SELECT id FROM promos WHERE internal_code=:code",
            {"code": internal_code},
            connection=connection,
        )
        if not created_rows:
            raise HTTPException(status_code=500, detail="Promo tersimpan tetapi ID tidak dapat dibaca")
        promo_id = int(created_rows[0][0])
        await _replace_promo_relations(
            promo_id,
            included_ids=[item["id"] for item in included_options],
            excluded_ids=[item["id"] for item in excluded_options],
            customer_ids=customer_ids,
            connection=connection,
        )
        await _write_audit_log(
            admin,
            action="promo.create",
            entity_type="promo",
            entity_id=promo_id,
            after={
                **values,
                "eligibility_rules": _eligibility_for_audit(eligibility_rules),
                "target_option_ids": payload.target_option_ids,
                "excluded_target_option_ids": payload.excluded_target_option_ids,
                "customer_ids": customer_ids,
            },
            connection=connection,
        )
    return {"success": True, "id": promo_id, "status": lifecycle_status, "message": "Promo berhasil disimpan"}


@router.put("/api/promos/{promo_id}")
async def update_promo(
    promo_id: int,
    payload: PromoUpdateRequest,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")
    promos = await get_promos(admin)
    existing = next((item for item in promos if int(item["id"]) == promo_id), None)
    if not existing:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")

    create_fields = set(PromoCreateRequest.model_fields)
    base = {
        field: existing.get(field)
        for field in create_fields
        if field in existing and field != "eligibility_rules"
    }
    base.update(
        {
            "title": existing.get("title"),
            "target_option_ids": existing.get("target_option_ids", []),
            "excluded_target_option_ids": existing.get("excluded_target_option_ids", []),
            "customer_ids": existing.get("customer_ids", []),
            "active": 1 if existing.get("active") else 0,
        }
    )
    incoming = payload.model_dump(exclude_unset=True)
    base.update(incoming)
    if "eligibility_rules" not in payload.model_fields_set:
        # The effective legacy adapter is response-only. An unrelated update
        # must not silently persist it or rewrite an existing explicit rule.
        base["eligibility_rules"] = None
    if "active" in incoming and not ({"status", "lifecycle_status"} & payload.model_fields_set):
        base["lifecycle_status"] = "active" if bool(incoming["active"]) else "disabled"
    if "status" in incoming and "lifecycle_status" not in incoming:
        base["lifecycle_status"] = incoming["status"]
    try:
        candidate = PromoCreateRequest(**base)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False),
        ) from exc

    title = _clean_text(candidate.title)
    if not title:
        raise HTTPException(status_code=400, detail="Nama promo wajib diisi")
    internal_code = _clean_promo_code(candidate.internal_code, field_name="Kode internal")
    code = _clean_promo_code(candidate.code, field_name="Kode voucher")
    promo_type = _clean_promo_type(candidate.promo_type, rule_type=candidate.rule_type, code=code)
    rule_type = "content" if promo_type == "banner" else "price"
    calculation_type = _clean_calculation_type(
        candidate.calculation_type,
        discount_type=candidate.discount_type,
        promo_type=promo_type,
    )
    discount_value: Optional[Union[Decimal, int]] = candidate.discount_value
    if calculation_type == "fixed":
        discount_value = _rupiah_value(candidate.discount_value, field_name="Nilai diskon")
    max_discount = _rupiah_value(candidate.max_discount, field_name="Maksimum diskon")
    minimum_transaction = _rupiah_value(candidate.minimum_transaction, field_name="Minimum transaksi")
    special_price = _rupiah_value(candidate.special_price, field_name="Harga khusus")
    budget_limit = _rupiah_value(candidate.budget_limit, field_name="Budget promo")
    if promo_type == "banner":
        discount_value = None
        max_discount = None
        minimum_transaction = None
        special_price = None
    lifecycle_status = _clean_lifecycle_status(candidate.lifecycle_status or candidate.status, active=candidate.active)
    active = 1 if lifecycle_status == "active" else 0
    starts_at, ends_at, daily_start, daily_end = _validate_promo_schedule(
        candidate.starts_at,
        candidate.ends_at,
        candidate.daily_start_time,
        candidate.daily_end_time,
    )
    timezone_name = _clean_timezone(candidate.timezone)
    active_days = _clean_active_days(candidate.active_days)
    placements = _clean_placements(candidate.placements)
    cta_url = _validate_promo_cta(candidate.cta_url, allow_external=bool(candidate.allow_external_cta))
    rounding_rule = str(candidate.rounding_rule or "none").strip().lower()
    if rounding_rule not in PROMO_ROUNDING_RULES:
        raise HTTPException(status_code=400, detail="Aturan pembulatan promo tidak dikenal")
    customer_segment = _clean_customer_segment(candidate.customer_segment)
    eligibility_changed = "eligibility_rules" in payload.model_fields_set
    if eligibility_changed and candidate.eligibility_rules is None:
        raise HTTPException(status_code=400, detail="Aturan eligibility eksplisit tidak boleh kosong")
    eligibility_rules = (
        _canonical_eligibility_for_write(candidate.eligibility_rules)
        if eligibility_changed
        else (
            existing.get("eligibility_rules")
            if existing.get("eligibility_source") == "explicit"
            else None
        )
    )
    eligibility_storage = (
        _eligibility_json(eligibility_rules)
        if eligibility_changed and eligibility_rules is not None
        else existing.get("eligibility_rules_storage")
    )
    payment_methods_changed = "payment_methods" in payload.model_fields_set
    promo_type_changed = "promo_type" in payload.model_fields_set
    payment_methods = (
        _validate_payment_method_codes(candidate.payment_methods)
        if payment_methods_changed or promo_type_changed
        else _normalize_payment_methods(candidate.payment_methods)
    )
    if promo_type == "payment_method" and not payment_methods:
        raise HTTPException(status_code=400, detail="Promo metode pembayaran wajib memilih minimal satu metode")

    include_changed = "target_option_ids" in payload.model_fields_set
    exclude_changed = "excluded_target_option_ids" in payload.model_fields_set
    customer_changed = "customer_ids" in payload.model_fields_set
    included_options = await _validated_relation_options(candidate.target_option_ids)
    excluded_options = await _validated_relation_options(candidate.excluded_target_option_ids)
    if {item["id"] for item in included_options} & {item["id"] for item in excluded_options}:
        raise HTTPException(status_code=400, detail="Target include dan exclude tidak boleh sama")
    segment_changed = (
        "customer_segment" in payload.model_fields_set
        and customer_segment != _clean_customer_segment(existing.get("customer_segment"), strict=False)
    )
    if eligibility_changed and eligibility_rules is not None:
        rule_customer_ids = eligibility_customer_ids(eligibility_rules)
        supplied_customer_ids = sorted({int(value) for value in candidate.customer_ids if int(value) > 0})
        if "customer_ids" in payload.model_fields_set and supplied_customer_ids != rule_customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Daftar customer harus sama dengan kondisi customer_id pada aturan eligibility",
            )
        customer_ids = await _validated_customer_ids(rule_customer_ids)
        customer_changed = True
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(
                status_code=400,
                detail="Preset pelanggan tertentu wajib memiliki kondisi customer_id",
            )
    elif existing.get("eligibility_rules_storage") is not None:
        # Cached/legacy admin clients do not know the new field. Preserve the
        # relation mirror and explicit rule when they update unrelated fields.
        customer_ids = list(existing.get("customer_ids") or [])
        customer_changed = False
    else:
        customer_ids = (
            await _validated_customer_ids(candidate.customer_ids)
            if customer_segment == "specific"
            else []
        )
        if customer_segment == "specific" and segment_changed and not customer_changed:
            raise HTTPException(
                status_code=400,
                detail="Pilih ulang akun target saat mengubah segment menjadi pelanggan tertentu",
            )
        if customer_segment == "specific" and not customer_ids:
            raise HTTPException(status_code=400, detail="Segment pelanggan tertentu wajib memilih akun")
        if customer_segment != "specific":
            customer_changed = True
    if included_options:
        target_scope, target_value = _legacy_target_from_options(included_options)
    else:
        target_scope = _clean_target_scope(candidate.target_scope)
        target_value = _clean_text(candidate.target_value)

    _validate_promo_target(
        target_scope=target_scope,
        target_value=target_value,
        included_options=included_options,
    )

    _validate_promo_rules(
        rule_type=rule_type,
        code=code,
        discount_type=calculation_type,
        discount_value=discount_value,
        promo_type=promo_type,
        calculation_type=calculation_type,
        special_price=special_price,
        minimum_transaction=minimum_transaction,
        max_discount=max_discount,
        usage_limit=candidate.usage_limit,
        budget_limit=budget_limit,
        max_per_customer=candidate.max_per_customer,
        max_per_phone=candidate.max_per_phone,
        max_per_target=candidate.max_per_target,
    )
    structured_fields = {
        "internal_code", "promo_type", "lifecycle_status", "status", "internal_description",
        "customer_description", "admin_notes", "calculation_type", "minimum_transaction",
        "special_price", "rounding_rule", "quota_daily", "max_per_customer_daily",
        "eligibility_rules", "customer_segment", "customer_ids", "exclusive", "max_promotions_per_order",
        "timezone", "active_days", "daily_start_time", "daily_end_time", "placements",
        "display_order", "allow_external_cta", "target_option_ids", "excluded_target_option_ids",
    }
    converted_to_v2 = bool(structured_fields & payload.model_fields_set)
    legacy_compatible = (
        0
        if eligibility_changed or converted_to_v2
        else int(bool(existing.get("legacy_compatible", True)))
    )
    if eligibility_changed:
        rules_version = "v3"
    elif existing.get("eligibility_rules_storage") is not None:
        rules_version = existing.get("rules_version", "v3")
    else:
        rules_version = "v2" if converted_to_v2 else existing.get("rules_version", "legacy_v1")
    actor = admin.get("username") or admin.get("sub") or "admin"
    customer_description = _clean_text(candidate.customer_description) or _clean_text(candidate.description)
    values: Dict[str, Any] = {
        "promo_id": promo_id,
        "title": title,
        "internal_code": internal_code,
        "code": code,
        "promo_type": promo_type,
        "lifecycle_status": lifecycle_status,
        "description": customer_description,
        "internal_description": _clean_text(candidate.internal_description),
        "customer_description": customer_description,
        "admin_notes": _clean_text(candidate.admin_notes),
        "badge": _clean_text(candidate.badge),
        "cta_text": _clean_text(candidate.cta_text),
        "cta_url": cta_url,
        "image_url": _clean_text(candidate.image_url),
        "rule_type": rule_type,
        "target_scope": target_scope,
        "target_value": target_value,
        "discount_type": calculation_type,
        "calculation_type": calculation_type,
        "discount_value": str(discount_value) if isinstance(discount_value, Decimal) else discount_value,
        "max_discount": max_discount,
        "minimum_transaction": minimum_transaction,
        "special_price": special_price,
        "rounding_rule": rounding_rule,
        "usage_limit": int(candidate.usage_limit or 0),
        "quota_daily": int(candidate.quota_daily or 0),
        "payment_methods": json.dumps(payment_methods),
        "budget_limit": budget_limit,
        "max_per_customer": int(candidate.max_per_customer or 0),
        "max_per_customer_daily": int(candidate.max_per_customer_daily or 0),
        "max_per_phone": int(candidate.max_per_phone or 0),
        "max_per_target": int(candidate.max_per_target or 0),
        "eligibility_rules": eligibility_storage,
        "customer_segment": customer_segment,
        "stackable": int(candidate.stackable or 0),
        "exclusive": int(candidate.exclusive or 0),
        "max_promotions_per_order": int(candidate.max_promotions_per_order or 1),
        "priority": int(candidate.priority or 0),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": timezone_name,
        "active_days": json.dumps(active_days),
        "daily_start_time": daily_start,
        "daily_end_time": daily_end,
        "show_on_website": int(candidate.show_on_website or 0),
        "placements": json.dumps(placements),
        "display_order": int(candidate.display_order or 0),
        "allow_external_cta": int(candidate.allow_external_cta or 0),
        "active": active,
        "rules_version": rules_version,
        "legacy_compatible": legacy_compatible,
        "updated_by": actor,
        "paused_at": datetime.now(timezone.utc).isoformat() if lifecycle_status == "paused" else None,
        "ended_at": datetime.now(timezone.utc).isoformat() if lifecycle_status == "ended" else None,
    }
    assignments = [f"{key}=:{key}" for key in values if key != "promo_id"]
    async with db_transaction() as connection:
        await _ensure_unique_promo_codes(
            internal_code=internal_code,
            voucher_code=code,
            exclude_id=promo_id,
            connection=connection,
        )
        await db_execute(
            f"UPDATE promos SET {', '.join(assignments)}, updated_at=CURRENT_TIMESTAMP WHERE id=:promo_id",
            values,
            connection=connection,
        )
        await _replace_promo_relations(
            promo_id,
            included_ids=[item["id"] for item in included_options] if include_changed or exclude_changed else None,
            excluded_ids=[item["id"] for item in excluded_options] if include_changed or exclude_changed else None,
            customer_ids=customer_ids if customer_changed else None,
            connection=connection,
        )
        await _write_audit_log(
            admin,
            action="promo.update",
            entity_type="promo",
            entity_id=promo_id,
            before=existing,
            after={
                **values,
                "eligibility_rules": _eligibility_for_audit(eligibility_rules),
                "target_option_ids": [item["id"] for item in included_options],
                "excluded_target_option_ids": [item["id"] for item in excluded_options],
                "customer_ids": customer_ids,
            },
            connection=connection,
        )
    return {"success": True, "status": lifecycle_status, "message": "Promo berhasil diperbarui"}


@router.delete("/api/promos/{promo_id}")
async def delete_promo(
    promo_id: int,
    admin: Dict[str, Any] = Depends(_require_permission("promos:manage")),
) -> Dict[str, Any]:
    promos = await get_promos(admin)
    existing = next((item for item in promos if int(item["id"]) == promo_id), None)
    if not existing:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")
    async with db_transaction() as connection:
        await db_execute(
            """
            UPDATE promos
            SET active=0,
                lifecycle_status='archived',
                archived_at=CURRENT_TIMESTAMP,
                updated_by=:actor,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:promo_id
            """,
            {"promo_id": promo_id, "actor": admin.get("username") or "admin"},
            connection=connection,
        )
        await _write_audit_log(
            admin,
            action="promo.archive",
            entity_type="promo",
            entity_id=promo_id,
            before=existing,
            after={"lifecycle_status": "archived", "active": False},
            connection=connection,
        )
    return {"success": True, "archived": True, "message": "Promo diarsipkan; histori tetap tersimpan"}
