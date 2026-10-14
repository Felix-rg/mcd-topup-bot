"""Safe, manually triggered catalog Preview/Review/Apply lifecycle.

The legacy direct-product synchronizer remains below as a deliberately
disabled compatibility facade.  The Phase 2 lifecycle only stores provider
facts in the Phase 1 catalog tables; it never selects a checkout supplier or
publishes/rewrites a storefront product.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

# Keep this import for the inactive legacy facade and its Phase 0 regression
# test.  Phase 2 does not call it.
from app.database import db_execute, db_transaction
from app.services.catalog_normalizer import (
    IDENTITY_FINGERPRINT_VERSION,
    SOURCE_ROW_FINGERPRINT_VERSION,
    NormalizedCandidate,
    RejectedCandidate,
    canonical_json,
    normalize_digiflazz_prepaid_rows,
    sha256_json,
)
from app.services.digiflazz_catalog_client import (
    DigiflazzCatalogClient,
    DigiflazzCatalogClientError,
)
from app.services.digiflazz_service import get_digiflazz_products


logger = logging.getLogger(__name__)


def _truthy_provider_flag(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "aktif", "active"}


LEGACY_CATALOG_SYNC_DISABLED_CODE = "legacy_catalog_sync_disabled"


async def sync_digiflazz_products() -> Dict[str, Any]:
    """Block the retired direct-upsert path until Preview/Apply replaces it.

    This intentionally performs no price-list request and no database write.
    The former implementation is retained below for controlled removal after
    the replacement catalog lifecycle has shipped.
    """

    return {
        "success": False,
        "code": LEGACY_CATALOG_SYNC_DISABLED_CODE,
        "processed": 0,
        "errors": ["Legacy catalog sync dinonaktifkan; gunakan Preview/Apply saat tersedia"],
        "message": "Legacy catalog sync dinonaktifkan sampai Preview/Apply tersedia",
    }


async def _legacy_direct_upsert_digiflazz_products() -> Dict[str, Any]:
    """Retained inactive implementation; never call from application runtime."""
    provider_batches: list[tuple[str, Any]] = [
        ("prepaid", await get_digiflazz_products(cmd="prepaid")),
        ("postpaid", await get_digiflazz_products(cmd="pasca")),
    ]

    processed = 0
    errors: list[str] = []
    for product_type, provider_products in provider_batches:
        if isinstance(provider_products, str):
            errors.append(f"{product_type}: {provider_products}")
            continue
        if not isinstance(provider_products, list):
            errors.append(f"{product_type}: Format data provider tidak valid")
            continue

        for item in provider_products:
            if not isinstance(item, dict):
                continue
            sku = item.get("sku") or item.get("buyer_sku_code") or item.get("product_code")
            if not sku:
                continue

            provider = item.get("brand") or item.get("provider") or item.get("seller_name") or "DIGIFLAZZ"
            name = item.get("product_name") or item.get("name") or sku
            provider_price = item.get("price") or item.get("sell_price") or item.get("price_sales") or 0
            cost_price = item.get("cost_price") or provider_price or 0
            category = item.get("category") or ("Pascabayar" if product_type == "postpaid" else "Lainnya")
            buyer_status = 1 if _truthy_provider_flag(item.get("buyer_product_status"), default=True) else 0
            seller_status = 1 if _truthy_provider_flag(item.get("seller_product_status"), default=True) else 0
            active = 1 if buyer_status and seller_status else 0

            await db_execute(
                """
                INSERT INTO products (
                    sku, provider, name, cost_price, price, active, category,
                    product_type, brand, provider_type, buyer_product_status,
                    seller_product_status, stock, unlimited_stock, multi,
                    start_cut_off, end_cut_off, admin_fee, commission,
                    provider_description
                )
                VALUES (
                    :sku, :provider, :name, :cost_price, :price, :active, :category,
                    :product_type, :brand, :provider_type, :buyer_product_status,
                    :seller_product_status, :stock, :unlimited_stock, :multi,
                    :start_cut_off, :end_cut_off, :admin_fee, :commission,
                    :provider_description
                )
                ON CONFLICT(sku) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    name = EXCLUDED.name,
                    cost_price = EXCLUDED.cost_price,
                    price = CASE
                        WHEN products.price IS NULL OR products.price <= products.cost_price THEN EXCLUDED.price
                        ELSE products.price
                    END,
                    active = EXCLUDED.active,
                    category = EXCLUDED.category,
                    product_type = EXCLUDED.product_type,
                    brand = EXCLUDED.brand,
                    provider_type = EXCLUDED.provider_type,
                    buyer_product_status = EXCLUDED.buyer_product_status,
                    seller_product_status = EXCLUDED.seller_product_status,
                    stock = EXCLUDED.stock,
                    unlimited_stock = EXCLUDED.unlimited_stock,
                    multi = EXCLUDED.multi,
                    start_cut_off = EXCLUDED.start_cut_off,
                    end_cut_off = EXCLUDED.end_cut_off,
                    admin_fee = EXCLUDED.admin_fee,
                    commission = EXCLUDED.commission,
                    provider_description = EXCLUDED.provider_description
                """,
                {
                    "sku": sku,
                    "provider": provider,
                    "name": name,
                    "cost_price": cost_price,
                    "price": provider_price,
                    "active": active,
                    "category": category,
                    "product_type": product_type,
                    "brand": item.get("brand") or provider,
                    "provider_type": item.get("type") or item.get("product_type") or "",
                    "buyer_product_status": buyer_status,
                    "seller_product_status": seller_status,
                    "stock": item.get("stock"),
                    "unlimited_stock": 1 if _truthy_provider_flag(item.get("unlimited_stock"), default=False) else 0,
                    "multi": 1 if _truthy_provider_flag(item.get("multi"), default=False) else 0,
                    "start_cut_off": item.get("start_cut_off") or "",
                    "end_cut_off": item.get("end_cut_off") or "",
                    "admin_fee": item.get("admin") or item.get("admin_fee") or 0,
                    "commission": item.get("commission") or 0,
                    "provider_description": item.get("desc") or item.get("description") or "",
                },
            )
            processed += 1

    return {
        "success": not errors,
        "processed": processed,
        "errors": errors,
        "message": f"Sinkronisasi selesai: {processed} produk diproses"
        + (f" ({'; '.join(errors)})" if errors else ""),
    }


# ---------------------------------------------------------------------------
# Phase 2 lifecycle.  Nothing below is called by the background engine or the
# legacy /admin/sync-products endpoint.
# ---------------------------------------------------------------------------

CATALOG_PROVIDER_DIGIFLAZZ = "digiflazz"
CATALOG_SCOPE_PREPAID = "prepaid"
CATALOG_SNAPSHOT_HASH_VERSION = "catalog-preview-snapshot-v1"
CATALOG_LOCK_TTL_SECONDS = 120
CATALOG_PREVIEW_COOLDOWN_SECONDS = 300
CATALOG_LOGICAL_KEY_VERSION = "catalog-phase2-manual-v1"

RUN_OPERATION_PREVIEW = "PREVIEW"
RUN_OPERATION_APPLY = "APPLY"
RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_PREVIEW_READY = "PREVIEW_READY"
RUN_STATUS_APPLYING = "APPLYING"
RUN_STATUS_APPLIED = "APPLIED"
RUN_STATUS_FAILED = "FAILED"

MAPPING_STATUS_PENDING = "PENDING"
MAPPING_STATUS_APPROVED = "APPROVED"
MAPPING_STATUS_REJECTED = "REJECTED"

ACTION_NEW = "NEW"
ACTION_CHANGED = "CHANGED"
ACTION_UNCHANGED = "UNCHANGED"
ACTION_UNAVAILABLE = "UNAVAILABLE"
ACTION_MAPPING_REQUIRED = "MAPPING_REQUIRED"
ACTION_REJECTED = "REJECTED"

_SAFE_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,119}$")


class CatalogSyncError(RuntimeError):
    """Base lifecycle error whose text is safe to return from the admin API."""

    code = "catalog_sync_error"
    http_status = 409

    def __init__(self, message: str, *, code: Optional[str] = None, http_status: Optional[int] = None) -> None:
        super().__init__(message)
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        self.safe_message = message


class CatalogSyncNotFound(CatalogSyncError):
    code = "catalog_not_found"
    http_status = 404


class CatalogSyncValidationError(CatalogSyncError):
    code = "catalog_validation_error"
    http_status = 400


class CatalogSyncBusy(CatalogSyncError):
    code = "catalog_sync_busy"
    http_status = 409


class CatalogSyncRateLimited(CatalogSyncError):
    code = "catalog_sync_cooldown"
    http_status = 409


class CatalogSyncBlocked(CatalogSyncError):
    code = "catalog_snapshot_not_ready"
    http_status = 409


class CatalogSyncStorageError(CatalogSyncError):
    code = "catalog_storage_unavailable"
    http_status = 503


@dataclass(frozen=True)
class _CatalogLease:
    provider_code: str
    catalog_scope: str
    owner_token: str
    run_id: str
    fencing_generation: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _database_timestamp(value: Optional[datetime] = None) -> datetime:
    """Return a UTC-naive value for ``TIMESTAMP`` database parameters.

    The Phase 1 schema intentionally uses PostgreSQL ``TIMESTAMP`` (without
    time zone).  Passing a display string works with SQLite, but asyncpg's
    native timestamp codec correctly requires a ``datetime``.  Keeping this
    conversion at the SQL boundary gives SQLite and PostgreSQL the same UTC
    instant without relying on an implicit driver cast.
    """

    timestamp = value or _utc_now()
    if timestamp.tzinfo is None:
        return timestamp.replace(microsecond=0)
    return timestamp.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _display_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return _timestamp(value)
    raw = str(value).strip()
    return raw or None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _json_load_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_load_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _json_dump(value: Any) -> str:
    return canonical_json(value)


async def _fetch_rows(
    connection: AsyncConnection,
    statement: str,
    values: Optional[Mapping[str, Any]] = None,
) -> list[dict[str, Any]]:
    result = await connection.execute(text(statement), dict(values or {}))
    return [dict(row) for row in result.mappings().all()]


async def _fetch_one(
    connection: AsyncConnection,
    statement: str,
    values: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    rows = await _fetch_rows(connection, statement, values)
    return rows[0] if rows else None


async def _execute(
    connection: AsyncConnection,
    statement: str,
    values: Optional[Mapping[str, Any]] = None,
) -> int:
    result = await connection.execute(text(statement), dict(values or {}))
    return int(result.rowcount or 0)


def _validate_provider_scope(provider_code: str, catalog_scope: str) -> tuple[str, str]:
    provider = str(provider_code or "").strip().casefold()
    scope = str(catalog_scope or "").strip().casefold()
    if provider != CATALOG_PROVIDER_DIGIFLAZZ or scope != CATALOG_SCOPE_PREPAID:
        raise CatalogSyncValidationError("Hanya katalog Digiflazz prepaid yang tersedia pada Phase 2.")
    return provider, scope


async def _acquire_lease_on_connection(
    connection: AsyncConnection,
    *,
    provider_code: str,
    catalog_scope: str,
    owner_token: str,
    run_id: str,
) -> Optional[_CatalogLease]:
    """Acquire a durable, fenced provider/scope lease in a short transaction."""

    now = _utc_now()
    expires_at = _database_timestamp(now + timedelta(seconds=CATALOG_LOCK_TTL_SECONDS))
    values = {
        "provider_code": provider_code,
        "catalog_scope": catalog_scope,
        "owner_token": owner_token,
        "run_id": run_id,
        "now": _database_timestamp(now),
        "expires_at": expires_at,
    }
    # PostgreSQL obtains a row lock before updating an existing lease. SQLite
    # serializes the following conditional write through its database writer;
    # no in-memory process lock is used in either case.
    if connection.dialect.name == "postgresql":
        await _fetch_one(
            connection,
            """
            SELECT provider_code
            FROM catalog_sync_locks
            WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
            FOR UPDATE
            """,
            values,
        )

    await _execute(
        connection,
        """
        INSERT INTO catalog_sync_locks (
            provider_code, catalog_scope, owner_token, run_id,
            fencing_generation, acquired_at, expires_at, updated_at
        )
        VALUES (
            :provider_code, :catalog_scope, :owner_token, :run_id,
            1, :now, :expires_at, :now
        )
        ON CONFLICT(provider_code, catalog_scope) DO NOTHING
        """,
        values,
    )
    inserted = await _fetch_one(
        connection,
        """
        SELECT fencing_generation
        FROM catalog_sync_locks
        WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
          AND owner_token=:owner_token AND run_id=:run_id
        """,
        values,
    )
    if inserted:
        return _CatalogLease(
            provider_code=provider_code,
            catalog_scope=catalog_scope,
            owner_token=owner_token,
            run_id=run_id,
            fencing_generation=int(inserted["fencing_generation"]),
        )

    updated = await _execute(
        connection,
        """
        UPDATE catalog_sync_locks
        SET owner_token=:owner_token,
            run_id=:run_id,
            fencing_generation=fencing_generation + 1,
            acquired_at=:now,
            expires_at=:expires_at,
            updated_at=:now
        WHERE provider_code=:provider_code
          AND catalog_scope=:catalog_scope
          AND (owner_token IS NULL OR expires_at IS NULL OR expires_at <= :now)
        """,
        values,
    )
    if updated != 1:
        return None
    acquired = await _fetch_one(
        connection,
        """
        SELECT fencing_generation
        FROM catalog_sync_locks
        WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
          AND owner_token=:owner_token AND run_id=:run_id
        """,
        values,
    )
    if not acquired:
        return None
    return _CatalogLease(
        provider_code=provider_code,
        catalog_scope=catalog_scope,
        owner_token=owner_token,
        run_id=run_id,
        fencing_generation=int(acquired["fencing_generation"]),
    )


async def _acquire_lease(
    *, provider_code: str, catalog_scope: str, run_id: str
) -> _CatalogLease:
    owner_token = str(uuid4())
    async with db_transaction() as connection:
        lease = await _acquire_lease_on_connection(
            connection,
            provider_code=provider_code,
            catalog_scope=catalog_scope,
            owner_token=owner_token,
            run_id=run_id,
        )
    if lease is None:
        raise CatalogSyncBusy("Sinkronisasi katalog sedang berjalan pada proses lain.")
    return lease


async def _renew_lease_on_connection(connection: AsyncConnection, lease: _CatalogLease) -> None:
    now = _utc_now()
    changed = await _execute(
        connection,
        """
        UPDATE catalog_sync_locks
        SET expires_at=:expires_at, updated_at=:now
        WHERE provider_code=:provider_code
          AND catalog_scope=:catalog_scope
          AND owner_token=:owner_token
          AND run_id=:run_id
          AND fencing_generation=:fencing_generation
          AND expires_at > :now
        """,
        {
            "provider_code": lease.provider_code,
            "catalog_scope": lease.catalog_scope,
            "owner_token": lease.owner_token,
            "run_id": lease.run_id,
            "fencing_generation": lease.fencing_generation,
            "now": _database_timestamp(now),
            "expires_at": _database_timestamp(now + timedelta(seconds=CATALOG_LOCK_TTL_SECONDS)),
        },
    )
    if changed != 1:
        raise CatalogSyncBusy("Lease sinkronisasi katalog telah berakhir atau digantikan proses lain.")


async def _release_lease(lease: _CatalogLease) -> None:
    try:
        async with db_transaction() as connection:
            now = _database_timestamp()
            await _execute(
                connection,
                """
                UPDATE catalog_sync_locks
                SET owner_token=NULL, run_id=NULL, expires_at=:now, updated_at=:now
                WHERE provider_code=:provider_code
                  AND catalog_scope=:catalog_scope
                  AND owner_token=:owner_token
                  AND run_id=:run_id
                  AND fencing_generation=:fencing_generation
                """,
                {
                    "provider_code": lease.provider_code,
                    "catalog_scope": lease.catalog_scope,
                    "owner_token": lease.owner_token,
                    "run_id": lease.run_id,
                    "fencing_generation": lease.fencing_generation,
                    "now": now,
                },
            )
    except SQLAlchemyError:
        # An expiry is still fail-closed; do not emit DB driver strings that
        # could include a credential-bearing connection URL.
        logger.warning("Catalog lease release failed without disclosing database details")


async def _reserve_preview(
    *, provider_code: str, catalog_scope: str, actor: str, cooldown_seconds: int
) -> tuple[str, _CatalogLease]:
    run_id = str(uuid4())
    lease = await _acquire_lease(provider_code=provider_code, catalog_scope=catalog_scope, run_id=run_id)
    try:
        async with db_transaction() as connection:
            await _renew_lease_on_connection(connection, lease)
            state = await _fetch_one(
                connection,
                """
                SELECT next_full_fetch_not_before
                FROM catalog_provider_sync_state
                WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
                """,
                {"provider_code": provider_code, "catalog_scope": catalog_scope},
            )
            next_allowed = _parse_timestamp(state.get("next_full_fetch_not_before") if state else None)
            if next_allowed and next_allowed > _utc_now():
                raise CatalogSyncRateLimited(
                    "Price-list katalog masih dalam masa cooldown; coba lagi setelah waktu yang tercatat."
                )
            now = _utc_now()
            now_db = _database_timestamp(now)
            await _execute(
                connection,
                """
                INSERT INTO catalog_sync_runs (
                    run_id, provider_code, catalog_scope, operation, status,
                    actor, source, correlation_id, started_at, updated_at
                ) VALUES (
                    :run_id, :provider_code, :catalog_scope, :operation, :status,
                    :actor, 'admin_catalog_preview', :correlation_id, :started_at, :updated_at
                )
                """,
                {
                    "run_id": run_id,
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "operation": RUN_OPERATION_PREVIEW,
                    "status": RUN_STATUS_RUNNING,
                    "actor": actor,
                    "correlation_id": run_id,
                    "started_at": now_db,
                    "updated_at": now_db,
                },
            )
            next_allowed = _database_timestamp(now + timedelta(seconds=max(1, int(cooldown_seconds))))
            await _execute(
                connection,
                """
                INSERT INTO catalog_provider_sync_state (
                    provider_code, catalog_scope, last_request_at,
                    next_full_fetch_not_before, created_at, updated_at
                ) VALUES (
                    :provider_code, :catalog_scope, :last_request_at,
                    :next_allowed, :now, :now
                )
                ON CONFLICT(provider_code, catalog_scope) DO UPDATE SET
                    last_request_at=EXCLUDED.last_request_at,
                    next_full_fetch_not_before=EXCLUDED.next_full_fetch_not_before,
                    updated_at=EXCLUDED.updated_at
                """,
                {
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "last_request_at": now_db,
                    "next_allowed": next_allowed,
                    "now": now_db,
                },
            )
        return run_id, lease
    except Exception:
        await _release_lease(lease)
        raise


async def _finish_preview_failure(
    *, run_id: str, lease: _CatalogLease, failure_code: str, failure_message: str
) -> None:
    try:
        async with db_transaction() as connection:
            await _renew_lease_on_connection(connection, lease)
            now = _database_timestamp()
            await _execute(
                connection,
                """
                UPDATE catalog_sync_runs
                SET status=:status, finished_at=:now, updated_at=:now,
                    sanitized_error_code=:failure_code,
                    sanitized_error_summary=:failure_message
                WHERE run_id=:run_id AND status=:running_status
                """,
                {
                    "status": RUN_STATUS_FAILED,
                    "now": now,
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "run_id": run_id,
                    "running_status": RUN_STATUS_RUNNING,
                },
            )
            await _execute(
                connection,
                """
                UPDATE catalog_provider_sync_state
                SET last_failure_code=:failure_code,
                    last_failure_summary=:failure_message,
                    updated_at=:now
                WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
                """,
                {
                    "failure_code": failure_code,
                    "failure_message": failure_message,
                    "now": now,
                    "provider_code": lease.provider_code,
                    "catalog_scope": lease.catalog_scope,
                },
            )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog preview failure could not be recorded safely")
    finally:
        await _release_lease(lease)


def _candidate_diff(candidate: NormalizedCandidate, previous: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not previous:
        return {"changed_fields": ["new_candidate"]}
    normalized = candidate.normalized_attributes
    comparisons = {
        "product_name": (previous.get("raw_product_name"), normalized.get("product_name")),
        "category": (previous.get("raw_category"), normalized.get("category")),
        "brand": (previous.get("raw_brand"), normalized.get("brand")),
        "type": (previous.get("raw_type"), normalized.get("type")),
        "cost_price": (str(previous.get("cost_price") or ""), str(normalized.get("cost_price") or "")),
        "availability_state": (previous.get("availability_state"), normalized.get("availability_state")),
    }
    return {
        "changed_fields": [name for name, values in comparisons.items() if str(values[0] or "") != str(values[1] or "")],
        "previous_source_row_fingerprint": str(previous.get("source_row_fingerprint") or "") or None,
    }


def _plan_preview_items(
    normalized_rows: Sequence[NormalizedCandidate | RejectedCandidate],
    existing_candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_identity: dict[str, list[Mapping[str, Any]]] = {}
    for existing in existing_candidates:
        fingerprint = str(existing.get("source_identity_fingerprint") or "")
        if fingerprint:
            existing_by_identity.setdefault(fingerprint, []).append(existing)

    planned: list[dict[str, Any]] = []
    for item in normalized_rows:
        if isinstance(item, RejectedCandidate):
            planned.append(
                {
                    "safe_raw_attributes": item.safe_raw_attributes,
                    "normalized_attributes": {},
                    "identity_fingerprint": None,
                    "source_row_fingerprint": None,
                    "proposed_action": ACTION_REJECTED,
                    "diff": {"changed_fields": []},
                    "reason_codes": list(item.reason_codes),
                    "catalog_product_id": None,
                    "requires_review": False,
                }
            )
            continue

        matches = existing_by_identity.get(item.identity_fingerprint, [])
        previous = matches[0] if len(matches) == 1 else None
        reason_codes = list(item.reason_codes)
        mapped_catalog_id = str(previous.get("catalog_product_id") or "") or None if previous else None
        mapped_state = str(previous.get("mapping_state") or "").upper() if previous else "UNMAPPED"
        if len(matches) > 1:
            action = ACTION_MAPPING_REQUIRED
            reason_codes.append("duplicate_persisted_provider_identity")
            requires_review = True
            mapped_catalog_id = None
        elif item.ambiguous:
            action = ACTION_MAPPING_REQUIRED
            requires_review = True
        elif item.availability_state == "UNAVAILABLE":
            action = ACTION_UNAVAILABLE
            requires_review = not (mapped_catalog_id and mapped_state == MAPPING_STATUS_APPROVED)
        elif previous is None:
            action = ACTION_NEW
            requires_review = True
        elif str(previous.get("source_row_fingerprint") or "") == item.source_row_fingerprint:
            action = ACTION_UNCHANGED
            requires_review = not (mapped_catalog_id and mapped_state == MAPPING_STATUS_APPROVED)
        else:
            action = ACTION_CHANGED
            requires_review = not (mapped_catalog_id and mapped_state == MAPPING_STATUS_APPROVED)

        planned.append(
            {
                "safe_raw_attributes": item.safe_raw_attributes,
                "normalized_attributes": item.normalized_attributes,
                "identity_fingerprint": item.identity_fingerprint,
                "source_row_fingerprint": item.source_row_fingerprint,
                "proposed_action": action,
                "diff": _candidate_diff(item, previous),
                "reason_codes": sorted(set(reason_codes)),
                "catalog_product_id": mapped_catalog_id,
                "requires_review": requires_review,
            }
        )
    return sorted(planned, key=canonical_json)


def _snapshot_hash(items: Iterable[Mapping[str, Any]]) -> str:
    safe_items = []
    for item in items:
        safe_items.append(
            {
                "safe_raw_attributes": dict(item.get("safe_raw_attributes") or {}),
                "normalized_attributes": dict(item.get("normalized_attributes") or {}),
                "identity_fingerprint": item.get("identity_fingerprint"),
                "source_row_fingerprint": item.get("source_row_fingerprint"),
                "proposed_action": item.get("proposed_action"),
                "diff": dict(item.get("diff") or {}),
                "reason_codes": list(item.get("reason_codes") or []),
                "catalog_product_id": item.get("catalog_product_id"),
                "requires_review": bool(item.get("requires_review")),
            }
        )
    return sha256_json(
        {
            "version": CATALOG_SNAPSHOT_HASH_VERSION,
            "items": sorted(safe_items, key=canonical_json),
        }
    )


def _run_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "run_id",
        "provider_code",
        "catalog_scope",
        "operation",
        "status",
        "source_preview_run_id",
        "snapshot_hash",
        "snapshot_hash_version",
        "received_count",
        "valid_count",
        "inserted_count",
        "updated_count",
        "unchanged_count",
        "rejected_count",
        "sanitized_count",
        "ambiguous_count",
        "review_required_count",
        "sanitized_error_code",
        "sanitized_error_summary",
    )
    payload = {key: row.get(key) for key in keys}
    payload["started_at"] = _display_timestamp(row.get("started_at"))
    payload["finished_at"] = _display_timestamp(row.get("finished_at"))
    payload["created_at"] = _display_timestamp(row.get("created_at"))
    payload["updated_at"] = _display_timestamp(row.get("updated_at"))
    return payload


async def _finalize_preview(
    *, run_id: str, lease: _CatalogLease, normalized_rows: Sequence[NormalizedCandidate | RejectedCandidate]
) -> dict[str, Any]:
    async with db_transaction() as connection:
        await _renew_lease_on_connection(connection, lease)
        existing = await _fetch_rows(
            connection,
            """
            SELECT provider_product_id, source_identity_fingerprint,
                   source_row_fingerprint, catalog_product_id, mapping_state,
                   raw_product_name, raw_category, raw_brand, raw_type,
                   cost_price, availability_state
            FROM provider_products
            WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
            """,
            {"provider_code": lease.provider_code, "catalog_scope": lease.catalog_scope},
        )
        planned = _plan_preview_items(normalized_rows, existing)
        snapshot_hash = _snapshot_hash(planned)
        now = _database_timestamp()
        action_counts = {action: 0 for action in (ACTION_NEW, ACTION_CHANGED, ACTION_UNCHANGED, ACTION_UNAVAILABLE, ACTION_MAPPING_REQUIRED, ACTION_REJECTED)}
        for item in planned:
            action_counts[str(item["proposed_action"])] = action_counts.get(str(item["proposed_action"]), 0) + 1
        valid_count = sum(1 for item in normalized_rows if isinstance(item, NormalizedCandidate))
        ambiguous_count = sum(1 for item in normalized_rows if isinstance(item, NormalizedCandidate) and item.ambiguous)
        review_count = sum(1 for item in planned if item["requires_review"])
        rejected_count = action_counts[ACTION_REJECTED]

        for sequence_no, item in enumerate(planned, start=1):
            run_item_id = str(uuid4())
            await _execute(
                connection,
                """
                INSERT INTO catalog_sync_run_items (
                    run_item_id, run_id, sequence_no, safe_raw_attributes_json,
                    normalized_attributes_json, identity_fingerprint,
                    identity_fingerprint_version, source_row_fingerprint,
                    source_row_fingerprint_version, proposed_action, diff_json,
                    reason_codes, provider_product_id, catalog_product_id,
                    requires_review, created_at
                ) VALUES (
                    :run_item_id, :run_id, :sequence_no, :safe_raw_attributes_json,
                    :normalized_attributes_json, :identity_fingerprint,
                    :identity_fingerprint_version, :source_row_fingerprint,
                    :source_row_fingerprint_version, :proposed_action, :diff_json,
                    :reason_codes, NULL, :catalog_product_id,
                    :requires_review, :created_at
                )
                """,
                {
                    "run_item_id": run_item_id,
                    "run_id": run_id,
                    "sequence_no": sequence_no,
                    "safe_raw_attributes_json": _json_dump(item["safe_raw_attributes"]),
                    "normalized_attributes_json": _json_dump(item["normalized_attributes"]),
                    "identity_fingerprint": item["identity_fingerprint"],
                    "identity_fingerprint_version": IDENTITY_FINGERPRINT_VERSION if item["identity_fingerprint"] else None,
                    "source_row_fingerprint": item["source_row_fingerprint"],
                    "source_row_fingerprint_version": SOURCE_ROW_FINGERPRINT_VERSION if item["source_row_fingerprint"] else None,
                    "proposed_action": item["proposed_action"],
                    "diff_json": _json_dump(item["diff"]),
                    "reason_codes": _json_dump(item["reason_codes"]),
                    "catalog_product_id": item["catalog_product_id"],
                    "requires_review": 1 if item["requires_review"] else 0,
                    "created_at": now,
                },
            )
            if item["requires_review"]:
                await _execute(
                    connection,
                    """
                    INSERT INTO catalog_mapping_reviews (
                        review_id, run_item_id, provider_product_id,
                        suggested_catalog_product_id, selected_catalog_product_id,
                        proposed_catalog_product_ids_json, status, evidence_json,
                        reason_codes, decision_note, created_by, created_at, updated_at
                    ) VALUES (
                        :review_id, :run_item_id, NULL,
                        :suggested_catalog_product_id, NULL,
                        :proposed_catalog_product_ids_json, :status, :evidence_json,
                        :reason_codes, NULL, :created_by, :created_at, :updated_at
                    )
                    """,
                    {
                        "review_id": str(uuid4()),
                        "run_item_id": run_item_id,
                        "suggested_catalog_product_id": item["catalog_product_id"],
                        "proposed_catalog_product_ids_json": "[]",
                        "status": MAPPING_STATUS_PENDING,
                        "evidence_json": _json_dump(
                            {
                                "evidence_version": "catalog-mapping-review-v1",
                                "source": "preview",
                                "snapshot_hash": snapshot_hash,
                                "identity_fingerprint": item["identity_fingerprint"],
                                "proposed_action": item["proposed_action"],
                            }
                        ),
                        "reason_codes": _json_dump(item["reason_codes"]),
                        "created_by": "system:catalog-preview",
                        "created_at": now,
                        "updated_at": now,
                    },
                )

        updated = await _execute(
            connection,
            """
            UPDATE catalog_sync_runs
            SET status=:status, snapshot_hash=:snapshot_hash,
                snapshot_hash_version=:snapshot_hash_version, finished_at=:now,
                received_count=:received_count, valid_count=:valid_count,
                inserted_count=:inserted_count, updated_count=:updated_count,
                unchanged_count=:unchanged_count, rejected_count=:rejected_count,
                sanitized_count=:sanitized_count, ambiguous_count=:ambiguous_count,
                review_required_count=:review_required_count, updated_at=:now
            WHERE run_id=:run_id AND status=:running_status
            """,
            {
                "status": RUN_STATUS_PREVIEW_READY,
                "snapshot_hash": snapshot_hash,
                "snapshot_hash_version": CATALOG_SNAPSHOT_HASH_VERSION,
                "now": now,
                "received_count": len(normalized_rows),
                "valid_count": valid_count,
                "inserted_count": action_counts[ACTION_NEW],
                "updated_count": action_counts[ACTION_CHANGED],
                "unchanged_count": action_counts[ACTION_UNCHANGED],
                "rejected_count": rejected_count,
                "sanitized_count": len(normalized_rows),
                "ambiguous_count": ambiguous_count,
                "review_required_count": review_count,
                "run_id": run_id,
                "running_status": RUN_STATUS_RUNNING,
            },
        )
        if updated != 1:
            raise CatalogSyncBusy("Run preview katalog tidak lagi berada pada state yang dapat diselesaikan.")
        run = await _fetch_one(connection, "SELECT * FROM catalog_sync_runs WHERE run_id=:run_id", {"run_id": run_id})
        if not run:
            raise CatalogSyncStorageError("Run preview katalog tidak dapat dibaca kembali.")
        return _run_payload(run)


async def preview_catalog_sync(
    *,
    provider: str = CATALOG_PROVIDER_DIGIFLAZZ,
    scope: str = CATALOG_SCOPE_PREPAID,
    actor: str = "admin",
    client: Optional[DigiflazzCatalogClient] = None,
    cooldown_seconds: int = CATALOG_PREVIEW_COOLDOWN_SECONDS,
) -> dict[str, Any]:
    """Fetch once and create an immutable Preview snapshot.

    Preview persists only the Phase 1 run/item/review/state structures.  It
    never inserts a logical product or provider candidate and therefore cannot
    affect storefront visibility, customer pricing, checkout routing, or an
    outbound transaction.
    """

    provider_code, catalog_scope = _validate_provider_scope(provider, scope)
    try:
        run_id, lease = await _reserve_preview(
            provider_code=provider_code,
            catalog_scope=catalog_scope,
            actor=str(actor or "admin")[:200],
            cooldown_seconds=cooldown_seconds,
        )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog preview reservation failed without database detail")
        raise CatalogSyncStorageError("Penyimpanan katalog tidak tersedia untuk memulai preview.") from None

    try:
        response = await (client or DigiflazzCatalogClient()).fetch_prepaid_price_list()
    except DigiflazzCatalogClientError as exc:
        await _finish_preview_failure(
            run_id=run_id,
            lease=lease,
            failure_code=exc.code,
            failure_message=exc.safe_message,
        )
        raise CatalogSyncError(exc.safe_message, code=exc.code, http_status=409) from None
    except Exception:
        # Never propagate arbitrary client/driver messages to the admin API.
        logger.warning("Catalog preview client failed error_type=unexpected")
        await _finish_preview_failure(
            run_id=run_id,
            lease=lease,
            failure_code="catalog_provider_unexpected_error",
            failure_message="Price-list Digiflazz tidak dapat diproses dengan aman.",
        )
        raise CatalogSyncError(
            "Price-list Digiflazz tidak dapat diproses dengan aman.",
            code="catalog_provider_unexpected_error",
            http_status=409,
        ) from None

    try:
        normalized = normalize_digiflazz_prepaid_rows(
            response.rows,
            provider_code=provider_code,
            catalog_scope=catalog_scope,
        )
        run = await _finalize_preview(run_id=run_id, lease=lease, normalized_rows=normalized)
        return {"success": True, "run": run}
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog preview finalization failed without database detail")
        await _finish_preview_failure(
            run_id=run_id,
            lease=lease,
            failure_code="catalog_storage_error",
            failure_message="Snapshot katalog tidak dapat disimpan dengan aman.",
        )
        raise CatalogSyncStorageError("Snapshot katalog tidak dapat disimpan dengan aman.") from None
    finally:
        await _release_lease(lease)


async def catalog_sync_status(
    *, provider: str = CATALOG_PROVIDER_DIGIFLAZZ, scope: str = CATALOG_SCOPE_PREPAID
) -> dict[str, Any]:
    """Return durable catalog state without fetching a provider price list."""

    provider_code, catalog_scope = _validate_provider_scope(provider, scope)
    try:
        async with db_transaction() as connection:
            state = await _fetch_one(
                connection,
                """
                SELECT s.provider_code, s.catalog_scope, s.last_request_at,
                       s.next_full_fetch_not_before,
                       s.last_successful_full_snapshot_hash,
                       s.last_successful_run_id, s.last_failure_code,
                       s.last_failure_summary, success.finished_at AS last_success_at
                FROM catalog_provider_sync_state s
                LEFT JOIN catalog_sync_runs success
                  ON success.run_id=s.last_successful_run_id
                WHERE s.provider_code=:provider_code AND s.catalog_scope=:catalog_scope
                """,
                {"provider_code": provider_code, "catalog_scope": catalog_scope},
            )
            last_preview = await _fetch_one(
                connection,
                """
                SELECT run_id, status, snapshot_hash, started_at, finished_at
                FROM catalog_sync_runs
                WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
                  AND operation=:operation
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                {
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "operation": RUN_OPERATION_PREVIEW,
                },
            )
    except SQLAlchemyError:
        logger.warning("Catalog status read failed without database detail")
        raise CatalogSyncStorageError("Status katalog belum tersedia.") from None

    state = state or {}
    return {
        "provider_code": provider_code,
        "catalog_scope": catalog_scope,
        "last_attempt_at": _display_timestamp(state.get("last_request_at")),
        "next_allowed_at": _display_timestamp(state.get("next_full_fetch_not_before")),
        "last_success_at": _display_timestamp(state.get("last_success_at")),
        "last_successful_snapshot_hash": state.get("last_successful_full_snapshot_hash"),
        "last_successful_run_id": state.get("last_successful_run_id"),
        "last_failure_code": state.get("last_failure_code"),
        "safe_failure_summary": state.get("last_failure_summary"),
        "last_preview": (
            {
                "run_id": last_preview.get("run_id"),
                "status": last_preview.get("status"),
                "snapshot_hash": last_preview.get("snapshot_hash"),
                "started_at": _display_timestamp(last_preview.get("started_at")),
                "finished_at": _display_timestamp(last_preview.get("finished_at")),
            }
            if last_preview
            else None
        ),
    }


