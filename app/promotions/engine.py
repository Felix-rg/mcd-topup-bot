"""Pure, deterministic promotion calculation using integer Rupiah values."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Jakarta"
ACTIVE_REDEMPTION_STATUSES = {"RESERVED", "REDEEMED"}
TERMINAL_LIFECYCLE_STATUSES = {"DRAFT", "PAUSED", "ENDED", "DISABLED", "ARCHIVED"}

ELIGIBILITY_RULES_VERSION = 1
ELIGIBILITY_MAX_CONDITIONS = 20
ELIGIBILITY_MAX_BYTES = 16 * 1024
ELIGIBILITY_GROUP_OPERATORS = ("all", "any")
ELIGIBILITY_COMPARISON_OPERATORS = (
    "equals",
    "not_equals",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "in",
    "not_in",
    "is_true",
    "is_false",
    "between",
)

_ENUM_OPERATORS = ("equals", "not_equals", "in", "not_in")
_NUMBER_OPERATORS = (
    "equals",
    "not_equals",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "between",
)
_BOOLEAN_OPERATORS = ("equals", "not_equals", "is_true", "is_false")
_IDENTIFIER_OPERATORS = ("equals", "not_equals", "in", "not_in")

# This is the only allowlist used by validation, evaluation, and admin metadata.
# It deliberately contains business fields rather than database column names.
ELIGIBILITY_FIELD_DEFINITIONS: dict[str, dict[str, Any]] = {
    "authentication_status": {
        "label": "Status autentikasi",
        "type": "enum",
        "operators": _ENUM_OPERATORS,
        "options": (
            {"value": "guest", "label": "Guest"},
            {"value": "member", "label": "Member"},
        ),
    },
    "account_status": {
        "label": "Status akun",
        "type": "enum",
        "operators": _ENUM_OPERATORS,
        "options": (
            {"value": "active", "label": "Aktif"},
            {"value": "inactive", "label": "Nonaktif"},
        ),
    },
    "has_customer_account": {
        "label": "Memiliki akun customer",
        "type": "boolean",
        "operators": _BOOLEAN_OPERATORS,
    },
    "account_age_days": {
        "label": "Umur akun (hari)",
        "type": "integer",
        "operators": _NUMBER_OPERATORS,
        "minimum": 0,
    },
    "account_created_at": {
        "label": "Tanggal akun dibuat",
        "type": "datetime",
        "operators": _NUMBER_OPERATORS,
    },
    "successful_order_count": {
        "label": "Jumlah transaksi berhasil",
        "type": "integer",
        "operators": _NUMBER_OPERATORS,
        "minimum": 0,
    },
    "successful_order_total": {
        "label": "Total pembelian berhasil",
        "type": "rupiah",
        "operators": _NUMBER_OPERATORS,
        "minimum": 0,
    },
    "days_since_last_successful_order": {
        "label": "Hari sejak transaksi berhasil terakhir",
        "type": "integer",
        "operators": _NUMBER_OPERATORS,
        "minimum": 0,
    },
    "has_successful_order": {
        "label": "Pernah transaksi berhasil",
        "type": "boolean",
        "operators": _BOOLEAN_OPERATORS,
    },
    "customer_id": {
        "label": "ID akun customer",
        "type": "customer_id",
        "operators": _IDENTIFIER_OPERATORS,
        "minimum": 1,
    },
    "normalized_phone": {
        "label": "Nomor WhatsApp",
        "type": "phone",
        "operators": _IDENTIFIER_OPERATORS,
    },
    "target_id": {
        "label": "Target ID",
        "type": "target_id",
        "operators": _IDENTIFIER_OPERATORS,
    },
}


def decimal_value(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convert provider/database values without passing through binary float."""

    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def rupiah(value: Any, *, rounding: str = "floor") -> int:
    numeric = decimal_value(value)
    mode = str(rounding or "floor").strip().lower()
    decimal_rounding = ROUND_CEILING if mode in {"ceil", "ceiling", "up"} else ROUND_FLOOR
    if mode in {"half_up", "nearest", "round"}:
        decimal_rounding = ROUND_HALF_UP
    return max(int(numeric.quantize(Decimal("1"), rounding=decimal_rounding)), 0)


def truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "active"}


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(decimal_value(value))
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_voucher_code(value: Any) -> str:
    """Return the canonical customer-facing voucher code."""

    return str(value or "").strip().upper()


def normalize_code(value: Any) -> str:
    """Backward-compatible alias used by the promotion service."""

    return normalize_voucher_code(value)


def normalize_payment_code(value: Any) -> str:
    """Normalize a provider payment code without treating its label as a code."""

    return str(value or "").strip().upper()


def normalize_sku(value: Any) -> str:
    """Normalize an SKU while preserving meaningful punctuation."""

    return str(value or "").strip().casefold()


def normalize_token(value: Any) -> str:
    return "".join(char for char in str(value or "").strip().casefold() if char.isalnum())


