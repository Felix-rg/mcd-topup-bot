"""Deterministic, allow-listed normalization for provider catalog candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence


NORMALIZER_VERSION = "digiflazz-prepaid-normalizer-v1"
IDENTITY_FINGERPRINT_VERSION = "catalog-provider-identity-v1"
SOURCE_ROW_FINGERPRINT_VERSION = "catalog-provider-row-v1"

# This is intentionally a narrow list.  Unknown fields in a provider response
# are never persisted in catalog snapshots or provider candidate storage.
SAFE_PROVIDER_FIELDS = (
    "buyer_sku_code",
    "product_name",
    "category",
    "brand",
    "type",
    "seller_name",
    "price",
    "desc",
    "buyer_product_status",
    "seller_product_status",
    "stock",
    "unlimited_stock",
    "multi",
    "start_cut_off",
    "end_cut_off",
)

_WHITESPACE = re.compile(r"\s+")
_DENOMINATION = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>k|rb|ribu|jt|gb|mb|uc|diamond|dm)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, *, limit: int = 500) -> str:
    cleaned = _WHITESPACE.sub(" ", str(value or "").strip())
    return cleaned[:limit]


def _optional_text(value: Any, *, limit: int = 500) -> Optional[str]:
    value = _text(value, limit=limit)
    return value or None


def _decimal(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return format(parsed.normalize(), "f")


def _integer(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _flag(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    normalized = str(value if value is not None else "").strip().casefold()
    if normalized in {"1", "true", "t", "yes", "y", "on", "active", "available"}:
        return 1
    if normalized in {"0", "false", "f", "no", "n", "off", "inactive", "unavailable"}:
        return 0
    return None


def _cutoff(value: Any) -> Optional[str]:
    candidate = _optional_text(value, limit=32)
    return candidate


def _denomination(name: str) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    matches = list(_DENOMINATION.finditer(name))
    if len(matches) != 1:
        return None, None, None, bool(matches)
    match = matches[0]
    raw_number = match.group("number").replace(",", ".")
    try:
        number = Decimal(raw_number)
    except InvalidOperation:
        return None, None, None, True
    unit = match.group("unit").casefold()
    multiplier = {"k": Decimal("1000"), "rb": Decimal("1000"), "ribu": Decimal("1000"), "jt": Decimal("1000000")}
    # A bare ``K`` is not a safe IDR assertion for a provider catalog.  For
    # example, "Mobile Legends 10K" commonly describes in-game diamonds,
    # while "Pulsa 10K" may mean Rp10.000.  Preserve it for human review but
    # deliberately do not turn it into a logical denomination or mapping key.
    if unit == "k":
        return match.group(0).upper(), None, None, True
    if unit in multiplier:
        value = number * multiplier[unit]
        normalized_unit = "IDR"
    elif unit in {"gb", "mb", "uc", "diamond", "dm"}:
        value = number
        normalized_unit = unit.upper()
    else:
        return None, None, None, True
    return match.group(0).upper(), format(value.normalize(), "f"), normalized_unit, False


def _safe_raw(row: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for field in SAFE_PROVIDER_FIELDS:
        if field not in row:
            continue
        value = row.get(field)
        if field in {"price"}:
            safe[field] = _decimal(value)
        elif field in {"buyer_product_status", "seller_product_status", "unlimited_stock", "multi"}:
            safe[field] = _flag(value)
        elif field == "stock":
            safe[field] = _integer(value)
        else:
            safe[field] = _optional_text(value)
    return safe


@dataclass(frozen=True)
class NormalizedCandidate:
    provider_code: str
    catalog_scope: str
    provider_sku: str
    seller_identity: Optional[str]
    safe_raw_attributes: dict[str, Any]
    normalized_attributes: dict[str, Any]
    identity_fingerprint: str
    source_row_fingerprint: str
    cost_price: Optional[str]
    buyer_product_status: Optional[int]
    seller_product_status: Optional[int]
    stock: Optional[int]
    unlimited_stock: Optional[int]
    multi: Optional[int]
    start_cut_off: Optional[str]
    end_cut_off: Optional[str]
    availability_state: str
    availability_reason_codes: list[str]
    reason_codes: list[str]
    ambiguous: bool

    def snapshot_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RejectedCandidate:
    reason_codes: list[str]
    safe_raw_attributes: dict[str, Any]

    def snapshot_payload(self) -> dict[str, Any]:
        return {"reason_codes": self.reason_codes, "safe_raw_attributes": self.safe_raw_attributes}


def normalize_digiflazz_prepaid_row(
    row: Mapping[str, Any], *, provider_code: str = "digiflazz", catalog_scope: str = "prepaid"
) -> NormalizedCandidate | RejectedCandidate:
    """Normalize one response row without making a mapping decision."""

    safe_raw = _safe_raw(row)
    sku = _optional_text(row.get("buyer_sku_code"), limit=200)
    name = _optional_text(row.get("product_name"), limit=500)
    if not sku or not name:
        missing = []
        if not sku:
            missing.append("missing_provider_sku")
        if not name:
            missing.append("missing_product_name")
        return RejectedCandidate(reason_codes=missing, safe_raw_attributes=safe_raw)

    seller_identity = _optional_text(row.get("seller_name"), limit=200)
    category = _optional_text(row.get("category"), limit=200)
    brand = _optional_text(row.get("brand"), limit=200)
    product_type = _optional_text(row.get("type"), limit=200)
    description = _optional_text(row.get("desc"), limit=2_000)
    cost_price = _decimal(row.get("price"))
    buyer_status = _flag(row.get("buyer_product_status"))
    seller_status = _flag(row.get("seller_product_status"))
    stock = _integer(row.get("stock"))
    unlimited_stock = _flag(row.get("unlimited_stock"))
    multi = _flag(row.get("multi"))
    start_cut_off = _cutoff(row.get("start_cut_off"))
    end_cut_off = _cutoff(row.get("end_cut_off"))

    # Cost is an essential candidate fact.  Applying an incomplete row over
    # an existing candidate could erase a last-known-good provider cost, so a
    # missing or malformed cost fails closed as a rejected snapshot item.
    if cost_price is None:
        return RejectedCandidate(
            reason_codes=["missing_or_invalid_cost_price"],
            safe_raw_attributes=safe_raw,
        )

    denomination_text, denomination_value, denomination_unit, denomination_ambiguous = _denomination(name)
    unavailable_reasons: list[str] = []
    if buyer_status == 0:
        unavailable_reasons.append("buyer_product_status_inactive")
    if seller_status == 0:
        unavailable_reasons.append("seller_product_status_inactive")
    if stock is not None and stock <= 0 and unlimited_stock != 1:
        unavailable_reasons.append("stock_not_available")
    availability = "UNAVAILABLE" if unavailable_reasons else "AVAILABLE"

    normalized = {
        "normalizer_version": NORMALIZER_VERSION,
        "provider_sku": sku,
        "seller_identity": seller_identity,
        "product_name": name,
        "category": category,
        "brand": brand,
        "type": product_type,
        "description": description,
        "denomination_text": denomination_text,
        "denomination_value": denomination_value,
        "denomination_unit": denomination_unit,
        "product_semantics": (
            f"{(brand or '').casefold()}|{(category or '').casefold()}|{(product_type or '').casefold()}|"
            f"{denomination_value or ''}:{(denomination_unit or '').casefold()}"
            if brand and denomination_value and denomination_unit
            else None
        ),
        "cost_price": cost_price,
        "buyer_product_status": buyer_status,
        "seller_product_status": seller_status,
        "stock": stock,
        "unlimited_stock": unlimited_stock,
        "multi": multi,
        "start_cut_off": start_cut_off,
        "end_cut_off": end_cut_off,
        "availability_state": availability,
        "availability_reason_codes": unavailable_reasons,
    }
    identity_source = {
        "provider_code": provider_code.casefold(),
        "catalog_scope": catalog_scope.casefold(),
        "provider_sku": sku.casefold(),
        "seller_identity": (seller_identity or "").casefold(),
        "version": IDENTITY_FINGERPRINT_VERSION,
    }
    identity_fingerprint = sha256_json(identity_source)
    source_fingerprint = sha256_json(
        {
            "identity": identity_source,
            "safe_raw_attributes": safe_raw,
            "normalized_attributes": normalized,
            "version": SOURCE_ROW_FINGERPRINT_VERSION,
        }
    )
    reason_codes: list[str] = []
    if denomination_ambiguous:
        reason_codes.append("ambiguous_denomination")
    ambiguous = bool(denomination_ambiguous)
    return NormalizedCandidate(
        provider_code=provider_code,
        catalog_scope=catalog_scope,
        provider_sku=sku,
        seller_identity=seller_identity,
        safe_raw_attributes=safe_raw,
        normalized_attributes=normalized,
        identity_fingerprint=identity_fingerprint,
        source_row_fingerprint=source_fingerprint,
        cost_price=cost_price,
        buyer_product_status=buyer_status,
        seller_product_status=seller_status,
        stock=stock,
        unlimited_stock=unlimited_stock,
        multi=multi,
        start_cut_off=start_cut_off,
        end_cut_off=end_cut_off,
        availability_state=availability,
        availability_reason_codes=unavailable_reasons,
        reason_codes=reason_codes,
        ambiguous=ambiguous,
    )


def normalize_digiflazz_prepaid_rows(
    rows: Sequence[Mapping[str, Any]], *, provider_code: str = "digiflazz", catalog_scope: str = "prepaid"
) -> list[NormalizedCandidate | RejectedCandidate]:
    """Return deterministic candidates and conservatively reject duplicates.

    The same provider identity occurring more than once is not arbitrarily
    selected.  Every duplicate is retained only as a rejected snapshot item.
    """

    normalized = [
        normalize_digiflazz_prepaid_row(row, provider_code=provider_code, catalog_scope=catalog_scope)
        for row in rows
    ]
    identity_counts: dict[str, int] = {}
    for candidate in normalized:
        if isinstance(candidate, NormalizedCandidate):
            identity_counts[candidate.identity_fingerprint] = identity_counts.get(candidate.identity_fingerprint, 0) + 1

    output: list[NormalizedCandidate | RejectedCandidate] = []
    for candidate in normalized:
        if isinstance(candidate, NormalizedCandidate) and identity_counts[candidate.identity_fingerprint] > 1:
            output.append(
                RejectedCandidate(
                    reason_codes=["duplicate_provider_identity"],
                    safe_raw_attributes=candidate.safe_raw_attributes,
                )
            )
        else:
            output.append(candidate)
    return sorted(
        output,
        key=lambda item: canonical_json(item.snapshot_payload()),
    )