async def list_catalog_sync_runs(
    *,
    provider: str = CATALOG_PROVIDER_DIGIFLAZZ,
    scope: str = CATALOG_SCOPE_PREPAID,
    limit: int = 50,
) -> dict[str, Any]:
    provider_code, catalog_scope = _validate_provider_scope(provider, scope)
    safe_limit = min(max(int(limit), 1), 100)
    try:
        async with db_transaction() as connection:
            rows = await _fetch_rows(
                connection,
                """
                SELECT *
                FROM catalog_sync_runs
                WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope
                ORDER BY started_at DESC, run_id DESC
                LIMIT :limit
                """,
                {"provider_code": provider_code, "catalog_scope": catalog_scope, "limit": safe_limit},
            )
    except SQLAlchemyError:
        logger.warning("Catalog run list failed without database detail")
        raise CatalogSyncStorageError("Daftar run katalog belum tersedia.") from None
    return {"items": [_run_payload(row) for row in rows], "limit": safe_limit}


def _run_item_hash_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "safe_raw_attributes": _json_load_object(row.get("safe_raw_attributes_json")),
        "normalized_attributes": _json_load_object(row.get("normalized_attributes_json")),
        "identity_fingerprint": row.get("identity_fingerprint"),
        "source_row_fingerprint": row.get("source_row_fingerprint"),
        "proposed_action": row.get("proposed_action"),
        "diff": _json_load_object(row.get("diff_json")),
        "reason_codes": _json_load_list(row.get("reason_codes")),
        "catalog_product_id": row.get("catalog_product_id"),
        "requires_review": bool(row.get("requires_review")),
    }