def normalize_phone(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("62"):
        return digits
    if digits.startswith("0"):
        return f"62{digits[1:]}"
    return digits


def normalize_target_id(value: Any) -> str:
    return str(value or "").strip().casefold()


def parse_json_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed
    return [part.strip() for part in re.split(r"[,;\n]+", str(value)) if part.strip()]


def _zone(value: Any) -> timezone | ZoneInfo:
    name = str(value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    if name == "Asia/Jakarta":
        return timezone(timedelta(hours=7), name="WIB")
    try:
        return ZoneInfo(name)
    except Exception:
        if DEFAULT_TIMEZONE == "Asia/Jakarta":
            return timezone(timedelta(hours=7), name="WIB")
        try:
            return ZoneInfo(DEFAULT_TIMEZONE)
        except Exception:
            return timezone.utc


def parse_datetime(value: Any, zone_name: Any = DEFAULT_TIMEZONE) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zone(zone_name))
    return parsed.astimezone(timezone.utc)


def _canonical_eligibility_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Nilai tanggal eligibility tidak valid") from exc
    else:
        raise ValueError("Nilai tanggal eligibility harus berupa ISO datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Nilai tanggal eligibility wajib memiliki timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _eligibility_scalar(field_name: str, value: Any) -> Any:
    definition = ELIGIBILITY_FIELD_DEFINITIONS[field_name]
    value_type = definition["type"]
    if value_type == "enum":
        if not isinstance(value, str):
            raise ValueError(f"Nilai {field_name} harus berupa pilihan teks")
        canonical = value.strip().casefold()
        choices = {str(item["value"]) for item in definition.get("options", ())}
        if canonical not in choices:
            raise ValueError(f"Nilai {field_name} tidak dikenal")
        return canonical
    if value_type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"Nilai {field_name} harus berupa boolean")
        return value
    if value_type in {"integer", "rupiah", "customer_id"}:
        if type(value) is not int:
            raise ValueError(f"Nilai {field_name} harus berupa integer")
        minimum = int(definition.get("minimum", 0))
        if value < minimum or value > 9_223_372_036_854_775_807:
            raise ValueError(f"Nilai {field_name} di luar batas")
        return value
    if value_type == "datetime":
        return _canonical_eligibility_datetime(value)
    if value_type == "phone":
        if not isinstance(value, str):
            raise ValueError("Nilai normalized_phone harus berupa teks")
        canonical = normalize_phone(value)
        if len(canonical) < 6 or len(canonical) > 20:
            raise ValueError("Nomor WhatsApp eligibility tidak valid")
        return canonical
    if value_type == "target_id":
        if not isinstance(value, str):
            raise ValueError("Nilai target_id harus berupa teks")
        canonical = normalize_target_id(value)
        if not canonical or len(canonical) > 255:
            raise ValueError("Target ID eligibility tidak valid")
        return canonical
    raise ValueError(f"Tipe field eligibility {field_name} tidak didukung")


def canonicalize_eligibility_rules(raw: Any) -> dict[str, Any]:
    """Validate untrusted rule JSON and return its deterministic representation."""

    if raw is None:
        raise ValueError("eligibility_rules tidak boleh null")
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError("eligibility_rules tidak boleh kosong")
        if len(raw.encode("utf-8")) > ELIGIBILITY_MAX_BYTES:
            raise ValueError("eligibility_rules melebihi batas ukuran")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("eligibility_rules bukan JSON yang valid") from exc
    elif isinstance(raw, Mapping):
        parsed = dict(raw)
        try:
            encoded = json.dumps(parsed, ensure_ascii=False, default=str, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("eligibility_rules tidak dapat diserialisasi") from exc
        if len(encoded.encode("utf-8")) > ELIGIBILITY_MAX_BYTES:
            raise ValueError("eligibility_rules melebihi batas ukuran")
    else:
        raise ValueError("eligibility_rules harus berupa object JSON")

    if not isinstance(parsed, dict) or set(parsed) != {"version", "operator", "conditions"}:
        raise ValueError("Schema eligibility_rules tidak dikenal")
    if type(parsed.get("version")) is not int or parsed["version"] != ELIGIBILITY_RULES_VERSION:
        raise ValueError("Versi eligibility_rules tidak didukung")
    group_operator = str(parsed.get("operator") or "").strip().casefold()
    if group_operator not in ELIGIBILITY_GROUP_OPERATORS:
        raise ValueError("Operator grup eligibility tidak dikenal")
    conditions = parsed.get("conditions")
    if not isinstance(conditions, list):
        raise ValueError("conditions eligibility harus berupa array")
    if len(conditions) > ELIGIBILITY_MAX_CONDITIONS:
        raise ValueError("Jumlah kondisi eligibility melebihi batas")
    if group_operator == "any" and not conditions:
        raise ValueError("Operator any memerlukan minimal satu kondisi")

    canonical_conditions: list[dict[str, Any]] = []
    for index, condition in enumerate(conditions):
        if not isinstance(condition, Mapping) or set(condition) != {"field", "operator", "value"}:
            raise ValueError(f"Schema kondisi eligibility ke-{index + 1} tidak valid")
        field_name = str(condition.get("field") or "").strip().casefold()
        if field_name not in ELIGIBILITY_FIELD_DEFINITIONS:
            raise ValueError(f"Field eligibility {field_name or index + 1} tidak dikenal")
        comparison = str(condition.get("operator") or "").strip().casefold()
        allowed = ELIGIBILITY_FIELD_DEFINITIONS[field_name]["operators"]
        if comparison not in allowed:
            raise ValueError(f"Operator {comparison or '-'} tidak valid untuk {field_name}")

        value = condition.get("value")
        if comparison in {"is_true", "is_false"}:
            if value is not None:
                raise ValueError(f"Operator {comparison} wajib memakai value null")
            canonical_value = None
        elif comparison in {"in", "not_in"}:
            if not isinstance(value, list) or not value or len(value) > 100:
                raise ValueError(f"Operator {comparison} memerlukan 1-100 nilai")
            canonical_value = []
            for item in value:
                normalized = _eligibility_scalar(field_name, item)
                if normalized not in canonical_value:
                    canonical_value.append(normalized)
        elif comparison == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("Operator between memerlukan tepat dua nilai")
            canonical_value = [
                _eligibility_scalar(field_name, value[0]),
                _eligibility_scalar(field_name, value[1]),
            ]
            left = _eligibility_comparable(field_name, canonical_value[0])
            right = _eligibility_comparable(field_name, canonical_value[1])
            if left > right:
                raise ValueError("Nilai minimum between tidak boleh melebihi maksimum")
        else:
            canonical_value = _eligibility_scalar(field_name, value)
        canonical_conditions.append(
            {"field": field_name, "operator": comparison, "value": canonical_value}
        )

    canonical = {
        "version": ELIGIBILITY_RULES_VERSION,
        "operator": group_operator,
        "conditions": canonical_conditions,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > ELIGIBILITY_MAX_BYTES:
        raise ValueError("eligibility_rules melebihi batas ukuran")
    return canonical


def serialize_eligibility_rules(raw: Any) -> str:
    return json.dumps(
        canonicalize_eligibility_rules(raw),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def legacy_eligibility_rules(
    segment: Any,
    customer_targets: Sequence[Any] = (),
) -> dict[str, Any]:
    """Adapt a legacy customer_segment without persisting or rewriting it."""

    if isinstance(segment, Mapping):
        promo = segment
        customer_targets = promo.get("customer_targets", customer_targets) or ()
        segment = promo.get("customer_segment")
    canonical_segment = str(segment or "all").strip().casefold()
    if canonical_segment == "all_customers":
        canonical_segment = "all"
    conditions: list[dict[str, Any]]
    if canonical_segment == "all":
        conditions = []
    elif canonical_segment == "members_only":
        conditions = [
            {"field": "authentication_status", "operator": "equals", "value": "member"},
            {"field": "account_status", "operator": "equals", "value": "active"},
        ]
    elif canonical_segment == "guests_only":
        conditions = [
            {"field": "authentication_status", "operator": "equals", "value": "guest"},
        ]
    elif canonical_segment == "new":
        conditions = [
            {"field": "successful_order_count", "operator": "equals", "value": 0},
        ]
    elif canonical_segment == "existing":
        conditions = [
            {
                "field": "successful_order_count",
                "operator": "greater_than_or_equal",
                "value": 1,
            },
        ]
    elif canonical_segment == "specific":
        ids = sorted({integer(value) for value in customer_targets if integer(value) > 0})
        # An empty legacy relation intentionally remains an impossible match.
        conditions = [
            {"field": "customer_id", "operator": "in", "value": ids},
        ]
    else:
        raise ValueError("Segmentasi pelanggan legacy tidak dikenal")
    return {
        "version": ELIGIBILITY_RULES_VERSION,
        "operator": "all",
        "conditions": conditions,
    }


def eligibility_customer_ids(rules: Any) -> list[int]:
    canonical = canonicalize_eligibility_rules(rules)
    values: set[int] = set()
    for condition in canonical["conditions"]:
        if condition["field"] != "customer_id":
            continue
        raw_value = condition["value"]
        candidates = raw_value if isinstance(raw_value, list) else [raw_value]
        values.update(value for value in candidates if type(value) is int and value > 0)
    return sorted(values)


def _eligibility_value_label(field_name: str, value: Any) -> str:
    if field_name in {"normalized_phone", "target_id"}:
        count = len(value) if isinstance(value, list) else 1
        return f"{count} nilai terlindungi"
    if field_name == "customer_id" and isinstance(value, list):
        return f"{len(value)} customer"
    if isinstance(value, list):
        return " sampai ".join(str(item) for item in value)
    if type(value) is bool:
        return "ya" if value else "tidak"
    return str(value)


def eligibility_rules_summary(rules: Any) -> str:
    try:
        canonical = canonicalize_eligibility_rules(rules)
    except ValueError:
        # Legacy `specific` with no selected relation is deliberately impossible
        # but still needs a useful admin summary.
        if not isinstance(rules, Mapping):
            return "Aturan eligibility tidak valid."
        canonical = dict(rules)
    conditions = canonical.get("conditions")
    if not isinstance(conditions, list):
        return "Aturan eligibility tidak valid."
    if not conditions:
        return "Promo berlaku untuk semua pelanggan."
    operator_labels = {
        "equals": "=",
        "not_equals": "!=",
        "greater_than": ">",
        "greater_than_or_equal": ">=",
        "less_than": "<",
        "less_than_or_equal": "<=",
        "in": "termasuk",
        "not_in": "tidak termasuk",
        "is_true": "ya",
        "is_false": "tidak",
        "between": "antara",
    }
    parts: list[str] = []
    for condition in conditions:
        field_name = str(condition.get("field") or "")
        definition = ELIGIBILITY_FIELD_DEFINITIONS.get(field_name, {})
        field_label = str(definition.get("label") or field_name)
        comparison = str(condition.get("operator") or "")
        value = condition.get("value")
        if comparison in {"is_true", "is_false"}:
            parts.append(f"{field_label}: {operator_labels[comparison]}")
        else:
            parts.append(
                f"{field_label} {operator_labels.get(comparison, comparison)} "
                f"{_eligibility_value_label(field_name, value)}"
            )
    conjunction = " dan " if canonical.get("operator") == "all" else " atau "
    return "Promo berlaku jika " + conjunction.join(parts) + "."


def eligibility_options_metadata() -> dict[str, Any]:
    fields = []
    for value, definition in ELIGIBILITY_FIELD_DEFINITIONS.items():
        item = {
            "value": value,
            "label": definition["label"],
            "type": definition["type"],
            "operators": list(definition["operators"]),
        }
        if definition.get("options"):
            item["options"] = [dict(option) for option in definition["options"]]
        if "minimum" in definition:
            item["minimum"] = definition["minimum"]
        fields.append(item)
    presets = [
        {
            "value": "all",
            "label": "Semua pelanggan",
            "description": "Tidak ada syarat identitas khusus.",
            "rules": legacy_eligibility_rules("all"),
        },
        {
            "value": "members_only",
            "label": "Khusus member",
            "description": "Member yang login dengan akun aktif.",
            "rules": legacy_eligibility_rules("members_only"),
        },
        {
            "value": "guests_only",
            "label": "Khusus guest",
            "description": "Checkout tanpa login akun.",
            "rules": legacy_eligibility_rules("guests_only"),
        },
        {
            "value": "first_purchase",
            "label": "Pembeli pertama",
            "description": "Guest atau member tanpa transaksi berhasil.",
            "rules": legacy_eligibility_rules("new"),
        },
        {
            "value": "member_new",
            "label": "Member baru",
            "description": "Member aktif dengan umur akun maksimal yang dapat diubah.",
            "rules": {
                "version": 1,
                "operator": "all",
                "conditions": [
                    {"field": "authentication_status", "operator": "equals", "value": "member"},
                    {"field": "account_status", "operator": "equals", "value": "active"},
                    {"field": "account_age_days", "operator": "less_than_or_equal", "value": 7},
                ],
            },
        },
        {
            "value": "existing",
            "label": "Pelanggan lama",
            "description": "Minimal satu transaksi berhasil.",
            "rules": legacy_eligibility_rules("existing"),
        },
        {
            "value": "specific",
            "label": "Pelanggan tertentu",
            "description": "ID akun customer yang dipilih admin.",
            "rules": legacy_eligibility_rules("specific"),
        },
        {
            "value": "custom",
            "label": "Custom / Aturan sendiri",
            "description": "Susun kombinasi syarat sendiri.",
            "rules": legacy_eligibility_rules("all"),
        },
    ]
    return {
        "version": ELIGIBILITY_RULES_VERSION,
        "max_conditions": ELIGIBILITY_MAX_CONDITIONS,
        "max_bytes": ELIGIBILITY_MAX_BYTES,
        "group_operators": [
            {"value": "all", "label": "Penuhi SEMUA syarat"},
            {"value": "any", "label": "Penuhi SALAH SATU syarat"},
        ],
        "operators": list(ELIGIBILITY_COMPARISON_OPERATORS),
        "fields": fields,
        "presets": presets,
    }


def _parse_clock(value: Any) -> Optional[time]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return time.fromisoformat(cleaned)
    except ValueError:
        try:
            return datetime.strptime(cleaned, "%H:%M").time()
        except ValueError:
            return None


DAY_ALIASES = {
    "0": 0,
    "sun": 0,
    "sunday": 0,
    "minggu": 0,
    "1": 1,
    "mon": 1,
    "monday": 1,
    "senin": 1,
    "2": 2,
    "tue": 2,
    "tuesday": 2,
    "selasa": 2,
    "3": 3,
    "wed": 3,
    "wednesday": 3,
    "rabu": 3,
    "4": 4,
    "thu": 4,
    "thursday": 4,
    "kamis": 4,
    "5": 5,
    "fri": 5,
    "friday": 5,
    "jumat": 5,
    "jum'at": 5,
    "6": 6,
    "sat": 6,
    "saturday": 6,
    "sabtu": 6,
    # Accept ISO-style Sunday for old manually-authored rows.
    "7": 0,
}


def _active_weekdays(value: Any) -> set[int]:
    days: set[int] = set()
    for raw in parse_json_list(value):
        cleaned = str(raw).strip().casefold()
        if cleaned in DAY_ALIASES:
            days.add(DAY_ALIASES[cleaned])
    return days


@dataclass(frozen=True)
class PromoIdentity:
    """Trusted promotion identity derived by the backend, never by request flags."""

    customer_id: Optional[int] = None
    phone: str = ""
    target_id: str = ""
    is_authenticated: Optional[bool] = None
    account_active: Optional[bool] = None
    account_status: str = field(init=False)

    def __post_init__(self) -> None:
        resolved_customer_id = integer(self.customer_id)
        if resolved_customer_id <= 0:
            resolved_customer_id = None
        authenticated = (
            resolved_customer_id is not None
            if self.is_authenticated is None
            else bool(self.is_authenticated)
        )
        authenticated = bool(authenticated and resolved_customer_id is not None)
        active = (
            bool(resolved_customer_id is not None and authenticated)
            if self.account_active is None
            else bool(self.account_active)
        )
        if not authenticated:
            active = False
            resolved_customer_id = None
        object.__setattr__(self, "customer_id", resolved_customer_id)
        object.__setattr__(self, "phone", normalize_phone(self.phone))
        object.__setattr__(self, "target_id", normalize_target_id(self.target_id))
        object.__setattr__(self, "is_authenticated", authenticated)
        object.__setattr__(self, "account_active", active)
        account_status = "member" if authenticated and active else ("inactive" if authenticated else "guest")
        object.__setattr__(self, "account_status", account_status)


@dataclass(frozen=True)
class EligibilityProfile:
    """Authoritative customer facts resolved once by the promotion service."""

    authentication_status: str = "guest"
    has_customer_account: bool = False
    account_status: Optional[str] = None
    account_created_at: Optional[datetime] = None
    account_age_days: Optional[int] = None
    successful_order_count: Optional[int] = None
    successful_order_total: Optional[int] = None
    last_successful_order_at: Optional[datetime] = None
    days_since_last_successful_order: Optional[int] = None
    has_successful_order: Optional[bool] = None
    customer_id: Optional[int] = None
    normalized_phone: str = ""
    target_id: str = ""

    def __post_init__(self) -> None:
        authentication = str(self.authentication_status or "guest").strip().casefold()
        object.__setattr__(self, "authentication_status", "member" if authentication == "member" else "guest")
        object.__setattr__(self, "has_customer_account", bool(self.has_customer_account))
        status = str(self.account_status or "").strip().casefold()
        object.__setattr__(self, "account_status", status if status in {"active", "inactive"} else None)

        customer_id = integer(self.customer_id)
        object.__setattr__(self, "customer_id", customer_id if customer_id > 0 else None)
        object.__setattr__(self, "normalized_phone", normalize_phone(self.normalized_phone))
        object.__setattr__(self, "target_id", normalize_target_id(self.target_id))

        for field_name in (
            "account_created_at",
            "last_successful_order_at",
        ):
            raw_value = getattr(self, field_name)
            parsed = parse_datetime(raw_value, "UTC") if raw_value is not None else None
            object.__setattr__(self, field_name, parsed)
        for field_name in (
            "account_age_days",
            "successful_order_count",
            "successful_order_total",
            "days_since_last_successful_order",
        ):
            raw_value = getattr(self, field_name)
            object.__setattr__(self, field_name, max(integer(raw_value), 0) if raw_value is not None else None)
        if self.successful_order_count is not None:
            object.__setattr__(self, "has_successful_order", self.successful_order_count > 0)
        elif self.has_successful_order is not None:
            object.__setattr__(self, "has_successful_order", bool(self.has_successful_order))

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "authentication_status": self.authentication_status,
            "has_customer_account": self.has_customer_account,
            "account_status": self.account_status,
            "account_created_at": (
                self.account_created_at.astimezone(timezone.utc).isoformat()
                if self.account_created_at
                else None
            ),
            "account_age_days": self.account_age_days,
            "successful_order_count": self.successful_order_count,
            "successful_order_total": self.successful_order_total,
            "last_successful_order_at": (
                self.last_successful_order_at.astimezone(timezone.utc).isoformat()
                if self.last_successful_order_at
                else None
            ),
            "days_since_last_successful_order": self.days_since_last_successful_order,
            "has_successful_order": self.has_successful_order,
            "customer_id_present": self.customer_id is not None,
            "phone_present": bool(self.normalized_phone),
            "target_id_present": bool(self.target_id),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_safe_dict()


@dataclass(frozen=True)
class PromotionContext:
    base_price: int
    sku: str
    provider: str = ""
    category: str = ""
    brand: str = ""
    supplier: str = ""
    product_id: Optional[str] = None
    payment_method: str = ""
    promo_code: Optional[str] = None
    customer_id: Optional[int] = None
    phone: str = ""
    target_id: str = ""
    customer_segment: str = "all"
    identity: Optional[PromoIdentity] = None
    eligibility_profile: Optional[EligibilityProfile] = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_price", rupiah(self.base_price))
        identity = self.identity or PromoIdentity(
            customer_id=self.customer_id,
            phone=self.phone,
            target_id=self.target_id,
        )
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "customer_id", identity.customer_id)
        object.__setattr__(self, "phone", identity.phone)
        object.__setattr__(self, "target_id", identity.target_id)
        object.__setattr__(self, "payment_method", normalize_payment_code(self.payment_method))
        if self.now.tzinfo is None:
            object.__setattr__(self, "now", self.now.replace(tzinfo=timezone.utc))
        if self.eligibility_profile is None:
            known_history_identity = bool(identity.customer_id is not None or identity.phone)
            segment = str(self.customer_segment or "").strip().casefold()
            success_count: Optional[int] = None
            if known_history_identity and segment == "new":
                success_count = 0
            elif known_history_identity and segment == "existing":
                success_count = 1
            profile = EligibilityProfile(
                authentication_status="member" if identity.is_authenticated else "guest",
                has_customer_account=bool(identity.is_authenticated and identity.customer_id is not None),
                account_status=(
                    "active" if identity.account_active else "inactive"
                ) if identity.is_authenticated else None,
                successful_order_count=success_count,
                successful_order_total=0 if success_count == 0 else None,
                has_successful_order=(success_count > 0) if success_count is not None else None,
                customer_id=identity.customer_id,
                normalized_phone=identity.phone,
                target_id=identity.target_id,
            )
            object.__setattr__(self, "eligibility_profile", profile)


@dataclass(frozen=True)
class EligibilityEvaluation:
    eligible: bool
    reason: Optional[str]
    reason_code: Optional[str]
    source: str
    operator: str
    conditions: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "source": self.source,
            "operator": self.operator,
            "conditions": [dict(condition) for condition in self.conditions],
        }


ELIGIBILITY_REASON_CODES = {
    "eligibility_rule_not_met": "ELIGIBILITY_RULE_NOT_MET",
    "member_login_required": "MEMBER_LOGIN_REQUIRED",
    "guest_only": "GUEST_ONLY_PROMO",
    "account_inactive": "ACCOUNT_INACTIVE",
    "account_too_old": "ACCOUNT_TOO_OLD",
    "first_purchase_only": "FIRST_PURCHASE_ONLY",
    "min_successful_orders_not_met": "MIN_SUCCESSFUL_ORDERS_NOT_MET",
    "min_successful_spend_not_met": "MIN_SUCCESSFUL_SPEND_NOT_MET",
    "customer_not_targeted": "CUSTOMER_NOT_TARGETED",
    "customer_identity_required": "CUSTOMER_IDENTITY_REQUIRED",
    "invalid_eligibility_rule": "INVALID_ELIGIBILITY_RULE",
    # Legacy-only reasons are retained for stable public behavior.
    "new_customer_only": "FIRST_PURCHASE_ONLY",
    "existing_customer_only": "EXISTING_CUSTOMER_ONLY",
    "customer_segment": "CUSTOMER_NOT_ELIGIBLE",
}


_ELIGIBILITY_REASON_MESSAGES = {
    "eligibility_rule_not_met": "Pelanggan tidak memenuhi syarat promo ini.",
    "member_login_required": "Masuk ke akun member untuk menggunakan promo ini.",
    "guest_only": "Promo ini hanya berlaku untuk checkout tanpa akun.",
    "account_inactive": "Akun customer tidak aktif.",
    "account_too_old": "Umur akun melewati batas promo.",
    "first_purchase_only": "Pelanggan sudah memiliki transaksi berhasil.",
    "min_successful_orders_not_met": "Jumlah transaksi berhasil belum memenuhi batas promo.",
    "min_successful_spend_not_met": "Total pembelian berhasil belum memenuhi batas promo.",
    "customer_not_targeted": "Akun customer tidak termasuk target promo.",
    "customer_identity_required": "Identitas customer diperlukan untuk memeriksa syarat promo.",
    "invalid_eligibility_rule": "Konfigurasi syarat promo tidak valid.",
    "new_customer_only": "Promo hanya berlaku untuk pembeli pertama.",
    "existing_customer_only": "Promo hanya berlaku untuk pelanggan lama.",
    "customer_segment": "Segmentasi pelanggan promo tidak dikenal.",
}


def _eligibility_comparable(field_name: str, value: Any) -> Any:
    if field_name == "account_created_at":
        parsed = parse_datetime(value, "UTC")
        if parsed is None:
            raise ValueError("Tanggal eligibility tidak valid")
        return parsed
    return value


def _profile_value(profile: EligibilityProfile, field_name: str) -> Any:
    return {
        "authentication_status": profile.authentication_status,
        "account_status": profile.account_status,
        "has_customer_account": profile.has_customer_account,
        "account_age_days": profile.account_age_days,
        "account_created_at": profile.account_created_at,
        "successful_order_count": profile.successful_order_count,
        "successful_order_total": profile.successful_order_total,
        "days_since_last_successful_order": profile.days_since_last_successful_order,
        "has_successful_order": profile.has_successful_order,
        "customer_id": profile.customer_id,
        "normalized_phone": profile.normalized_phone or None,
        "target_id": profile.target_id or None,
    }[field_name]


def _condition_matches(field_name: str, comparison: str, actual: Any, expected: Any) -> bool:
    # Unknown authoritative facts must never be interpreted as zero/false and
    # must not accidentally pass `not_*` operators.
    if actual is None:
        return False
    comparable_actual = _eligibility_comparable(field_name, actual)
    if comparison == "is_true":
        return comparable_actual is True
    if comparison == "is_false":
        return comparable_actual is False
    if comparison in {"in", "not_in"}:
        comparable_expected = [_eligibility_comparable(field_name, item) for item in expected]
        included = comparable_actual in comparable_expected
        return included if comparison == "in" else not included
    if comparison == "between":
        minimum, maximum = (
            _eligibility_comparable(field_name, expected[0]),
            _eligibility_comparable(field_name, expected[1]),
        )
        return minimum <= comparable_actual <= maximum
    comparable_expected = _eligibility_comparable(field_name, expected)
    if comparison == "equals":
        return comparable_actual == comparable_expected
    if comparison == "not_equals":
        return comparable_actual != comparable_expected
    if comparison == "greater_than":
        return comparable_actual > comparable_expected
    if comparison == "greater_than_or_equal":
        return comparable_actual >= comparable_expected
    if comparison == "less_than":
        return comparable_actual < comparable_expected
    if comparison == "less_than_or_equal":
        return comparable_actual <= comparable_expected
    return False


def _condition_failure_reason(
    profile: EligibilityProfile,
    field_name: str,
    comparison: str,
    expected: Any,
    actual: Any,
) -> str:
    if field_name in {
        "successful_order_count",
        "successful_order_total",
        "has_successful_order",
        "normalized_phone",
        "target_id",
    } and actual is None:
        return "customer_identity_required"
    if field_name == "days_since_last_successful_order" and actual is None:
        return (
            "customer_identity_required"
            if profile.successful_order_count is None
            else "eligibility_rule_not_met"
        )
    if field_name in {"account_status", "account_age_days", "account_created_at"} and actual is None:
        return "member_login_required" if profile.authentication_status == "guest" else "account_inactive"
    if field_name == "authentication_status":
        expected_values = set(expected if isinstance(expected, list) else [expected])
        member_required = (
            comparison in {"equals", "in"} and "member" in expected_values
        ) or (
            comparison in {"not_equals", "not_in"} and "guest" in expected_values
        )
        if member_required:
            return "member_login_required"
        if "guest" in expected_values or "member" in expected_values:
            return "guest_only"
    if field_name == "account_status" and (
        expected == "active" or (isinstance(expected, list) and "active" in expected)
    ):
        return "account_inactive"
    if field_name == "account_age_days":
        if comparison in {"less_than", "less_than_or_equal"}:
            return "account_too_old"
        if comparison == "between" and actual is not None and actual > expected[1]:
            return "account_too_old"
    if field_name == "successful_order_count":
        if comparison == "equals" and expected == 0:
            return "first_purchase_only"
        if comparison in {"greater_than", "greater_than_or_equal"}:
            return "min_successful_orders_not_met"
    if field_name == "successful_order_total" and comparison in {"greater_than", "greater_than_or_equal"}:
        return "min_successful_spend_not_met"
    if field_name == "customer_id":
        return "customer_not_targeted"
    return "eligibility_rule_not_met"


def _safe_diagnostic_value(field_name: str, value: Any, *, actual: bool = False) -> Any:
    if field_name in {"customer_id", "normalized_phone", "target_id"}:
        if actual:
            return "present" if value not in {None, ""} else "missing"
        count = len(value) if isinstance(value, list) else (0 if value is None else 1)
        return {"protected_value_count": count}
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _evaluate_canonical_rules(
    rules: Mapping[str, Any],
    profile: EligibilityProfile,
    *,
    source: str,
) -> EligibilityEvaluation:
    operator = str(rules.get("operator") or "all")
    condition_results: list[dict[str, Any]] = []
    for index, condition in enumerate(rules.get("conditions") or []):
        field_name = str(condition.get("field") or "")
        comparison = str(condition.get("operator") or "")
        expected = condition.get("value")
        actual = _profile_value(profile, field_name)
        matched = _condition_matches(field_name, comparison, actual, expected)
        reason = None if matched else _condition_failure_reason(
            profile,
            field_name,
            comparison,
            expected,
            actual,
        )
        condition_results.append(
            {
                "index": index,
                "field": field_name,
                "operator": comparison,
                "expected": _safe_diagnostic_value(field_name, expected),
                "actual": _safe_diagnostic_value(field_name, actual, actual=True),
                "matched": matched,
                "reason": reason,
                "reason_code": ELIGIBILITY_REASON_CODES.get(reason) if reason else None,
                "message": _ELIGIBILITY_REASON_MESSAGES.get(reason) if reason else "Syarat terpenuhi.",
            }
        )
    matches = [bool(item["matched"]) for item in condition_results]
    eligible = all(matches) if operator == "all" else any(matches)
    if not condition_results:
        eligible = operator == "all"
    first_failure = next((item for item in condition_results if not item["matched"]), None)
    reason = None if eligible else str((first_failure or {}).get("reason") or "eligibility_rule_not_met")
    return EligibilityEvaluation(
        eligible=eligible,
        reason=reason,
        reason_code=ELIGIBILITY_REASON_CODES.get(reason) if reason else None,
        source=source,
        operator=operator,
        conditions=tuple(condition_results),
    )


def evaluate_eligibility_rules(
    rules: Any,
    profile: EligibilityProfile,
) -> EligibilityEvaluation:
    try:
        canonical = canonicalize_eligibility_rules(rules)
    except ValueError:
        return EligibilityEvaluation(
            eligible=False,
            reason="invalid_eligibility_rule",
            reason_code=ELIGIBILITY_REASON_CODES["invalid_eligibility_rule"],
            source="explicit",
            operator="all",
        )
    return _evaluate_canonical_rules(canonical, profile, source="explicit")


def resolve_eligibility_rules(promo: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    raw = promo.get("eligibility_rules")
    if raw is None:
        return (
            legacy_eligibility_rules(
                promo.get("customer_segment"),
                promo.get("customer_targets") or (),
            ),
            "legacy_adapter",
        )
    return canonicalize_eligibility_rules(raw), "explicit"


def _legacy_failure_reason(
    segment: str,
    profile: EligibilityProfile,
    evaluation: EligibilityEvaluation,
) -> str:
    if segment in {"", "all", "all_customers"}:
        return evaluation.reason or "customer_segment"
    if segment == "members_only":
        if profile.authentication_status == "member" and profile.account_status != "active":
            return "account_inactive"
        return "member_login_required"
    if segment == "guests_only":
        if profile.authentication_status == "member" and profile.account_status == "inactive":
            return "account_inactive"
        return "guest_only"
    if segment == "new":
        return "customer_identity_required" if profile.successful_order_count is None else "new_customer_only"
    if segment == "existing":
        return "customer_identity_required" if profile.successful_order_count is None else "existing_customer_only"
    if segment == "specific":
        if profile.authentication_status == "member" and profile.account_status == "inactive":
            return "account_inactive"
        return "customer_not_targeted"
    return "customer_segment"


def evaluate_promotion_eligibility(
    promo: Mapping[str, Any],
    context: PromotionContext,
) -> EligibilityEvaluation:
    profile = context.eligibility_profile or EligibilityProfile()
    raw = promo.get("eligibility_rules")
    try:
        rules, source = resolve_eligibility_rules(promo)
    except ValueError:
        legacy_unknown = raw is None
        reason = "customer_segment" if legacy_unknown else "invalid_eligibility_rule"
        return EligibilityEvaluation(
            eligible=False,
            reason=reason,
            reason_code=ELIGIBILITY_REASON_CODES[reason],
            source="legacy_adapter" if legacy_unknown else "explicit",
            operator="all",
        )
    evaluation = (
        _evaluate_canonical_rules(rules, profile, source=source)
        if source == "legacy_adapter"
        else evaluate_eligibility_rules(rules, profile)
    )
    if source != "legacy_adapter" or evaluation.eligible:
        return evaluation
    segment = str(promo.get("customer_segment") or "all").strip().casefold()
    reason = _legacy_failure_reason(segment, profile, evaluation)
    return EligibilityEvaluation(
        eligible=False,
        reason=reason,
        reason_code=ELIGIBILITY_REASON_CODES.get(reason, "CUSTOMER_NOT_ELIGIBLE"),
        source=source,
        operator=evaluation.operator,
        conditions=evaluation.conditions,
    )


@dataclass(frozen=True)
class AppliedPromotion:
    id: int
    title: str
    internal_code: str
    voucher_code: str
    promo_type: str
    calculation_type: str
    discount_value: str
    max_discount: int
    minimum_transaction: int
    priority: int
    exclusive: bool
    stackable: bool
    rules_version: str
    application_source: str
    price_before: int
    discount_amount: int
    price_after: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "name": self.title,
            "internal_code": self.internal_code,
            "code": self.voucher_code,
            "voucher_code": self.voucher_code,
            "promo_type": self.promo_type,
            "discount_type": self.calculation_type,
            "calculation_type": self.calculation_type,
            "discount_value": self.discount_value,
            "max_discount": self.max_discount,
            "minimum_transaction": self.minimum_transaction,
            "priority": self.priority,
            "exclusive": self.exclusive,
            "stackable": self.stackable,
            "rules_version": self.rules_version,
            "application_source": self.application_source,
            "price_before": self.price_before,
            "discount_amount": self.discount_amount,
            "price_after": self.price_after,
        }


@dataclass(frozen=True)
class PromotionQuote:
    base_price: int
    final_price: int
    applied: tuple[AppliedPromotion, ...] = ()
    requested_code: str = ""
    code_valid: bool = True
    rejections: tuple[dict[str, Any], ...] = ()

    @property
    def discount_amount(self) -> int:
        return max(self.base_price - self.final_price, 0)

    @property
    def primary(self) -> Optional[AppliedPromotion]:
        return self.applied[-1] if self.applied else None

    def to_dict(self, *, include_rejections: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "base_price": self.base_price,
            "original_price": self.base_price,
            "final_price": self.final_price,
            "discount_amount": self.discount_amount,
            "requested_code": self.requested_code,
            "code_valid": self.code_valid,
            "promos_applied": [item.to_dict() for item in self.applied],
            "promo_applied": self.primary.to_dict() if self.primary else None,
            "snapshot_version": 2,
        }
        if include_rejections:
            payload["rejections"] = list(self.rejections)
        return payload


@dataclass(frozen=True)
class _Candidate:
    promo: Mapping[str, Any]
    preview_discount: int
    source: str

    @property
    def priority(self) -> int:
        return integer(self.promo.get("priority"))

    @property
    def promo_id(self) -> int:
        return integer(self.promo.get("id"))

    @property
    def exclusive(self) -> bool:
        return truthy(self.promo.get("exclusive"))

    @property
    def stackable(self) -> bool:
        return truthy(self.promo.get("stackable"), True)


def _lifecycle_reason(promo: Mapping[str, Any], context: PromotionContext) -> Optional[str]:
    if not truthy(promo.get("active"), True):
        return "disabled"
    if promo.get("archived_at"):
        return "archived"
    lifecycle = str(promo.get("lifecycle_status") or "active").strip().upper()
    if lifecycle in TERMINAL_LIFECYCLE_STATUSES:
        return lifecycle.casefold()

    zone_name = promo.get("timezone") or DEFAULT_TIMEZONE
    starts_at = parse_datetime(promo.get("starts_at"), zone_name)
    ends_at = parse_datetime(promo.get("ends_at"), zone_name)
    now_utc = context.now.astimezone(timezone.utc)
    if starts_at and now_utc < starts_at:
        return "not_started"
    if ends_at and now_utc >= ends_at:
        return "expired"

    local_now = now_utc.astimezone(_zone(zone_name))
    weekdays = _active_weekdays(promo.get("active_days"))
    # Admin/API contract follows JavaScript: Sunday=0 ... Saturday=6.
    # datetime.weekday() follows Monday=0, so convert before comparing.
    admin_weekday = (local_now.weekday() + 1) % 7
    if weekdays and admin_weekday not in weekdays:
        return "inactive_day"
    daily_start = _parse_clock(promo.get("daily_start_time"))
    daily_end = _parse_clock(promo.get("daily_end_time"))
    if daily_start and daily_end:
        clock = local_now.timetz().replace(tzinfo=None)
        if daily_start <= daily_end:
            if not (daily_start <= clock < daily_end):
                return "inactive_hour"
        elif not (clock >= daily_start or clock < daily_end):
            return "inactive_hour"
    return None


def _payment_methods(promo: Mapping[str, Any]) -> set[str]:
    return {normalize_payment_code(item) for item in parse_json_list(promo.get("payment_methods")) if str(item).strip()}


def _single_target_matches(target_type: Any, target_key: Any, context: PromotionContext, *, legacy: bool) -> bool:
    kind = str(target_type or "").strip().casefold()
    key = str(target_key or "").strip()
    if not kind or not key:
        return False
    if kind in {"sku", "product", "product_sku"}:
        return normalize_sku(key) == normalize_sku(context.sku)
    if kind in {"provider", "game"}:
        actuals = [context.provider, context.brand]
    elif kind in {"category", "kategori"}:
        actuals = [context.category]
    elif kind in {"supplier", "provider_supplier"}:
        actuals = [context.supplier]
    else:
        return False
    key_token = normalize_token(key)
    for actual in actuals:
        actual_token = normalize_token(actual)
        if not actual_token:
            continue
        if key_token == actual_token:
            return True
        if legacy and (key_token in actual_token or actual_token in key_token):
            return True
    return False


def _target_reason(promo: Mapping[str, Any], context: PromotionContext) -> Optional[str]:
    structured = promo.get("targets")
    targets = list(structured) if isinstance(structured, Sequence) and not isinstance(structured, (str, bytes)) else []
    legacy = truthy(promo.get("legacy_compatible"), True)
    if targets:
        excluded = [target for target in targets if truthy(target.get("excluded"))]
        included = [target for target in targets if not truthy(target.get("excluded"))]
        for target in excluded:
            if _single_target_matches(target.get("target_type"), target.get("target_key"), context, legacy=legacy):
                return "target_excluded"
        if included and not any(
            _single_target_matches(target.get("target_type"), target.get("target_key"), context, legacy=legacy)
            for target in included
        ):
            return "target_not_included"
        return None

    scope = str(promo.get("target_scope") or "all").strip().casefold()
    if scope in {"", "all"}:
        return None
    raw_values = parse_json_list(promo.get("target_value"))
    if not raw_values:
        return "target_missing"
    if any(_single_target_matches(scope, value, context, legacy=True) for value in raw_values):
        return None
    return "target_not_included"


def _customer_reason(promo: Mapping[str, Any], context: PromotionContext) -> Optional[str]:
    identity = context.identity or PromoIdentity(
        customer_id=context.customer_id,
        phone=context.phone,
        target_id=context.target_id,
    )
    eligibility = evaluate_promotion_eligibility(promo, context)
    if not eligibility.eligible:
        return eligibility.reason or "eligibility_rule_not_met"

    has_customer_identity_limit = (
        integer(promo.get("max_per_customer")) > 0
        or integer(promo.get("max_per_customer_daily")) > 0
    )
    if has_customer_identity_limit and identity.customer_id is None and not identity.phone:
        return "phone_required"
    if integer(promo.get("max_per_phone")) > 0 and not identity.phone:
        return "phone_required"
    if integer(promo.get("max_per_target")) > 0 and not identity.target_id:
        return "target_id_required"
    return None


def _usage_reason(promo: Mapping[str, Any]) -> Optional[str]:
    checks = (
        ("usage_limit", "usage_count", "quota_exhausted"),
        ("quota_daily", "daily_usage_count", "daily_quota_exhausted"),
        ("max_per_customer", "customer_usage_count", "customer_limit"),
        ("max_per_customer_daily", "customer_daily_usage_count", "customer_daily_limit"),
        ("max_per_phone", "phone_usage_count", "phone_limit"),
        ("max_per_target", "target_usage_count", "target_limit"),
    )
    for limit_key, count_key, reason in checks:
        limit = integer(promo.get(limit_key))
        if limit > 0 and integer(promo.get(count_key)) >= limit:
            return reason
    budget = rupiah(promo.get("budget_limit"))
    if budget > 0 and rupiah(promo.get("discount_spent")) >= budget:
        return "budget_exhausted"
    return None


def _calculation_type(promo: Mapping[str, Any]) -> str:
    value = promo.get("calculation_type") or promo.get("discount_type")
    normalized = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "percentage": "percent",
        "persentase": "percent",
        "nominal": "fixed",
        "amount": "fixed",
        "special": "special_price",
        "price": "special_price",
    }
    return aliases.get(normalized, normalized)


def _round_money(raw: Decimal, rule: Any) -> int:
    normalized = str(rule or "none").strip().casefold().replace("-", "_")
    if normalized in {"ceil", "ceiling", "up"}:
        value = raw.quantize(Decimal("1"), rounding=ROUND_CEILING)
    elif normalized in {"half_up", "nearest", "round"}:
        value = raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        value = raw.quantize(Decimal("1"), rounding=ROUND_FLOOR)

    amount = max(int(value), 0)
    step = 0
    if normalized in {"nearest_100", "round_100"}:
        step = 100
    elif normalized in {"nearest_1000", "round_1000"}:
        step = 1000
    if step:
        amount = int((Decimal(amount) / Decimal(step)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * step
    elif normalized in {"floor_100", "down_100"}:
        amount = amount // 100 * 100
    elif normalized in {"ceil_100", "up_100"}:
        amount = ((amount + 99) // 100) * 100
    return max(amount, 0)


def promotion_discount(promo: Mapping[str, Any], price: int) -> int:
    base = rupiah(price)
    calculation = _calculation_type(promo)
    value = decimal_value(promo.get("discount_value"))
    if calculation == "percent":
        raw = Decimal(base) * value / Decimal("100")
        discount = _round_money(raw, promo.get("rounding_rule"))
    elif calculation == "fixed":
        discount = rupiah(value)
    elif calculation == "special_price":
        special = rupiah(promo.get("special_price") if promo.get("special_price") is not None else value)
        discount = max(base - special, 0)
    else:
        return 0

    maximum = rupiah(promo.get("max_discount"))
    if maximum > 0:
        discount = min(discount, maximum)
    budget = rupiah(promo.get("budget_limit"))
    if budget > 0:
        discount = min(discount, max(budget - rupiah(promo.get("discount_spent")), 0))
    return min(max(discount, 0), base)


def _eligibility_reason(promo: Mapping[str, Any], context: PromotionContext) -> tuple[Optional[str], str]:
    promo_type = str(promo.get("promo_type") or "").strip().casefold()
    rule_type = str(promo.get("rule_type") or "").strip().casefold()
    if promo_type in {"banner", "content", "content_banner"} or rule_type == "content":
        return "content_only", "automatic"
    lifecycle = _lifecycle_reason(promo, context)
    if lifecycle:
        return lifecycle, "automatic"

    voucher = normalize_code(promo.get("code") or promo.get("voucher_code"))
    requested = normalize_code(context.promo_code)
    source = "voucher" if voucher or promo_type in {"voucher", "code"} else "automatic"
    if voucher:
        if not requested:
            return "voucher_required", source
        if requested != voucher:
            return "voucher_mismatch", source
    elif promo_type in {"voucher", "code"}:
        return "voucher_not_configured", source

    methods = _payment_methods(promo)
    if methods and normalize_payment_code(context.payment_method) not in methods:
        return "payment_method", source
    if context.base_price < rupiah(promo.get("minimum_transaction")):
        return "minimum_transaction", source
    for checker in (_target_reason, _customer_reason):
        reason = checker(promo, context)
        if reason:
            return reason, source
    usage = _usage_reason(promo)
    if usage:
        return usage, source
    if _calculation_type(promo) not in {"percent", "fixed", "special_price"}:
        return "calculation_invalid", source
    return None, source


def _rank(candidate: _Candidate) -> tuple[int, int, int, int]:
    # Explicit voucher wins the final deterministic tie because the customer
    # intentionally supplied it. Stable ID is the final tie breaker.
    source_rank = 1 if candidate.source == "voucher" else 0
    return (candidate.priority, candidate.preview_discount, source_rank, -candidate.promo_id)


def _best_by_source(candidates: Iterable[_Candidate]) -> list[_Candidate]:
    best: dict[str, _Candidate] = {}
    for candidate in candidates:
        current = best.get(candidate.source)
        if current is None or _rank(candidate) > _rank(current):
            best[candidate.source] = candidate
    return list(best.values())


def _select_candidates(candidates: Sequence[_Candidate]) -> list[_Candidate]:
    selected = _best_by_source(candidates)
    if not selected:
        return []

    voucher = next((item for item in selected if item.source == "voucher"), None)
    automatic = next((item for item in selected if item.source == "automatic"), None)
    if voucher and (voucher.exclusive or not voucher.stackable):
        return [voucher]
    if automatic and (automatic.exclusive or not automatic.stackable):
        contenders = [automatic] + ([voucher] if voucher else [])
        return [max(contenders, key=_rank)]

    limits = [integer(item.promo.get("max_promotions_per_order"), 2) for item in selected]
    positive_limits = [value for value in limits if value > 0]
    maximum = min(positive_limits) if positive_limits else 2
    ranked = sorted(selected, key=_rank, reverse=True)[:maximum]
    # Apply priority first; for an exact tie preserve legacy auto -> voucher
    # sequence so fixed/percentage combinations are deterministic.
    return sorted(ranked, key=lambda item: (-item.priority, 0 if item.source == "automatic" else 1, item.promo_id))


def quote_promotions(promotions: Sequence[Mapping[str, Any]], context: PromotionContext) -> PromotionQuote:
    """Evaluate all rules and return one deterministic checkout quote."""

    candidates: list[_Candidate] = []
    rejections: list[dict[str, Any]] = []
    requested = normalize_code(context.promo_code)
    requested_seen = False

    for promo in promotions:
        voucher = normalize_code(promo.get("code") or promo.get("voucher_code"))
        if requested and voucher == requested:
            requested_seen = True
        reason, source = _eligibility_reason(promo, context)
        if reason:
            if (requested and voucher == requested) or reason not in {"voucher_required", "voucher_mismatch"}:
                rejection = {
                    "promo_id": integer(promo.get("id")),
                    "code": str(promo.get("code") or ""),
                    "reason": reason,
                }
                eligibility = evaluate_promotion_eligibility(promo, context)
                if not eligibility.eligible and reason == eligibility.reason:
                    rejection["eligibility"] = eligibility.to_dict()
                rejections.append(rejection)
            continue
        preview = promotion_discount(promo, context.base_price)
        if preview <= 0:
            rejections.append({"promo_id": integer(promo.get("id")), "code": str(promo.get("code") or ""), "reason": "zero_discount"})
            continue
        candidates.append(_Candidate(promo=promo, preview_discount=preview, source=source))

    selected = _select_candidates(candidates)
    current_price = context.base_price
    applied: list[AppliedPromotion] = []
    for candidate in selected:
        promo = candidate.promo
        discount = promotion_discount(promo, current_price)
        if discount <= 0:
            continue
        after = max(current_price - discount, 0)
        applied.append(
            AppliedPromotion(
                id=integer(promo.get("id")),
                title=str(promo.get("title") or promo.get("name") or ""),
                internal_code=str(promo.get("internal_code") or ""),
                voucher_code=str(promo.get("code") or promo.get("voucher_code") or ""),
                promo_type=str(promo.get("promo_type") or ("voucher" if candidate.source == "voucher" else "automatic")),
                calculation_type=_calculation_type(promo),
                discount_value=str(promo.get("discount_value") or "0"),
                max_discount=rupiah(promo.get("max_discount")),
                minimum_transaction=rupiah(promo.get("minimum_transaction")),
                priority=integer(promo.get("priority")),
                exclusive=candidate.exclusive,
                stackable=candidate.stackable,
                rules_version=str(promo.get("rules_version") or "legacy_v1"),
                application_source=candidate.source,
                price_before=current_price,
                discount_amount=discount,
                price_after=after,
            )
        )
        current_price = after

    code_applied = any(normalize_code(item.voucher_code) == requested for item in applied) if requested else True
    code_valid = code_applied if requested else True
    if requested and not requested_seen:
        rejections.append({"promo_id": 0, "code": str(context.promo_code or "").strip(), "reason": "code_not_found"})
    elif requested and not code_applied and not any(
        normalize_code(rejection.get("code")) == requested for rejection in rejections
    ):
        # The voucher exists and passed eligibility, but deterministic stacking,
        # exclusivity, or per-order limits selected another promotion.
        rejections.append({"promo_id": 0, "code": str(context.promo_code or "").strip(), "reason": "promo_conflict"})
    return PromotionQuote(
        base_price=context.base_price,
        final_price=current_price,
        applied=tuple(applied),
        requested_code=str(context.promo_code or "").strip(),
        code_valid=code_valid,
        rejections=tuple(rejections),
    )


def quote_fingerprint(quote: PromotionQuote) -> tuple[Any, ...]:
    return (
        quote.base_price,
        quote.final_price,
        tuple((item.id, item.discount_amount, item.price_before, item.price_after) for item in quote.applied),
        quote.code_valid,
    )