async def _preview_apply_readiness(
    connection: AsyncConnection,
    run: Mapping[str, Any],
    items: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Validate immutable snapshot and review evidence without mutating it."""

    blocked: list[str] = []
    if str(run.get("operation") or "") != RUN_OPERATION_PREVIEW:
        blocked.append("run_is_not_preview")
    if str(run.get("status") or "") != RUN_STATUS_PREVIEW_READY:
        blocked.append("preview_not_ready")
    rows = list(items) if items is not None else await _fetch_rows(
        connection,
        "SELECT * FROM catalog_sync_run_items WHERE run_id=:run_id ORDER BY sequence_no",
        {"run_id": run["run_id"]},
    )
    expected_hash = str(run.get("snapshot_hash") or "")
    if not expected_hash or expected_hash != _snapshot_hash(_run_item_hash_payload(row) for row in rows):
        blocked.append("snapshot_hash_mismatch")

    # A source row that the normalizer rejected (malformed identity, duplicate
    # provider identity, missing essential cost, and so on) means this is not
    # a complete provider snapshot.  It must be visible for review, but it is
    # not safe to treat it as a successful full apply or to replace the
    # last-known-good catalog state with a partial observation.
    if any(str(row.get("proposed_action") or "") == ACTION_REJECTED for row in rows):
        blocked.append("snapshot_contains_rejected_items")

    required_rows = [row for row in rows if bool(row.get("requires_review"))]
    if required_rows:
        review_rows = await _fetch_rows(
            connection,
            """
            SELECT review_id, run_item_id, status, selected_catalog_product_id
            FROM catalog_mapping_reviews
            WHERE run_item_id IN (
                SELECT run_item_id FROM catalog_sync_run_items WHERE run_id=:run_id
            )
            """,
            {"run_id": run["run_id"]},
        )
        review_by_item: dict[str, list[Mapping[str, Any]]] = {}
        for review in review_rows:
            review_by_item.setdefault(str(review.get("run_item_id") or ""), []).append(review)
        for item in required_rows:
            matches = review_by_item.get(str(item.get("run_item_id") or ""), [])
            if len(matches) != 1:
                blocked.append("review_evidence_incomplete")
                continue
            status = str(matches[0].get("status") or "").upper()
            if status not in {MAPPING_STATUS_APPROVED, MAPPING_STATUS_REJECTED}:
                blocked.append("review_pending")
            if status == MAPPING_STATUS_APPROVED and not matches[0].get("selected_catalog_product_id"):
                blocked.append("approved_review_missing_logical_product")

    applied = await _fetch_one(
        connection,
        """
        SELECT run_id FROM catalog_sync_runs
        WHERE source_preview_run_id=:run_id AND operation=:operation AND status=:status
        ORDER BY finished_at DESC, run_id DESC
        LIMIT 1
        """,
        {"run_id": run["run_id"], "operation": RUN_OPERATION_APPLY, "status": RUN_STATUS_APPLIED},
    )
    if applied:
        blocked.append("already_applied")
    return {
        "can_apply": not blocked,
        "blocked_reasons": sorted(set(blocked)),
        "already_applied_run_id": applied.get("run_id") if applied else None,
        "items": rows,
    }


def _display_run_item(row: Mapping[str, Any], review: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    normalized = _json_load_object(row.get("normalized_attributes_json"))
    safe_raw = _json_load_object(row.get("safe_raw_attributes_json"))
    review_payload: Optional[dict[str, Any]] = None
    if review:
        review_payload = {
            "review_id": review.get("review_id"),
            "status": review.get("status"),
            "suggested_catalog_product_id": review.get("suggested_catalog_product_id"),
            "selected_catalog_product_id": review.get("selected_catalog_product_id"),
            "reason_codes": _json_load_list(review.get("reason_codes")),
            "decision_note": review.get("decision_note"),
            "reviewed_by": review.get("reviewed_by"),
            "reviewed_at": _display_timestamp(review.get("reviewed_at")),
        }
    return {
        "run_item_id": row.get("run_item_id"),
        "sequence_no": row.get("sequence_no"),
        "provider_sku": normalized.get("provider_sku"),
        "product_name": normalized.get("product_name"),
        "category": normalized.get("category"),
        "brand": normalized.get("brand"),
        "type": normalized.get("type"),
        "cost_price": normalized.get("cost_price"),
        "availability_state": normalized.get("availability_state"),
        "buyer_product_status": normalized.get("buyer_product_status"),
        "seller_product_status": normalized.get("seller_product_status"),
        "stock": normalized.get("stock"),
        "unlimited_stock": normalized.get("unlimited_stock"),
        "start_cut_off": normalized.get("start_cut_off"),
        "end_cut_off": normalized.get("end_cut_off"),
        "proposed_action": row.get("proposed_action"),
        "diff": _json_load_object(row.get("diff_json")),
        "reason_codes": _json_load_list(row.get("reason_codes")),
        "catalog_product_id": row.get("catalog_product_id"),
        "requires_review": bool(row.get("requires_review")),
        "review": review_payload,
        # The response deliberately omits raw provider records.  ``safe_raw``
        # stays internal except for documented display facts above.
        "metadata_present": bool(safe_raw),
    }


async def get_catalog_sync_run_detail(run_id: str) -> dict[str, Any]:
    run_id = str(run_id or "").strip()
    if not run_id:
        raise CatalogSyncValidationError("Run katalog wajib diisi.")
    try:
        async with db_transaction() as connection:
            run = await _fetch_one(connection, "SELECT * FROM catalog_sync_runs WHERE run_id=:run_id", {"run_id": run_id})
            if not run:
                raise CatalogSyncNotFound("Run katalog tidak ditemukan.")
            items = await _fetch_rows(
                connection,
                "SELECT * FROM catalog_sync_run_items WHERE run_id=:run_id ORDER BY sequence_no",
                {"run_id": run_id},
            )
            reviews = await _fetch_rows(
                connection,
                """
                SELECT * FROM catalog_mapping_reviews
                WHERE run_item_id IN (
                    SELECT run_item_id FROM catalog_sync_run_items WHERE run_id=:run_id
                )
                ORDER BY created_at, review_id
                """,
                {"run_id": run_id},
            )
            reviews_by_item: dict[str, list[Mapping[str, Any]]] = {}
            for review in reviews:
                reviews_by_item.setdefault(str(review.get("run_item_id") or ""), []).append(review)
            # A duplicate review record is intentionally surfaced as a blocked
            # condition, never resolved by arbitrarily selecting one.
            item_reviews = {
                item_id: values[0] for item_id, values in reviews_by_item.items() if len(values) == 1
            }
            readiness = await _preview_apply_readiness(connection, run, items)
            if any(len(values) != 1 for values in reviews_by_item.values()):
                readiness["can_apply"] = False
                readiness["blocked_reasons"] = sorted(
                    set([*readiness["blocked_reasons"], "duplicate_review_evidence"])
                )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog run detail failed without database detail")
        raise CatalogSyncStorageError("Detail run katalog belum tersedia.") from None
    return {
        "run": _run_payload(run),
        "items": [_display_run_item(item, item_reviews.get(str(item.get("run_item_id") or ""))) for item in items],
        "can_apply": readiness["can_apply"],
        "blocked_reasons": readiness["blocked_reasons"],
        "already_applied_run_id": readiness["already_applied_run_id"],
    }


def _clean_operator_text(value: Any, *, field: str, required: bool = False, limit: int = 500) -> Optional[str]:
    cleaned = " ".join(str(value or "").strip().split())[:limit]
    if required and not cleaned:
        raise CatalogSyncValidationError(f"{field} wajib diisi.")
    return cleaned or None


def _operator_selling_price(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise CatalogSyncValidationError("Harga jual logical product wajib diisi secara eksplisit.")
    try:
        from decimal import Decimal, InvalidOperation

        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise CatalogSyncValidationError("Harga jual logical product tidak valid.") from None
    if not price.is_finite() or price < 0:
        raise CatalogSyncValidationError("Harga jual logical product tidak valid.")
    return format(price.normalize(), "f")


async def _create_unpublished_logical_product(
    connection: AsyncConnection,
    *,
    product_input: Mapping[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Create only an explicit operator-owned, unpublished logical product."""

    sku = str(product_input.get("sku") or "").strip().casefold()
    if not _SAFE_PROVIDER.fullmatch(sku):
        raise CatalogSyncValidationError(
            "SKU logical product harus 3–120 karakter huruf kecil, angka, titik, strip, atau garis bawah."
        )
    name = _clean_operator_text(product_input.get("name"), field="Nama logical product", required=True, limit=255)
    assert name is not None
    price = _operator_selling_price(product_input.get("selling_price"))
    category = _clean_operator_text(product_input.get("category"), field="Kategori", limit=200) or "Uncategorized"
    provider_label = _clean_operator_text(product_input.get("provider_label"), field="Label provider", limit=200) or "LIXAFA"
    description = _clean_operator_text(product_input.get("description"), field="Deskripsi", limit=2_000)
    brand = _clean_operator_text(product_input.get("brand"), field="Brand", limit=200)
    product_semantics = _clean_operator_text(product_input.get("product_semantics"), field="Semantik", limit=500)
    existing = await _fetch_one(connection, "SELECT sku FROM products WHERE sku=:sku", {"sku": sku})
    if existing:
        raise CatalogSyncValidationError("SKU logical product sudah digunakan.")

    catalog_product_id = str(uuid4())
    now = _timestamp()
    await _execute(
        connection,
        """
        INSERT INTO products (
            sku, provider, name, cost_price, price, active, category,
            product_type, brand, description, catalog_product_id, logical_key,
            logical_key_version, product_semantics, store_enabled
        ) VALUES (
            :sku, :provider, :name, NULL, :price, 0, :category,
            'prepaid', :brand, :description, :catalog_product_id, :logical_key,
            :logical_key_version, :product_semantics, 0
        )
        """,
        {
            "sku": sku,
            "provider": provider_label,
            "name": name,
            "price": price,
            "category": category,
            "brand": brand,
            "description": description,
            "catalog_product_id": catalog_product_id,
            "logical_key": f"catalog-manual:{sku}",
            "logical_key_version": CATALOG_LOGICAL_KEY_VERSION,
            "product_semantics": product_semantics,
        },
    )
    return {
        "catalog_product_id": catalog_product_id,
        "sku": sku,
        "name": name,
        "selling_price": price,
        "active": False,
        "store_enabled": False,
        "created_by": actor,
        "created_at": now,
    }


async def decide_catalog_mapping(
    *,
    review_id: str,
    decision: str,
    actor: str,
    catalog_product_id: Optional[str] = None,
    create_logical_product: Optional[Mapping[str, Any]] = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Record an explicit review decision without changing a snapshot row."""

    review_id = str(review_id or "").strip()
    action = str(decision or "").strip().casefold()
    if not review_id:
        raise CatalogSyncValidationError("Review mapping wajib diisi.")
    if action not in {"attach_existing", "create_unpublished", "reject", "pending"}:
        raise CatalogSyncValidationError("Keputusan mapping tidak dikenal.")
    clean_note = _clean_operator_text(note, field="Catatan", limit=1_000)
    try:
        async with db_transaction() as connection:
            # Apply takes a row lock on the source preview before it reads
            # review evidence.  Taking that same parent lock here makes the
            # review decision and its evidence atomic with respect to Apply:
            # either Apply observes this completed decision, or a late review
            # observes the APPLIED state and is rejected.  The review row is
            # locked too, so concurrent reviewers cannot silently overwrite
            # one another's decision on PostgreSQL.
            locking_suffix = " FOR UPDATE OF r, catalog_run" if connection.dialect.name == "postgresql" else ""
            review = await _fetch_one(
                connection,
                """
                SELECT r.*, item.run_id, catalog_run.status AS run_status, catalog_run.snapshot_hash
                FROM catalog_mapping_reviews r
                JOIN catalog_sync_run_items item ON item.run_item_id=r.run_item_id
                JOIN catalog_sync_runs catalog_run ON catalog_run.run_id=item.run_id
                WHERE r.review_id=:review_id
                """ + locking_suffix,
                {"review_id": review_id},
            )
            if not review:
                raise CatalogSyncNotFound("Review mapping tidak ditemukan.")
            if str(review.get("run_status") or "") != RUN_STATUS_PREVIEW_READY:
                raise CatalogSyncBlocked("Mapping hanya dapat diubah pada preview yang belum diaplikasikan.")
            duplicates = await _fetch_one(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM catalog_mapping_reviews
                WHERE run_item_id=:run_item_id
                """,
                {"run_item_id": review.get("run_item_id")},
            )
            if not duplicates or int(duplicates.get("count") or 0) != 1:
                raise CatalogSyncBlocked("Bukti review mapping ganda terdeteksi; apply dihentikan.")

            selected_catalog_product_id: Optional[str] = None
            created_product: Optional[dict[str, Any]] = None
            status = MAPPING_STATUS_PENDING
            if action == "attach_existing":
                selected_catalog_product_id = str(catalog_product_id or "").strip()
                if not selected_catalog_product_id:
                    raise CatalogSyncValidationError("Catalog product ID wajib dipilih untuk attach.")
                product = await _fetch_one(
                    connection,
                    """
                    SELECT catalog_product_id, sku, name, active, store_enabled
                    FROM products
                    WHERE catalog_product_id=:catalog_product_id
                    """,
                    {"catalog_product_id": selected_catalog_product_id},
                )
                if not product:
                    raise CatalogSyncValidationError("Logical product yang dipilih tidak ditemukan.")
                status = MAPPING_STATUS_APPROVED
            elif action == "create_unpublished":
                if not isinstance(create_logical_product, Mapping):
                    raise CatalogSyncValidationError("Data logical product unpublished wajib diisi.")
                created_product = await _create_unpublished_logical_product(
                    connection,
                    product_input=create_logical_product,
                    actor=actor,
                )
                selected_catalog_product_id = str(created_product["catalog_product_id"])
                status = MAPPING_STATUS_APPROVED
            elif action == "reject":
                status = MAPPING_STATUS_REJECTED

            now = _utc_now()
            now_db = _database_timestamp(now)
            evidence = _json_load_object(review.get("evidence_json"))
            evidence["last_decision"] = {
                "action": action,
                "actor": str(actor or "admin")[:200],
                "at": _timestamp(now),
                "selected_catalog_product_id": selected_catalog_product_id,
                "created_unpublished": bool(created_product),
            }
            await _execute(
                connection,
                """
                UPDATE catalog_mapping_reviews
                SET selected_catalog_product_id=:selected_catalog_product_id,
                    status=:status, evidence_json=:evidence_json,
                    decision_note=:decision_note, reviewed_by=:reviewed_by,
                    reviewed_at=:reviewed_at, updated_at=:updated_at
                WHERE review_id=:review_id
                """,
                {
                    "selected_catalog_product_id": selected_catalog_product_id,
                    "status": status,
                    "evidence_json": _json_dump(evidence),
                    "decision_note": clean_note,
                    "reviewed_by": str(actor or "admin")[:200],
                    "reviewed_at": now_db if action != "pending" else None,
                    "updated_at": now_db,
                    "review_id": review_id,
                },
            )
            updated = await _fetch_one(
                connection,
                "SELECT * FROM catalog_mapping_reviews WHERE review_id=:review_id",
                {"review_id": review_id},
            )
            if not updated:
                raise CatalogSyncStorageError("Keputusan mapping tidak dapat dibaca kembali.")
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog mapping decision failed without database detail")
        raise CatalogSyncStorageError("Keputusan mapping tidak dapat disimpan dengan aman.") from None

    return {
        "success": True,
        "review": {
            "review_id": updated.get("review_id"),
            "status": updated.get("status"),
            "selected_catalog_product_id": updated.get("selected_catalog_product_id"),
            "decision_note": updated.get("decision_note"),
            "reviewed_by": updated.get("reviewed_by"),
            "reviewed_at": _display_timestamp(updated.get("reviewed_at")),
        },
        "created_logical_product": created_product,
    }


def _candidate_from_snapshot_item(item: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    normalized = _json_load_object(item.get("normalized_attributes_json"))
    identity = str(item.get("identity_fingerprint") or "")
    sku = str(normalized.get("provider_sku") or "").strip()
    name = str(normalized.get("product_name") or "").strip()
    if not identity or not sku or not name:
        return None
    return {
        "provider_sku": sku,
        "seller_identity": normalized.get("seller_identity"),
        "raw_product_name": name,
        "raw_category": normalized.get("category"),
        "raw_brand": normalized.get("brand"),
        "raw_type": normalized.get("type"),
        "raw_description": normalized.get("description"),
        "raw_attributes_json": _json_dump(_json_load_object(item.get("safe_raw_attributes_json"))),
        "cost_price": normalized.get("cost_price"),
        "buyer_product_status": normalized.get("buyer_product_status"),
        "seller_product_status": normalized.get("seller_product_status"),
        "stock": normalized.get("stock"),
        "unlimited_stock": normalized.get("unlimited_stock"),
        "multi": normalized.get("multi"),
        "start_cut_off": normalized.get("start_cut_off"),
        "end_cut_off": normalized.get("end_cut_off"),
        "availability_state": normalized.get("availability_state") or "UNKNOWN",
        "availability_reason_codes": _json_dump(normalized.get("availability_reason_codes") or []),
        "identity_fingerprint": identity,
        "source_row_fingerprint": str(item.get("source_row_fingerprint") or ""),
    }


async def _validate_selected_product(
    connection: AsyncConnection, catalog_product_id: str
) -> None:
    product = await _fetch_one(
        connection,
        "SELECT catalog_product_id FROM products WHERE catalog_product_id=:catalog_product_id",
        {"catalog_product_id": catalog_product_id},
    )
    if not product:
        raise CatalogSyncBlocked("Logical product hasil review tidak lagi tersedia.")


async def apply_catalog_snapshot(*, preview_run_id: str, actor: str) -> dict[str, Any]:
    """Apply one fully reviewed immutable Preview snapshot.

    This function makes no provider HTTP request.  It operates entirely in a
    fenced database transaction and only mutates provider candidates, mapping
    evidence, run/state metadata, and (when explicitly created during review)
    an already-unpublished logical product.
    """

    preview_run_id = str(preview_run_id or "").strip()
    if not preview_run_id:
        raise CatalogSyncValidationError("Preview run ID wajib diisi.")
    try:
        async with db_transaction() as connection:
            initial = await _fetch_one(
                connection,
                """
                SELECT run_id, provider_code, catalog_scope, operation
                FROM catalog_sync_runs WHERE run_id=:run_id
                """,
                {"run_id": preview_run_id},
            )
            if not initial:
                raise CatalogSyncNotFound("Preview katalog tidak ditemukan.")
            if str(initial.get("operation") or "") != RUN_OPERATION_PREVIEW:
                raise CatalogSyncValidationError("Run yang dipilih bukan preview katalog.")
            provider_code, catalog_scope = _validate_provider_scope(
                str(initial.get("provider_code") or ""), str(initial.get("catalog_scope") or "")
            )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog apply lookup failed without database detail")
        raise CatalogSyncStorageError("Preview katalog tidak dapat dibaca.") from None

    apply_run_id = str(uuid4())
    try:
        lease = await _acquire_lease(
            provider_code=provider_code,
            catalog_scope=catalog_scope,
            run_id=apply_run_id,
        )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog apply lock failed without database detail")
        raise CatalogSyncStorageError("Lock apply katalog tidak tersedia.") from None

    try:
        async with db_transaction() as connection:
            await _renew_lease_on_connection(connection, lease)
            locking_suffix = " FOR UPDATE" if connection.dialect.name == "postgresql" else ""
            run = await _fetch_one(
                connection,
                "SELECT * FROM catalog_sync_runs WHERE run_id=:run_id" + locking_suffix,
                {"run_id": preview_run_id},
            )
            if not run:
                raise CatalogSyncNotFound("Preview katalog tidak ditemukan.")
            items = await _fetch_rows(
                connection,
                "SELECT * FROM catalog_sync_run_items WHERE run_id=:run_id ORDER BY sequence_no",
                {"run_id": preview_run_id},
            )
            readiness = await _preview_apply_readiness(connection, run, items)
            if readiness["already_applied_run_id"]:
                applied_run = await _fetch_one(
                    connection,
                    "SELECT * FROM catalog_sync_runs WHERE run_id=:run_id",
                    {"run_id": readiness["already_applied_run_id"]},
                )
                return {
                    "success": True,
                    "already_applied": True,
                    "run": _run_payload(applied_run or {"run_id": readiness["already_applied_run_id"]}),
                }
            if not readiness["can_apply"]:
                raise CatalogSyncBlocked(
                    "Preview belum memenuhi syarat apply: " + ", ".join(readiness["blocked_reasons"])
                )

            reviews = await _fetch_rows(
                connection,
                """
                SELECT * FROM catalog_mapping_reviews
                WHERE run_item_id IN (
                    SELECT run_item_id FROM catalog_sync_run_items WHERE run_id=:run_id
                )
                ORDER BY created_at, review_id
                """,
                {"run_id": preview_run_id},
            )
            reviews_by_item: dict[str, Mapping[str, Any]] = {}
            for review in reviews:
                item_id = str(review.get("run_item_id") or "")
                if item_id in reviews_by_item:
                    raise CatalogSyncBlocked("Bukti review mapping ganda terdeteksi; apply dihentikan.")
                reviews_by_item[item_id] = review
            for item in items:
                if not bool(item.get("requires_review")):
                    continue
                review = reviews_by_item.get(str(item.get("run_item_id") or ""))
                if not review or str(review.get("status") or "").upper() not in {
                    MAPPING_STATUS_APPROVED,
                    MAPPING_STATUS_REJECTED,
                }:
                    raise CatalogSyncBlocked("Masih ada mapping kandidat yang belum direview.")
                if str(review.get("status") or "").upper() == MAPPING_STATUS_APPROVED:
                    await _validate_selected_product(connection, str(review.get("selected_catalog_product_id") or ""))

            existing = await _fetch_rows(
                connection,
                "SELECT * FROM provider_products WHERE provider_code=:provider_code AND catalog_scope=:catalog_scope",
                {"provider_code": provider_code, "catalog_scope": catalog_scope},
            )
            existing_by_identity: dict[str, list[Mapping[str, Any]]] = {}
            for candidate in existing:
                fingerprint = str(candidate.get("source_identity_fingerprint") or "")
                if fingerprint:
                    existing_by_identity.setdefault(fingerprint, []).append(candidate)

            # Validate every exact identity before creating the APPLY run so a
            # corrupt historical duplicate cannot cause partial candidate work.
            for item in items:
                candidate = _candidate_from_snapshot_item(item)
                if candidate and len(existing_by_identity.get(candidate["identity_fingerprint"], [])) > 1:
                    raise CatalogSyncBlocked("Kandidat provider historis ganda memerlukan review manual.")

            now = _database_timestamp()
            await _execute(
                connection,
                """
                INSERT INTO catalog_sync_runs (
                    run_id, provider_code, catalog_scope, operation, status,
                    source_preview_run_id, actor, source, correlation_id,
                    snapshot_hash, snapshot_hash_version, started_at, updated_at
                ) VALUES (
                    :run_id, :provider_code, :catalog_scope, :operation, :status,
                    :source_preview_run_id, :actor, 'admin_catalog_apply', :correlation_id,
                    :snapshot_hash, :snapshot_hash_version, :started_at, :updated_at
                )
                """,
                {
                    "run_id": apply_run_id,
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "operation": RUN_OPERATION_APPLY,
                    "status": RUN_STATUS_APPLYING,
                    "source_preview_run_id": preview_run_id,
                    "actor": str(actor or "admin")[:200],
                    "correlation_id": apply_run_id,
                    "snapshot_hash": run.get("snapshot_hash"),
                    "snapshot_hash_version": run.get("snapshot_hash_version"),
                    "started_at": now,
                    "updated_at": now,
                },
            )

            inserted_count = 0
            updated_count = 0
            unchanged_count = 0
            rejected_count = 0
            observed_identities: set[str] = set()
            for item in items:
                candidate = _candidate_from_snapshot_item(item)
                action = str(item.get("proposed_action") or "")
                review = reviews_by_item.get(str(item.get("run_item_id") or ""))
                review_status = str(review.get("status") or "").upper() if review else ""
                if not candidate:
                    rejected_count += 1
                    continue
                observed_identities.add(candidate["identity_fingerprint"])
                if action == ACTION_REJECTED or review_status == MAPPING_STATUS_REJECTED:
                    rejected_count += 1
                    continue

                existing_matches = existing_by_identity.get(candidate["identity_fingerprint"], [])
                prior = existing_matches[0] if existing_matches else None
                selected_catalog_product_id = (
                    str(review.get("selected_catalog_product_id") or "") if review_status == MAPPING_STATUS_APPROVED else ""
                )
                if not selected_catalog_product_id and prior:
                    selected_catalog_product_id = str(prior.get("catalog_product_id") or "")
                mapping_state = MAPPING_STATUS_APPROVED if selected_catalog_product_id else "UNMAPPED"
                common = {
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "provider_sku": candidate["provider_sku"],
                    "seller_identity": candidate["seller_identity"],
                    "source_identity_fingerprint": candidate["identity_fingerprint"],
                    "source_identity_fingerprint_version": IDENTITY_FINGERPRINT_VERSION,
                    "catalog_product_id": selected_catalog_product_id or None,
                    "mapping_state": mapping_state,
                    "mapping_provenance": "admin_review" if selected_catalog_product_id else "preview_apply",
                    "mapping_key_version": CATALOG_LOGICAL_KEY_VERSION if selected_catalog_product_id else None,
                    "mapping_actor": str(actor or "admin")[:200] if selected_catalog_product_id else None,
                    "mapping_decided_at": now if selected_catalog_product_id else None,
                    "raw_product_name": candidate["raw_product_name"],
                    "raw_category": candidate["raw_category"],
                    "raw_brand": candidate["raw_brand"],
                    "raw_type": candidate["raw_type"],
                    "raw_description": candidate["raw_description"],
                    "raw_attributes_json": candidate["raw_attributes_json"],
                    "cost_price": candidate["cost_price"],
                    "buyer_product_status": candidate["buyer_product_status"],
                    "seller_product_status": candidate["seller_product_status"],
                    "stock": candidate["stock"],
                    "unlimited_stock": candidate["unlimited_stock"],
                    "multi": candidate["multi"],
                    "start_cut_off": candidate["start_cut_off"],
                    "end_cut_off": candidate["end_cut_off"],
                    "availability_state": candidate["availability_state"],
                    "availability_reason_codes": candidate["availability_reason_codes"],
                    "source_row_fingerprint": candidate["source_row_fingerprint"],
                    "source_row_fingerprint_version": SOURCE_ROW_FINGERPRINT_VERSION,
                    "last_seen_run_id": apply_run_id,
                    "last_seen_at": now,
                    "last_known_good_at": now if candidate["availability_state"] == "AVAILABLE" else (prior.get("last_known_good_at") if prior else None),
                    "updated_at": now,
                }
                if prior is None:
                    provider_product_id = str(uuid4())
                    await _execute(
                        connection,
                        """
                        INSERT INTO provider_products (
                            provider_product_id, provider_code, catalog_scope, provider_sku,
                            seller_identity, source_identity_fingerprint,
                            source_identity_fingerprint_version, catalog_product_id,
                            mapping_state, mapping_provenance, mapping_key_version,
                            mapping_actor, mapping_decided_at, raw_product_name,
                            raw_category, raw_brand, raw_type, raw_description,
                            raw_attributes_json, cost_price, buyer_product_status,
                            seller_product_status, stock, unlimited_stock, multi,
                            start_cut_off, end_cut_off, availability_state,
                            availability_reason_codes, source_row_fingerprint,
                            source_row_fingerprint_version, first_seen_run_id,
                            last_seen_run_id, first_seen_at, last_seen_at,
                            last_known_good_at, created_at, updated_at
                        ) VALUES (
                            :provider_product_id, :provider_code, :catalog_scope, :provider_sku,
                            :seller_identity, :source_identity_fingerprint,
                            :source_identity_fingerprint_version, :catalog_product_id,
                            :mapping_state, :mapping_provenance, :mapping_key_version,
                            :mapping_actor, :mapping_decided_at, :raw_product_name,
                            :raw_category, :raw_brand, :raw_type, :raw_description,
                            :raw_attributes_json, :cost_price, :buyer_product_status,
                            :seller_product_status, :stock, :unlimited_stock, :multi,
                            :start_cut_off, :end_cut_off, :availability_state,
                            :availability_reason_codes, :source_row_fingerprint,
                            :source_row_fingerprint_version, :first_seen_run_id,
                            :last_seen_run_id, :first_seen_at, :last_seen_at,
                            :last_known_good_at, :created_at, :updated_at
                        )
                        """,
                        {
                            **common,
                            "provider_product_id": provider_product_id,
                            "first_seen_run_id": apply_run_id,
                            "first_seen_at": now,
                            "created_at": now,
                        },
                    )
                    inserted_count += 1
                else:
                    provider_product_id = str(prior["provider_product_id"])
                    changed = str(prior.get("source_row_fingerprint") or "") != candidate["source_row_fingerprint"]
                    await _execute(
                        connection,
                        """
                        UPDATE provider_products
                        SET provider_sku=:provider_sku, seller_identity=:seller_identity,
                            catalog_product_id=:catalog_product_id, mapping_state=:mapping_state,
                            mapping_provenance=:mapping_provenance,
                            mapping_key_version=:mapping_key_version,
                            mapping_actor=:mapping_actor,
                            mapping_decided_at=:mapping_decided_at,
                            raw_product_name=:raw_product_name, raw_category=:raw_category,
                            raw_brand=:raw_brand, raw_type=:raw_type,
                            raw_description=:raw_description,
                            raw_attributes_json=:raw_attributes_json,
                            cost_price=:cost_price,
                            buyer_product_status=:buyer_product_status,
                            seller_product_status=:seller_product_status, stock=:stock,
                            unlimited_stock=:unlimited_stock, multi=:multi,
                            start_cut_off=:start_cut_off, end_cut_off=:end_cut_off,
                            availability_state=:availability_state,
                            availability_reason_codes=:availability_reason_codes,
                            source_row_fingerprint=:source_row_fingerprint,
                            source_row_fingerprint_version=:source_row_fingerprint_version,
                            last_seen_run_id=:last_seen_run_id, last_seen_at=:last_seen_at,
                            last_known_good_at=:last_known_good_at, archived_at=NULL,
                            updated_at=:updated_at
                        WHERE provider_product_id=:provider_product_id
                        """,
                        {**common, "provider_product_id": provider_product_id},
                    )
                    if changed:
                        updated_count += 1
                    else:
                        unchanged_count += 1
                if review:
                    await _execute(
                        connection,
                        """
                        UPDATE catalog_mapping_reviews
                        SET provider_product_id=:provider_product_id, updated_at=:updated_at
                        WHERE review_id=:review_id
                        """,
                        {
                            "provider_product_id": provider_product_id,
                            "updated_at": now,
                            "review_id": review.get("review_id"),
                        },
                    )

            # Only a complete, valid snapshot may mark a formerly observed
            # candidate stale.  A malformed/duplicate source row makes the
            # snapshot incomplete and therefore cannot degrade last-known-good
            # candidate availability.  Stale is never a delete/archive or an
            # assertion of provider unavailability.
            snapshot_complete = not any(str(item.get("proposed_action") or "") == ACTION_REJECTED for item in items)
            if snapshot_complete:
                for prior in existing:
                    identity = str(prior.get("source_identity_fingerprint") or "")
                    if identity and identity not in observed_identities:
                        await _execute(
                            connection,
                            """
                            UPDATE provider_products
                            SET availability_state='STALE',
                                availability_reason_codes=:reason_codes,
                                updated_at=:updated_at
                            WHERE provider_product_id=:provider_product_id
                            """,
                            {
                                "reason_codes": _json_dump(["missing_from_applied_snapshot"]),
                                "updated_at": now,
                                "provider_product_id": prior.get("provider_product_id"),
                            },
                        )

            valid_count = sum(1 for item in items if _candidate_from_snapshot_item(item))
            await _execute(
                connection,
                """
                UPDATE catalog_sync_runs
                SET status=:status, finished_at=:finished_at, updated_at=:updated_at,
                    received_count=:received_count, valid_count=:valid_count,
                    inserted_count=:inserted_count, updated_count=:updated_count,
                    unchanged_count=:unchanged_count, rejected_count=:rejected_count
                WHERE run_id=:run_id AND status=:applying_status
                """,
                {
                    "status": RUN_STATUS_APPLIED,
                    "finished_at": now,
                    "updated_at": now,
                    "received_count": len(items),
                    "valid_count": valid_count,
                    "inserted_count": inserted_count,
                    "updated_count": updated_count,
                    "unchanged_count": unchanged_count,
                    "rejected_count": rejected_count,
                    "run_id": apply_run_id,
                    "applying_status": RUN_STATUS_APPLYING,
                },
            )
            await _execute(
                connection,
                """
                UPDATE catalog_sync_runs
                SET status=:status, updated_at=:updated_at
                WHERE run_id=:run_id AND status=:preview_status
                """,
                {
                    "status": RUN_STATUS_APPLIED,
                    "updated_at": now,
                    "run_id": preview_run_id,
                    "preview_status": RUN_STATUS_PREVIEW_READY,
                },
            )
            await _execute(
                connection,
                """
                INSERT INTO catalog_provider_sync_state (
                    provider_code, catalog_scope, last_successful_full_snapshot_hash,
                    last_successful_run_id, last_failure_code, last_failure_summary,
                    created_at, updated_at
                ) VALUES (
                    :provider_code, :catalog_scope, :snapshot_hash,
                    :run_id, NULL, NULL, :now, :now
                )
                ON CONFLICT(provider_code, catalog_scope) DO UPDATE SET
                    last_successful_full_snapshot_hash=EXCLUDED.last_successful_full_snapshot_hash,
                    last_successful_run_id=EXCLUDED.last_successful_run_id,
                    last_failure_code=NULL, last_failure_summary=NULL,
                    updated_at=EXCLUDED.updated_at
                """,
                {
                    "provider_code": provider_code,
                    "catalog_scope": catalog_scope,
                    "snapshot_hash": run.get("snapshot_hash"),
                    "run_id": apply_run_id,
                    "now": now,
                },
            )
            applied = await _fetch_one(connection, "SELECT * FROM catalog_sync_runs WHERE run_id=:run_id", {"run_id": apply_run_id})
            if not applied:
                raise CatalogSyncStorageError("Hasil apply katalog tidak dapat dibaca kembali.")
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog apply failed without database detail")
        raise CatalogSyncStorageError("Apply katalog gagal disimpan dengan aman.") from None
    finally:
        await _release_lease(lease)
    return {"success": True, "already_applied": False, "run": _run_payload(applied)}


async def list_catalog_logical_products(*, limit: int = 100) -> dict[str, Any]:
    """Small safe picker for review; it neither publishes nor changes products."""

    safe_limit = min(max(int(limit), 1), 200)
    try:
        async with db_transaction() as connection:
            rows = await _fetch_rows(
                connection,
                """
                SELECT catalog_product_id, sku, name, category, brand,
                       active, store_enabled
                FROM products
                WHERE catalog_product_id IS NOT NULL
                ORDER BY COALESCE(category, ''), name, sku
                LIMIT :limit
                """,
                {"limit": safe_limit},
            )
    except SQLAlchemyError:
        logger.warning("Catalog logical product list failed without database detail")
        raise CatalogSyncStorageError("Daftar logical product belum tersedia.") from None
    return {
        "items": [
            {
                "catalog_product_id": row.get("catalog_product_id"),
                "sku": row.get("sku"),
                "name": row.get("name"),
                "category": row.get("category"),
                "brand": row.get("brand"),
                "active": bool(row.get("active")),
                "store_enabled": bool(row.get("store_enabled")),
            }
            for row in rows
        ],
        "limit": safe_limit,
    }


async def provider_candidates_for_logical_product(catalog_product_id: str) -> dict[str, Any]:
    catalog_product_id = str(catalog_product_id or "").strip()
    if not catalog_product_id:
        raise CatalogSyncValidationError("Catalog product ID wajib diisi.")
    try:
        async with db_transaction() as connection:
            product = await _fetch_one(
                connection,
                """
                SELECT catalog_product_id, sku, name, active, store_enabled
                FROM products WHERE catalog_product_id=:catalog_product_id
                """,
                {"catalog_product_id": catalog_product_id},
            )
            if not product:
                raise CatalogSyncNotFound("Logical product tidak ditemukan.")
            rows = await _fetch_rows(
                connection,
                """
                SELECT provider_product_id, provider_code, catalog_scope, provider_sku,
                       seller_identity, mapping_state, raw_product_name,
                       raw_category, raw_brand, raw_type, cost_price,
                       buyer_product_status, seller_product_status, stock,
                       unlimited_stock, availability_state,
                       availability_reason_codes, first_seen_run_id,
                       last_seen_run_id, last_seen_at, last_known_good_at
                FROM provider_products
                WHERE catalog_product_id=:catalog_product_id
                ORDER BY provider_code, catalog_scope, provider_sku
                """,
                {"catalog_product_id": catalog_product_id},
            )
    except CatalogSyncError:
        raise
    except SQLAlchemyError:
        logger.warning("Catalog candidate list failed without database detail")
        raise CatalogSyncStorageError("Daftar kandidat provider belum tersedia.") from None
    return {
        "logical_product": {
            "catalog_product_id": product.get("catalog_product_id"),
            "sku": product.get("sku"),
            "name": product.get("name"),
            "active": bool(product.get("active")),
            "store_enabled": bool(product.get("store_enabled")),
        },
        "items": [
            {
                "provider_product_id": row.get("provider_product_id"),
                "provider_code": row.get("provider_code"),
                "catalog_scope": row.get("catalog_scope"),
                "provider_sku": row.get("provider_sku"),
                "seller_identity": row.get("seller_identity"),
                "mapping_state": row.get("mapping_state"),
                "product_name": row.get("raw_product_name"),
                "category": row.get("raw_category"),
                "brand": row.get("raw_brand"),
                "type": row.get("raw_type"),
                "cost_price": row.get("cost_price"),
                "buyer_product_status": row.get("buyer_product_status"),
                "seller_product_status": row.get("seller_product_status"),
                "stock": row.get("stock"),
                "unlimited_stock": row.get("unlimited_stock"),
                "availability_state": row.get("availability_state"),
                "availability_reason_codes": _json_load_list(row.get("availability_reason_codes")),
                "first_seen_run_id": row.get("first_seen_run_id"),
                "last_seen_run_id": row.get("last_seen_run_id"),
                "last_seen_at": _display_timestamp(row.get("last_seen_at")),
                "last_known_good_at": _display_timestamp(row.get("last_known_good_at")),
            }
            for row in rows
        ],
    }
