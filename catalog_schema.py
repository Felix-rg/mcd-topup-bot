"""Dedicated additive catalog-schema migration primitives.

This module deliberately has no dependency on the FastAPI application, its
database helpers, or provider services.  The catalog migration is an explicit
owner operation and must never trigger application bootstrap, seed data, or a
provider request as an import side effect.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool


CATALOG_SCHEMA_MIGRATION_ID = "20261004_catalog_schema_v1"
LEGACY_LOGICAL_KEY_VERSION = "legacy-sku-v1"
BACKUP_MANIFEST_VERSION = "lixafa-catalog-backup-v1"
_BOOTSTRAP_SKUS = ("ml_10k", "ff_12k", "pubg_20k")


class CatalogMigrationError(RuntimeError):
    """Base error with a safe machine-readable code for the CLI."""

    code = "catalog_migration_error"


class DatabaseTargetError(CatalogMigrationError):
    code = "database_target_invalid"


class PreflightError(CatalogMigrationError):
    code = "migration_preflight_failed"


class BackupRequiredError(CatalogMigrationError):
    code = "backup_required"


class BackupVerificationError(CatalogMigrationError):
    code = "backup_verification_failed"


class MigrationLockError(CatalogMigrationError):
    code = "migration_lock_unavailable"


class MigrationChecksumMismatch(CatalogMigrationError):
    code = "migration_checksum_mismatch"


class MigrationVerificationError(CatalogMigrationError):
    code = "migration_verification_failed"


@dataclass(frozen=True)
class DatabaseTarget:
    """A parsed target whose public summary never includes credentials."""

    database_url: str
    dialect: str
    fingerprint: str
    summary: Dict[str, str]
    sqlite_path: Optional[Path] = None


PRODUCT_CATALOG_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("catalog_product_id", "TEXT"),
    ("logical_key", "TEXT"),
    ("logical_key_version", "TEXT"),
    ("denomination_text", "TEXT"),
    ("denomination_value", "NUMERIC"),
    ("denomination_unit", "TEXT"),
    ("product_semantics", "TEXT"),
    # Nullable on purpose: the explicit migration backfill bridges it to
    # active without changing legacy application behavior.
    ("store_enabled", "INTEGER"),
)


CATALOG_TABLE_DDL: Tuple[Tuple[str, str], ...] = (
    (
        "catalog_sync_runs",
        """
        CREATE TABLE IF NOT EXISTS catalog_sync_runs (
            run_id TEXT PRIMARY KEY NOT NULL,
            provider_code TEXT NOT NULL,
            catalog_scope TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            source_preview_run_id TEXT,
            actor TEXT,
            source TEXT,
            correlation_id TEXT,
            snapshot_hash TEXT,
            snapshot_hash_version TEXT,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            received_count INTEGER NOT NULL DEFAULT 0,
            valid_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unchanged_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            sanitized_count INTEGER NOT NULL DEFAULT 0,
            ambiguous_count INTEGER NOT NULL DEFAULT 0,
            review_required_count INTEGER NOT NULL DEFAULT 0,
            sanitized_error_code TEXT,
            sanitized_error_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_preview_run_id)
                REFERENCES catalog_sync_runs(run_id) ON DELETE RESTRICT
        )
        """,
    ),
    (
        "provider_products",
        """
        CREATE TABLE IF NOT EXISTS provider_products (
            provider_product_id TEXT PRIMARY KEY NOT NULL,
            provider_code TEXT NOT NULL,
            catalog_scope TEXT NOT NULL,
            provider_sku TEXT NOT NULL,
            seller_identity TEXT,
            source_identity_fingerprint TEXT NOT NULL,
            source_identity_fingerprint_version TEXT NOT NULL,
            catalog_product_id TEXT,
            mapping_state TEXT NOT NULL DEFAULT 'UNMAPPED',
            mapping_provenance TEXT,
            mapping_key_version TEXT,
            mapping_actor TEXT,
            mapping_decided_at TIMESTAMP,
            mapping_note TEXT,
            raw_product_name TEXT,
            raw_category TEXT,
            raw_brand TEXT,
            raw_type TEXT,
            raw_description TEXT,
            raw_attributes_json TEXT,
            cost_price NUMERIC,
            admin_fee NUMERIC,
            commission NUMERIC,
            buyer_product_status INTEGER,
            seller_product_status INTEGER,
            stock INTEGER,
            unlimited_stock INTEGER,
            multi INTEGER,
            start_cut_off TEXT,
            end_cut_off TEXT,
            start_cut_off_minutes INTEGER,
            end_cut_off_minutes INTEGER,
            cut_off_parser_version TEXT,
            cut_off_timezone TEXT,
            availability_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            availability_reason_codes TEXT,
            source_row_fingerprint TEXT,
            source_row_fingerprint_version TEXT,
            first_seen_run_id TEXT,
            last_seen_run_id TEXT,
            first_seen_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            last_known_good_at TIMESTAMP,
            archived_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
    (
        "catalog_sync_run_items",
        """
        CREATE TABLE IF NOT EXISTS catalog_sync_run_items (
            run_item_id TEXT PRIMARY KEY NOT NULL,
            run_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            safe_raw_attributes_json TEXT,
            normalized_attributes_json TEXT,
            identity_fingerprint TEXT,
            identity_fingerprint_version TEXT,
            source_row_fingerprint TEXT,
            source_row_fingerprint_version TEXT,
            proposed_action TEXT,
            diff_json TEXT,
            reason_codes TEXT,
            provider_product_id TEXT,
            catalog_product_id TEXT,
            requires_review INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, sequence_no),
            FOREIGN KEY (run_id)
                REFERENCES catalog_sync_runs(run_id) ON DELETE RESTRICT
        )
        """,
    ),
    (
        "catalog_mapping_reviews",
        """
        CREATE TABLE IF NOT EXISTS catalog_mapping_reviews (
            review_id TEXT PRIMARY KEY NOT NULL,
            run_item_id TEXT,
            provider_product_id TEXT,
            suggested_catalog_product_id TEXT,
            selected_catalog_product_id TEXT,
            proposed_catalog_product_ids_json TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            evidence_json TEXT,
            reason_codes TEXT,
            decision_note TEXT,
            created_by TEXT,
            reviewed_by TEXT,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_item_id)
                REFERENCES catalog_sync_run_items(run_item_id) ON DELETE RESTRICT,
            FOREIGN KEY (provider_product_id)
                REFERENCES provider_products(provider_product_id) ON DELETE RESTRICT
        )
        """,
    ),
    (
        "catalog_provider_sync_state",
        """
        CREATE TABLE IF NOT EXISTS catalog_provider_sync_state (
            provider_code TEXT NOT NULL,
            catalog_scope TEXT NOT NULL,
            last_request_at TIMESTAMP,
            next_full_fetch_not_before TIMESTAMP,
            last_successful_full_snapshot_hash TEXT,
            last_successful_run_id TEXT,
            last_failure_code TEXT,
            last_failure_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider_code, catalog_scope)
        )
        """,
    ),
    (
        "catalog_sync_locks",
        """
        CREATE TABLE IF NOT EXISTS catalog_sync_locks (
            provider_code TEXT NOT NULL,
            catalog_scope TEXT NOT NULL,
            owner_token TEXT,
            run_id TEXT,
            fencing_generation INTEGER NOT NULL DEFAULT 0,
            acquired_at TIMESTAMP,
            expires_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (provider_code, catalog_scope)
        )
        """,
    ),
)


CATALOG_TABLE_COLUMNS: Mapping[str, Tuple[str, ...]] = {
    "catalog_sync_runs": (
        "run_id",
        "provider_code",
        "catalog_scope",
        "operation",
        "status",
        "source_preview_run_id",
        "actor",
        "source",
        "correlation_id",
        "snapshot_hash",
        "snapshot_hash_version",
        "started_at",
        "finished_at",
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
        "created_at",
        "updated_at",
    ),
    "provider_products": (
        "provider_product_id",
        "provider_code",
        "catalog_scope",
        "provider_sku",
        "seller_identity",
        "source_identity_fingerprint",
        "source_identity_fingerprint_version",
        "catalog_product_id",
        "mapping_state",
        "mapping_provenance",
        "mapping_key_version",
        "mapping_actor",
        "mapping_decided_at",
        "mapping_note",
        "raw_product_name",
        "raw_category",
        "raw_brand",
        "raw_type",
        "raw_description",
        "raw_attributes_json",
        "cost_price",
        "admin_fee",
        "commission",
        "buyer_product_status",
        "seller_product_status",
        "stock",
        "unlimited_stock",
        "multi",
        "start_cut_off",
        "end_cut_off",
        "start_cut_off_minutes",
        "end_cut_off_minutes",
        "cut_off_parser_version",
        "cut_off_timezone",
        "availability_state",
        "availability_reason_codes",
        "source_row_fingerprint",
        "source_row_fingerprint_version",
        "first_seen_run_id",
        "last_seen_run_id",
        "first_seen_at",
        "last_seen_at",
        "last_known_good_at",
        "archived_at",
        "created_at",
        "updated_at",
    ),
    "catalog_sync_run_items": (
        "run_item_id",
        "run_id",
        "sequence_no",
        "safe_raw_attributes_json",
        "normalized_attributes_json",
        "identity_fingerprint",
        "identity_fingerprint_version",
        "source_row_fingerprint",
        "source_row_fingerprint_version",
        "proposed_action",
        "diff_json",
        "reason_codes",
        "provider_product_id",
        "catalog_product_id",
        "requires_review",
        "created_at",
    ),
    "catalog_mapping_reviews": (
        "review_id",
        "run_item_id",
        "provider_product_id",
        "suggested_catalog_product_id",
        "selected_catalog_product_id",
        "proposed_catalog_product_ids_json",
        "status",
        "evidence_json",
        "reason_codes",
        "decision_note",
        "created_by",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    ),
    "catalog_provider_sync_state": (
        "provider_code",
        "catalog_scope",
        "last_request_at",
        "next_full_fetch_not_before",
        "last_successful_full_snapshot_hash",
        "last_successful_run_id",
        "last_failure_code",
        "last_failure_summary",
        "created_at",
        "updated_at",
    ),
    "catalog_sync_locks": (
        "provider_code",
        "catalog_scope",
        "owner_token",
        "run_id",
        "fencing_generation",
        "acquired_at",
        "expires_at",
        "updated_at",
    ),
}


INDEX_STATEMENTS: Tuple[Tuple[str, str], ...] = (
    (
        "uq_products_catalog_product_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_products_catalog_product_id ON products (catalog_product_id)",
    ),
    (
        "idx_provider_products_scope_sku",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_scope_sku "
        "ON provider_products (provider_code, catalog_scope, provider_sku)",
    ),
    (
        "idx_provider_products_seller",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_seller "
        "ON provider_products (provider_code, catalog_scope, seller_identity)",
    ),
    (
        "idx_provider_products_source_identity",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_source_identity "
        "ON provider_products (provider_code, catalog_scope, source_identity_fingerprint)",
    ),
    (
        "idx_provider_products_catalog_product",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_catalog_product "
        "ON provider_products (catalog_product_id)",
    ),
    (
        "idx_provider_products_mapping_availability",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_mapping_availability "
        "ON provider_products (mapping_state, availability_state)",
    ),
    (
        "idx_provider_products_last_seen_run",
        "CREATE INDEX IF NOT EXISTS idx_provider_products_last_seen_run "
        "ON provider_products (last_seen_run_id)",
    ),
    (
        "idx_catalog_sync_runs_scope_started",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_runs_scope_started "
        "ON catalog_sync_runs (provider_code, catalog_scope, started_at)",
    ),
    (
        "idx_catalog_sync_runs_status_started",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_runs_status_started "
        "ON catalog_sync_runs (status, started_at)",
    ),
    (
        "idx_catalog_sync_runs_source_preview",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_runs_source_preview "
        "ON catalog_sync_runs (source_preview_run_id)",
    ),
    (
        "idx_catalog_sync_run_items_run_action",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_run_items_run_action "
        "ON catalog_sync_run_items (run_id, proposed_action)",
    ),
    (
        "idx_catalog_sync_run_items_candidate",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_run_items_candidate "
        "ON catalog_sync_run_items (provider_product_id)",
    ),
    (
        "idx_catalog_sync_run_items_catalog_product",
        "CREATE INDEX IF NOT EXISTS idx_catalog_sync_run_items_catalog_product "
        "ON catalog_sync_run_items (catalog_product_id)",
    ),
    (
        "idx_catalog_mapping_reviews_status_created",
        "CREATE INDEX IF NOT EXISTS idx_catalog_mapping_reviews_status_created "
        "ON catalog_mapping_reviews (status, created_at)",
    ),
    (
        "idx_catalog_mapping_reviews_run_item",
        "CREATE INDEX IF NOT EXISTS idx_catalog_mapping_reviews_run_item "
        "ON catalog_mapping_reviews (run_item_id)",
    ),
    (
        "idx_catalog_mapping_reviews_candidate",
        "CREATE INDEX IF NOT EXISTS idx_catalog_mapping_reviews_candidate "
        "ON catalog_mapping_reviews (provider_product_id)",
    ),
)


# The runner validates pre-existing/resumable catalog objects before issuing a
# single DDL statement.  These contracts are deliberately explicit rather
# than relying on CREATE ... IF NOT EXISTS, which would otherwise accept a
# same-named object with an incompatible shape.
PRODUCT_CATALOG_COLUMN_SPECS: Mapping[str, Mapping[str, Any]] = {
    column_name: {"type": column_type, "nullable": True, "default": None}
    for column_name, column_type in PRODUCT_CATALOG_COLUMNS
}

_DDL_COLUMN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<column_type>TEXT|NUMERIC|INTEGER|TIMESTAMP)\b"
    r"(?P<definition>[^,\n]*)",
    re.IGNORECASE | re.MULTILINE,
)
_DDL_DEFAULT_RE = re.compile(
    r"\bDEFAULT\s+(?P<value>'(?:''|[^'])*'|[^\s,]+)", re.IGNORECASE
)


def _catalog_table_column_specs(statement: str) -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    for match in _DDL_COLUMN_RE.finditer(statement):
        definition = str(match.group("definition") or "")
        default_match = _DDL_DEFAULT_RE.search(definition)
        specs[str(match.group("name"))] = {
            "type": str(match.group("column_type")).upper(),
            "nullable": "NOT NULL" not in definition.upper()
            and "PRIMARY KEY" not in definition.upper(),
            "default": default_match.group("value") if default_match else None,
        }
    return specs


CATALOG_TABLE_COLUMN_SPECS: Mapping[str, Mapping[str, Mapping[str, Any]]] = {
    table_name: _catalog_table_column_specs(statement)
    for table_name, statement in CATALOG_TABLE_DDL
}
for _contract_table_name, _contract_columns in CATALOG_TABLE_COLUMNS.items():
    if set(CATALOG_TABLE_COLUMN_SPECS[_contract_table_name]) != set(_contract_columns):
        raise RuntimeError(f"Catalog DDL contract is incomplete for {_contract_table_name}")


CATALOG_TABLE_PRIMARY_KEYS: Mapping[str, Tuple[str, ...]] = {
    "catalog_sync_runs": ("run_id",),
    "provider_products": ("provider_product_id",),
    "catalog_sync_run_items": ("run_item_id",),
    "catalog_mapping_reviews": ("review_id",),
    "catalog_provider_sync_state": ("provider_code", "catalog_scope"),
    "catalog_sync_locks": ("provider_code", "catalog_scope"),
}
CATALOG_TABLE_UNIQUE_SETS: Mapping[str, Tuple[Tuple[str, ...], ...]] = {
    "catalog_sync_runs": (),
    "provider_products": (),
    "catalog_sync_run_items": (("run_id", "sequence_no"),),
    "catalog_mapping_reviews": (),
    "catalog_provider_sync_state": (),
    "catalog_sync_locks": (),
}
CATALOG_TABLE_FOREIGN_KEYS: Mapping[str, Tuple[Tuple[str, str, str], ...]] = {
    "catalog_sync_runs": (("source_preview_run_id", "catalog_sync_runs", "run_id"),),
    "provider_products": (),
    "catalog_sync_run_items": (("run_id", "catalog_sync_runs", "run_id"),),
    "catalog_mapping_reviews": (
        ("run_item_id", "catalog_sync_run_items", "run_item_id"),
        ("provider_product_id", "provider_products", "provider_product_id"),
    ),
    "catalog_provider_sync_state": (),
    "catalog_sync_locks": (),
}
INDEX_SPECS: Mapping[str, Tuple[bool, str, Tuple[str, ...]]] = {
    "uq_products_catalog_product_id": (True, "products", ("catalog_product_id",)),
    "idx_provider_products_scope_sku": (
        False,
        "provider_products",
        ("provider_code", "catalog_scope", "provider_sku"),
    ),
    "idx_provider_products_seller": (
        False,
        "provider_products",
        ("provider_code", "catalog_scope", "seller_identity"),
    ),
    "idx_provider_products_source_identity": (
        False,
        "provider_products",
        ("provider_code", "catalog_scope", "source_identity_fingerprint"),
    ),
    "idx_provider_products_catalog_product": (
        False,
        "provider_products",
        ("catalog_product_id",),
    ),
    "idx_provider_products_mapping_availability": (
        False,
        "provider_products",
        ("mapping_state", "availability_state"),
    ),
    "idx_provider_products_last_seen_run": (False, "provider_products", ("last_seen_run_id",)),
    "idx_catalog_sync_runs_scope_started": (
        False,
        "catalog_sync_runs",
        ("provider_code", "catalog_scope", "started_at"),
    ),
    "idx_catalog_sync_runs_status_started": (
        False,
        "catalog_sync_runs",
        ("status", "started_at"),
    ),
    "idx_catalog_sync_runs_source_preview": (
        False,
        "catalog_sync_runs",
        ("source_preview_run_id",),
    ),
    "idx_catalog_sync_run_items_run_action": (
        False,
        "catalog_sync_run_items",
        ("run_id", "proposed_action"),
    ),
    "idx_catalog_sync_run_items_candidate": (
        False,
        "catalog_sync_run_items",
        ("provider_product_id",),
    ),
    "idx_catalog_sync_run_items_catalog_product": (
        False,
        "catalog_sync_run_items",
        ("catalog_product_id",),
    ),
    "idx_catalog_mapping_reviews_status_created": (
        False,
        "catalog_mapping_reviews",
        ("status", "created_at"),
    ),
    "idx_catalog_mapping_reviews_run_item": (
        False,
        "catalog_mapping_reviews",
        ("run_item_id",),
    ),
    "idx_catalog_mapping_reviews_candidate": (
        False,
        "catalog_mapping_reviews",
        ("provider_product_id",),
    ),
}
RUN_ITEM_IMMUTABILITY_VERSION = "catalog-run-item-immutable-v1"
_SQLITE_RUN_ITEM_IMMUTABILITY_TRIGGERS: Mapping[str, str] = {
    "catalog_sync_run_items_no_update": """
        CREATE TRIGGER IF NOT EXISTS catalog_sync_run_items_no_update
        BEFORE UPDATE ON catalog_sync_run_items
        BEGIN
            SELECT RAISE(ABORT, 'catalog_sync_run_items are immutable');
        END
    """,
    "catalog_sync_run_items_no_delete": """
        CREATE TRIGGER IF NOT EXISTS catalog_sync_run_items_no_delete
        BEFORE DELETE ON catalog_sync_run_items
        BEGIN
            SELECT RAISE(ABORT, 'catalog_sync_run_items are immutable');
        END
    """,
}
_POSTGRES_RUN_ITEM_IMMUTABILITY_FUNCTION = "catalog_sync_run_items_immutable_v1"
_POSTGRES_RUN_ITEM_IMMUTABILITY_TRIGGER = "catalog_sync_run_items_immutable_v1"


_MIGRATION_MANIFEST = {
    "migration_id": CATALOG_SCHEMA_MIGRATION_ID,
    "legacy_logical_key_version": LEGACY_LOGICAL_KEY_VERSION,
    "product_columns": PRODUCT_CATALOG_COLUMNS,
    "catalog_tables": CATALOG_TABLE_DDL,
    "indexes": INDEX_STATEMENTS,
    "run_item_immutability": RUN_ITEM_IMMUTABILITY_VERSION,
    "backfill": {
        "catalog_product_id": "generated_uuid_once_when_null",
        "logical_key": "legacy-sku:<sku.casefold()>",
        "store_enabled": "active_when_null",
        "provider_products": "never_backfilled_from_legacy",
    },
}
CATALOG_SCHEMA_MIGRATION_CHECKSUM = hashlib.sha256(
    json.dumps(_MIGRATION_MANIFEST, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
_MIGRATION_ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(CATALOG_SCHEMA_MIGRATION_ID.encode("utf-8")).digest()[:8],
    byteorder="big",
    signed=True,
)

_BASE_REQUIRED_COLUMNS: Mapping[str, Tuple[str, ...]] = {
    "products": ("sku", "name", "price", "cost_price", "active"),
    "topup": ("id", "nominal", "price", "product_cost"),
    "promos": ("id", "code", "target_scope", "target_value"),
}
_CATALOG_TABLE_NAMES = tuple(name for name, _statement in CATALOG_TABLE_DDL)
_EXPECTED_INDEX_NAMES = tuple(name for name, _statement in INDEX_STATEMENTS)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_bytes(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _fingerprint_rows(rows: Iterable[Sequence[Any]]) -> str:
    normalized = [tuple(_canonical_value(value) for value in row) for row in rows]
    normalized.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _mask_host(host: Optional[str]) -> str:
    cleaned = str(host or "").strip()
    if not cleaned:
        return "local"
    if len(cleaned) <= 2:
        return "***"
    return f"{cleaned[:2]}***"


def database_target(database_url: str) -> DatabaseTarget:
    """Validate an explicit supported URL and expose only a safe identity."""

    raw_url = str(database_url or "").strip()
    if not raw_url:
        raise DatabaseTargetError("Database target must be supplied explicitly")
    try:
        parsed = make_url(raw_url)
    except Exception as exc:
        raise DatabaseTargetError("Database target URL is invalid") from exc

    driver = str(parsed.drivername or "").lower()
    if driver == "sqlite+aiosqlite":
        database_name = str(parsed.database or "").strip()
        if not database_name or database_name == ":memory:":
            raise DatabaseTargetError("Catalog migration requires a persistent SQLite database file")
        sqlite_path = Path(database_name).expanduser().resolve()
        # Connecting to an absent SQLite path silently creates a new database.
        # A migration preflight must be read-only and fail closed instead of
        # manufacturing an empty target when an owner mistypes a path.
        if not sqlite_path.exists() or not sqlite_path.is_file():
            raise DatabaseTargetError("SQLite target database file does not exist")
        identity = f"sqlite|{sqlite_path.as_posix()}"
        return DatabaseTarget(
            database_url=raw_url,
            dialect="sqlite",
            fingerprint=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            summary={"dialect": "sqlite", "host": "local", "database": sqlite_path.name},
            sqlite_path=sqlite_path,
        )

    if driver == "postgresql+asyncpg":
        database_name = str(parsed.database or "").strip()
        host = str(parsed.host or "").strip()
        if not database_name or not host:
            raise DatabaseTargetError("PostgreSQL target must include an explicit host and database")
        port = int(parsed.port or 5432)
        identity = f"postgresql|{host.casefold()}|{port}|{database_name.casefold()}"
        return DatabaseTarget(
            database_url=raw_url,
            dialect="postgresql",
            fingerprint=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            summary={
                "dialect": "postgresql",
                "host": _mask_host(host),
                "database": database_name,
            },
        )

    raise DatabaseTargetError("Only sqlite+aiosqlite and postgresql+asyncpg targets are supported")


def create_migration_engine(
    target: DatabaseTarget,
    *,
    postgres_read_only: bool = False,
) -> AsyncEngine:
    """Create a direct engine; no application bootstrap helpers are involved.

    The opt-in PostgreSQL mode is reserved for an inspection whose entire
    connection must fail closed against writes. Its connection default protects
    even driver/dialect initialization, while the caller still opens an
    explicit BEGIN READ ONLY transaction around the catalog inspection.
    """

    if target.dialect == "sqlite":
        if postgres_read_only:
            raise DatabaseTargetError("PostgreSQL read-only mode requires a PostgreSQL target")
        return create_async_engine(
            target.database_url,
            echo=False,
            poolclass=NullPool,
            connect_args={"timeout": 0},
        )
    if postgres_read_only:
        return create_async_engine(
            target.database_url,
            echo=False,
            # Do not issue a pre-ping before the explicit read-only transaction.
            pool_pre_ping=False,
            poolclass=NullPool,
            # AUTOCOMMIT permits the literal BEGIN READ ONLY below to start the
            # server-side transaction rather than SQLAlchemy first emitting a
            # default read/write BEGIN.
            isolation_level="AUTOCOMMIT",
            # asyncpg sends these as startup parameters. Every transaction on
            # this one-off NullPool connection defaults to read-only, including
            # any dialect initialization before the explicit transaction.
            connect_args={"server_settings": {"default_transaction_read_only": "on"}},
        )
    return create_async_engine(target.database_url, echo=False, pool_pre_ping=True, poolclass=NullPool)


async def _resolved_target_summary(
    conn: AsyncConnection,
    target: DatabaseTarget,
) -> Dict[str, str]:
    """Bind PostgreSQL confirmation to its actual database and search-path schema."""

    if target.dialect == "sqlite":
        return {"fingerprint": target.fingerprint, **target.summary}

    result = await conn.execute(text("SELECT current_database(), current_schema()"))
    row = result.one()
    actual_database = str(row[0] or "").strip()
    current_schema = str(row[1] or "").strip()
    if not actual_database or not current_schema:
        raise DatabaseTargetError("PostgreSQL target did not expose a database and schema identity")
    if actual_database.casefold() != str(target.summary["database"]).casefold():
        raise DatabaseTargetError("PostgreSQL connection database does not match the explicit target")
    identity = f"{target.fingerprint}|database:{actual_database.casefold()}|schema:{current_schema.casefold()}"
    return {
        "fingerprint": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        **target.summary,
        "schema": current_schema,
    }


async def _table_names(conn: AsyncConnection, target: DatabaseTarget) -> set[str]:
    if target.dialect == "sqlite":
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    else:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")
        )
    return {str(row[0]) for row in result.fetchall()}


def _quote_sqlite_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise DatabaseTargetError("Catalog schema contains an unsafe identifier")
    return f'"{identifier}"'


async def _table_column_details(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Return portable column facts needed for fail-closed shape validation."""

    if target.dialect == "sqlite":
        identifier = _quote_sqlite_identifier(table_name)
        result = await conn.execute(text(f"PRAGMA table_info({identifier})"))
        return {
            str(row[1]): {
                "type": str(row[2] or "").upper(),
                "nullable": not bool(row[3]),
                "default": row[4],
                "primary_key_position": int(row[5] or 0),
            }
            for row in result.fetchall()
        }

    result = await conn.execute(
        text(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:table_name
            """
        ),
        {"table_name": table_name},
    )
    return {
        str(row[0]): {
            "type": str(row[1] or "").upper(),
            "nullable": str(row[2] or "").upper() == "YES",
            "default": row[3],
            "primary_key_position": 0,
        }
        for row in result.fetchall()
    }


async def _table_columns(conn: AsyncConnection, target: DatabaseTarget, table_name: str) -> Dict[str, str]:
    details = await _table_column_details(conn, target, table_name)
    return {column_name: str(detail["type"]) for column_name, detail in details.items()}


async def _index_names(conn: AsyncConnection, target: DatabaseTarget) -> set[str]:
    if target.dialect == "sqlite":
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))
    else:
        result = await conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"))
    return {str(row[0]) for row in result.fetchall() if row[0]}


async def _primary_key_columns(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_name: str,
) -> Tuple[str, ...]:
    if target.dialect == "sqlite":
        details = await _table_column_details(conn, target, table_name)
        primary_key = sorted(
            (
                (int(detail["primary_key_position"]), column_name)
                for column_name, detail in details.items()
                if int(detail["primary_key_position"] or 0) > 0
            ),
            key=lambda value: value[0],
        )
        return tuple(column_name for _position, column_name in primary_key)

    result = await conn.execute(
        text(
            """
            SELECT key_column_usage.column_name
            FROM information_schema.table_constraints
            JOIN information_schema.key_column_usage
              ON table_constraints.constraint_catalog = key_column_usage.constraint_catalog
             AND table_constraints.constraint_schema = key_column_usage.constraint_schema
             AND table_constraints.constraint_name = key_column_usage.constraint_name
            WHERE table_constraints.table_schema=current_schema()
              AND table_constraints.table_name=:table_name
              AND table_constraints.constraint_type='PRIMARY KEY'
            ORDER BY key_column_usage.ordinal_position
            """
        ),
        {"table_name": table_name},
    )
    return tuple(str(row[0]) for row in result.fetchall())


async def _unique_index_column_sets(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_name: str,
) -> set[Tuple[str, ...]]:
    if target.dialect == "sqlite":
        identifier = _quote_sqlite_identifier(table_name)
        result = await conn.execute(text(f"PRAGMA index_list({identifier})"))
        unique_sets: set[Tuple[str, ...]] = set()
        for row in result.fetchall():
            if not bool(row[2]):
                continue
            index_identifier = _quote_sqlite_identifier(str(row[1]))
            columns = await conn.execute(text(f"PRAGMA index_info({index_identifier})"))
            ordered = sorted(columns.fetchall(), key=lambda index_row: int(index_row[0]))
            unique_sets.add(tuple(str(index_row[2]) for index_row in ordered))
        return unique_sets

    result = await conn.execute(
        text(
            """
            SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
            FROM pg_index AS index_definition
            JOIN pg_class AS table_class
              ON table_class.oid = index_definition.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_class.relnamespace
            JOIN pg_class AS index_class
              ON index_class.oid = index_definition.indexrelid
            LEFT JOIN LATERAL unnest(index_definition.indkey::smallint[])
              WITH ORDINALITY AS key_column(attnum, ordinality) ON TRUE
            LEFT JOIN pg_attribute AS attribute
              ON attribute.attrelid = table_class.oid
             AND attribute.attnum = key_column.attnum
            WHERE namespace.nspname=current_schema()
              AND table_class.relname=:table_name
              AND index_definition.indisunique
            GROUP BY index_class.relname, index_definition.indexrelid
            """
        ),
        {"table_name": table_name},
    )
    return {
        tuple(str(column_name) for column_name in (row[0] or []) if column_name is not None)
        for row in result.fetchall()
    }


async def _named_index_shape(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_name: str,
    index_name: str,
) -> Optional[Dict[str, Any]]:
    if target.dialect == "sqlite":
        table_identifier = _quote_sqlite_identifier(table_name)
        result = await conn.execute(text(f"PRAGMA index_list({table_identifier})"))
        matching_rows = [row for row in result.fetchall() if str(row[1]) == index_name]
        if not matching_rows:
            return None
        row = matching_rows[0]
        index_identifier = _quote_sqlite_identifier(index_name)
        columns = await conn.execute(text(f"PRAGMA index_info({index_identifier})"))
        ordered = sorted(columns.fetchall(), key=lambda index_row: int(index_row[0]))
        return {
            "unique": bool(row[2]),
            "columns": tuple(str(index_row[2]) for index_row in ordered),
        }

    result = await conn.execute(
        text(
            """
            SELECT index_definition.indisunique,
                   array_agg(attribute.attname ORDER BY key_column.ordinality)
            FROM pg_index AS index_definition
            JOIN pg_class AS table_class
              ON table_class.oid = index_definition.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_class.relnamespace
            JOIN pg_class AS index_class
              ON index_class.oid = index_definition.indexrelid
            LEFT JOIN LATERAL unnest(index_definition.indkey::smallint[])
              WITH ORDINALITY AS key_column(attnum, ordinality) ON TRUE
            LEFT JOIN pg_attribute AS attribute
              ON attribute.attrelid = table_class.oid
             AND attribute.attnum = key_column.attnum
            WHERE namespace.nspname=current_schema()
              AND table_class.relname=:table_name
              AND index_class.relname=:index_name
            GROUP BY index_definition.indisunique, index_definition.indexrelid
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    row = result.first()
    if row is None:
        return None
    return {
        "unique": bool(row[0]),
        "columns": tuple(str(column_name) for column_name in (row[1] or []) if column_name is not None),
    }


async def _foreign_key_shapes(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_name: str,
) -> set[Tuple[str, str, str, str]]:
    if target.dialect == "sqlite":
        identifier = _quote_sqlite_identifier(table_name)
        result = await conn.execute(text(f"PRAGMA foreign_key_list({identifier})"))
        return {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in result.fetchall()
        }

    result = await conn.execute(
        text(
            """
            SELECT key_column_usage.column_name,
                   referenced_column_usage.table_name,
                   referenced_column_usage.column_name,
                   referential_constraints.delete_rule
            FROM information_schema.table_constraints
            JOIN information_schema.key_column_usage
              ON table_constraints.constraint_catalog = key_column_usage.constraint_catalog
             AND table_constraints.constraint_schema = key_column_usage.constraint_schema
             AND table_constraints.constraint_name = key_column_usage.constraint_name
            JOIN information_schema.referential_constraints
              ON table_constraints.constraint_catalog = referential_constraints.constraint_catalog
             AND table_constraints.constraint_schema = referential_constraints.constraint_schema
             AND table_constraints.constraint_name = referential_constraints.constraint_name
            JOIN information_schema.constraint_column_usage AS referenced_column_usage
              ON referential_constraints.unique_constraint_catalog = referenced_column_usage.constraint_catalog
             AND referential_constraints.unique_constraint_schema = referenced_column_usage.constraint_schema
             AND referential_constraints.unique_constraint_name = referenced_column_usage.constraint_name
            WHERE table_constraints.table_schema=current_schema()
              AND table_constraints.table_name=:table_name
              AND table_constraints.constraint_type='FOREIGN KEY'
            """
        ),
        {"table_name": table_name},
    )
    return {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]).upper())
        for row in result.fetchall()
    }


async def _run_item_immutability_state(
    conn: AsyncConnection,
    target: DatabaseTarget,
) -> Dict[str, List[str]]:
    """Inspect the append-only guard without changing the schema."""

    if target.dialect == "sqlite":
        result = await conn.execute(
            text(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type='trigger' AND tbl_name='catalog_sync_run_items'
                """
            )
        )
        definitions = {str(row[0]): str(row[1] or "").upper() for row in result.fetchall()}
        missing: List[str] = []
        incompatible: List[str] = []
        expected_events = {
            "catalog_sync_run_items_no_update": "BEFORE UPDATE",
            "catalog_sync_run_items_no_delete": "BEFORE DELETE",
        }
        for trigger_name, event_fragment in expected_events.items():
            definition = definitions.get(trigger_name)
            if definition is None:
                missing.append(trigger_name)
            elif event_fragment not in definition or "RAISE(ABORT" not in definition:
                incompatible.append(trigger_name)
        return {"missing": missing, "incompatible": incompatible}

    result = await conn.execute(
        text(
            """
            SELECT trigger_definition.tgname,
                   pg_get_triggerdef(trigger_definition.oid)
            FROM pg_trigger AS trigger_definition
            JOIN pg_class AS table_class
              ON table_class.oid = trigger_definition.tgrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = table_class.relnamespace
            WHERE namespace.nspname=current_schema()
              AND table_class.relname='catalog_sync_run_items'
              AND NOT trigger_definition.tgisinternal
              AND trigger_definition.tgname=:trigger_name
            """
        ),
        {"trigger_name": _POSTGRES_RUN_ITEM_IMMUTABILITY_TRIGGER},
    )
    row = result.first()
    if row is None:
        return {"missing": [_POSTGRES_RUN_ITEM_IMMUTABILITY_TRIGGER], "incompatible": []}
    definition = str(row[1] or "").upper()
    if (
        "BEFORE" not in definition
        or "UPDATE" not in definition
        or "DELETE" not in definition
        or _POSTGRES_RUN_ITEM_IMMUTABILITY_FUNCTION.upper() not in definition
    ):
        return {"missing": [], "incompatible": [_POSTGRES_RUN_ITEM_IMMUTABILITY_TRIGGER]}
    return {"missing": [], "incompatible": []}


def _type_matches(expected_type: str, actual_type: Any) -> bool:
    expected = re.sub(r"\s+", " ", str(expected_type or "").strip().upper())
    actual = re.sub(r"\s+", " ", str(actual_type or "").strip().upper())
    if expected == "TIMESTAMP":
        return actual in {"TIMESTAMP", "TIMESTAMP WITHOUT TIME ZONE"}
    return actual == expected


def _canonical_default(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"::[A-Za-z0-9_\s\[\]]+$", "", normalized).strip()
    if not normalized or normalized.upper() == "NULL":
        return None
    if len(normalized) >= 2 and normalized.startswith("'") and normalized.endswith("'"):
        literal_value = normalized[1:-1].replace("''", "'")
        return "literal:" + literal_value.casefold()
    return normalized.casefold()


def _column_contract_errors(
    table_name: str,
    details: Mapping[str, Mapping[str, Any]],
    specs: Mapping[str, Mapping[str, Any]],
    columns_to_check: Iterable[str],
) -> List[str]:
    errors: List[str] = []
    for column_name in columns_to_check:
        detail = details.get(column_name)
        spec = specs[column_name]
        if detail is None:
            errors.append(f"catalog_column_missing:{table_name}.{column_name}")
            continue
        if not _type_matches(str(spec["type"]), detail.get("type")):
            errors.append(f"catalog_column_type_incompatible:{table_name}.{column_name}")
        if bool(detail.get("nullable")) != bool(spec["nullable"]):
            errors.append(f"catalog_column_nullability_incompatible:{table_name}.{column_name}")
        if _canonical_default(detail.get("default")) != _canonical_default(spec["default"]):
            errors.append(f"catalog_column_default_incompatible:{table_name}.{column_name}")
    return errors


async def _marker_info(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_names: set[str],
) -> Dict[str, Any]:
    if "schema_migrations" not in table_names:
        return {"state": "absent", "table_present": False}

    details = await _table_column_details(conn, target, "schema_migrations")
    columns = {column_name: str(detail["type"]) for column_name, detail in details.items()}
    required = {"migration_id", "checksum", "applied_at"}
    missing = sorted(required - set(columns))
    if missing:
        return {"state": "incompatible", "table_present": True, "missing_columns": missing}

    marker_types = {"migration_id": "TEXT", "checksum": "TEXT", "applied_at": "TIMESTAMP"}
    incompatible_columns = sorted(
        column_name
        for column_name, expected_type in marker_types.items()
        if not _type_matches(expected_type, details[column_name].get("type"))
    )
    primary_key = await _primary_key_columns(conn, target, "schema_migrations")
    if incompatible_columns or primary_key != ("migration_id",):
        return {
            "state": "incompatible",
            "table_present": True,
            "incompatible_columns": incompatible_columns,
            "primary_key": primary_key,
        }

    marker = await conn.execute(
        text("SELECT checksum, applied_at FROM schema_migrations WHERE migration_id=:migration_id"),
        {"migration_id": CATALOG_SCHEMA_MIGRATION_ID},
    )
    rows = marker.fetchall()
    if len(rows) > 1:
        return {"state": "incompatible", "table_present": True, "duplicate_marker_rows": len(rows)}
    if not rows:
        return {"state": "absent", "table_present": True}
    row = rows[0]
    checksum = str(row[0] or "")
    if checksum != CATALOG_SCHEMA_MIGRATION_CHECKSUM:
        return {"state": "checksum_mismatch", "table_present": True}
    return {
        "state": "matching",
        "table_present": True,
        "applied_at": str(row[1]) if row[1] is not None else None,
    }


async def _catalog_schema_info(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_names: set[str],
) -> Dict[str, Any]:
    product_details = (
        await _table_column_details(conn, target, "products") if "products" in table_names else {}
    )
    products_columns = {column_name: str(detail["type"]) for column_name, detail in product_details.items()}
    expected_product_columns = {name for name, _column_type in PRODUCT_CATALOG_COLUMNS}
    actual_product_columns = expected_product_columns & set(products_columns)
    expected_tables = set(_CATALOG_TABLE_NAMES)
    actual_tables = expected_tables & table_names
    indexes = await _index_names(conn, target)
    actual_indexes = set(_EXPECTED_INDEX_NAMES) & indexes

    errors: List[str] = []
    if actual_product_columns and actual_product_columns != expected_product_columns:
        errors.append("catalog_product_columns_partial")
    if actual_tables and actual_tables != expected_tables:
        errors.append("catalog_tables_partial")

    if actual_product_columns:
        errors.extend(
            _column_contract_errors(
                "products",
                product_details,
                PRODUCT_CATALOG_COLUMN_SPECS,
                sorted(actual_product_columns),
            )
        )

    for table_name in actual_tables:
        expected_columns = set(CATALOG_TABLE_COLUMNS[table_name])
        details = await _table_column_details(conn, target, table_name)
        actual_columns = set(details)
        if expected_columns - actual_columns:
            errors.append(f"catalog_table_incompatible:{table_name}")
        if actual_columns - expected_columns:
            errors.append(f"catalog_table_unexpected_columns:{table_name}")
        errors.extend(
            _column_contract_errors(
                table_name,
                details,
                CATALOG_TABLE_COLUMN_SPECS[table_name],
                sorted(expected_columns & actual_columns),
            )
        )
        primary_key = await _primary_key_columns(conn, target, table_name)
        if primary_key != CATALOG_TABLE_PRIMARY_KEYS[table_name]:
            errors.append(f"catalog_table_primary_key_incompatible:{table_name}")
        unique_sets = await _unique_index_column_sets(conn, target, table_name)
        expected_unique_sets = {
            CATALOG_TABLE_PRIMARY_KEYS[table_name],
            *CATALOG_TABLE_UNIQUE_SETS[table_name],
        }
        if unique_sets != expected_unique_sets:
            errors.append(f"catalog_table_unique_constraints_incompatible:{table_name}")
        foreign_keys = await _foreign_key_shapes(conn, target, table_name)
        expected_foreign_keys = {
            (column_name, referenced_table, referenced_column, "RESTRICT")
            for column_name, referenced_table, referenced_column in CATALOG_TABLE_FOREIGN_KEYS[table_name]
        }
        if foreign_keys != expected_foreign_keys:
            errors.append(f"catalog_table_foreign_keys_incompatible:{table_name}")

    for index_name in actual_indexes:
        expected_unique, expected_table, expected_columns = INDEX_SPECS[index_name]
        shape = await _named_index_shape(conn, target, expected_table, index_name)
        if shape is None or shape["unique"] != expected_unique or shape["columns"] != expected_columns:
            errors.append(f"catalog_index_incompatible:{index_name}")

    immutability = {"missing": [], "incompatible": []}
    if "catalog_sync_run_items" in actual_tables:
        immutability = await _run_item_immutability_state(conn, target)
        if immutability["incompatible"]:
            errors.append("catalog_sync_run_item_immutability_incompatible")

    if not actual_product_columns and not actual_tables:
        state = "absent"
    elif errors:
        state = "partial_incompatible"
    elif actual_product_columns == expected_product_columns and actual_tables == expected_tables:
        state = (
            "complete"
            if actual_indexes == set(_EXPECTED_INDEX_NAMES) and not immutability["missing"]
            else "resumable"
        )
    else:
        state = "partial_incompatible"

    return {
        "state": state,
        "errors": errors,
        "missing_product_columns": sorted(expected_product_columns - actual_product_columns),
        "missing_tables": sorted(expected_tables - actual_tables),
        "missing_indexes": sorted(set(_EXPECTED_INDEX_NAMES) - actual_indexes),
        "missing_immutability_guards": immutability["missing"],
    }


async def _capture_legacy_baseline(
    conn: AsyncConnection,
    target: DatabaseTarget,
    table_names: set[str],
) -> Dict[str, Any]:
    products = (
        await conn.execute(text("SELECT sku, name, price, cost_price, active FROM products ORDER BY sku"))
    ).fetchall()
    topup = (
        await conn.execute(text("SELECT id, nominal, price, product_cost FROM topup ORDER BY id"))
    ).fetchall()
    promos = (
        await conn.execute(
            text("SELECT id, code, target_scope, target_value FROM promos ORDER BY id")
        )
    ).fetchall()

    product_skus = [str(row[0]) for row in products]
    raw_duplicates = len(product_skus) != len(set(product_skus))
    casefolded: Dict[str, List[str]] = {}
    for sku in product_skus:
        casefolded.setdefault(sku.strip().casefold(), []).append(sku)
    casefold_collisions = sorted(
        normalized for normalized, values in casefolded.items() if len(values) > 1
    )

    target_rows: List[Sequence[Any]] = []
    if "promotion_target_catalog" in table_names:
        target_columns = await _table_columns(conn, target, "promotion_target_catalog")
        selected_columns = [
            column
            for column in ("promo_id", "target_type", "target_key", "active", "excluded")
            if column in target_columns
        ]
        if selected_columns:
            result = await conn.execute(
                text(f"SELECT {', '.join(selected_columns)} FROM promotion_target_catalog")
            )
            target_rows = result.fetchall()

    parts = {
        "products": _fingerprint_rows(products),
        "topup": _fingerprint_rows(topup),
        "promos": _fingerprint_rows(promos),
        "promotion_targets": _fingerprint_rows(target_rows),
    }
    baseline_fingerprint = hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    bootstrap_presence = {sku: sku in set(product_skus) for sku in _BOOTSTRAP_SKUS}
    return {
        "fingerprint": baseline_fingerprint,
        "product_count": len(products),
        "topup_count": len(topup),
        "promo_count": len(promos),
        "promotion_target_count": len(target_rows),
        "bootstrap_presence": bootstrap_presence,
        "raw_sku_duplicates": raw_duplicates,
        "casefold_sku_collisions": casefold_collisions,
    }


async def _preflight_on_connection(conn: AsyncConnection, target: DatabaseTarget) -> Dict[str, Any]:
    await conn.execute(text("SELECT 1"))
    resolved_target = await _resolved_target_summary(conn, target)
    table_names = await _table_names(conn, target)
    base_missing: Dict[str, List[str]] = {}
    for table_name, required_columns in _BASE_REQUIRED_COLUMNS.items():
        if table_name not in table_names:
            base_missing[table_name] = list(required_columns)
            continue
        columns = await _table_columns(conn, target, table_name)
        missing_columns = sorted(set(required_columns) - set(columns))
        if missing_columns:
            base_missing[table_name] = missing_columns

    marker = await _marker_info(conn, target, table_names)
    catalog_schema = await _catalog_schema_info(conn, target, table_names)
    baseline: Optional[Dict[str, Any]] = None
    backfill_precheck: Optional[Dict[str, Any]] = None
    issues: List[str] = []
    if base_missing:
        issues.append("legacy_base_schema_missing_or_incompatible")
    else:
        baseline = await _capture_legacy_baseline(conn, target, table_names)
        if not all(baseline["bootstrap_presence"].values()):
            issues.append("bootstrap_products_missing")
        if baseline["raw_sku_duplicates"] or baseline["casefold_sku_collisions"]:
            issues.append("sku_collision_detected")

        catalog_product_columns = {column_name for column_name, _column_type in PRODUCT_CATALOG_COLUMNS}
        product_columns = await _table_columns(conn, target, "products")
        if catalog_product_columns.issubset(product_columns):
            backfill_issues = await _existing_legacy_backfill_issues(conn)
            backfill_precheck = {"valid": not backfill_issues, "issues": backfill_issues}
            if backfill_issues:
                issues.append("catalog_backfill_values_incompatible")

    if marker["state"] == "incompatible":
        issues.append("schema_migrations_incompatible")
    if marker["state"] == "checksum_mismatch":
        issues.append("catalog_marker_checksum_mismatch")
    if catalog_schema["state"] == "partial_incompatible":
        issues.extend(catalog_schema["errors"] or ["catalog_schema_partial_incompatible"])
    if marker["state"] == "matching" and catalog_schema["state"] != "complete":
        issues.append("catalog_marker_requires_complete_schema")

    return {
        "target": resolved_target,
        "base_schema": {"valid": not base_missing, "missing": base_missing},
        "catalog_schema": catalog_schema,
        "marker": marker,
        "baseline": baseline,
        "backfill_precheck": backfill_precheck,
        "issues": sorted(set(issues)),
        "can_upgrade": not issues,
    }


def _backup_manifest_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.catalog-migration.json")


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BackupVerificationError("Backup manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise BackupVerificationError("Backup manifest has an invalid format")
    return value


def _sqlite_integrity_ok(path: Path) -> bool:
    try:
        with sqlite3.connect(str(path)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error:
        return False
    return bool(result and str(result[0]).strip().lower() == "ok")


def _verify_sqlite_backup(
    backup_path: Path,
    target: DatabaseTarget,
    baseline_fingerprint: str,
) -> Dict[str, Any]:
    manifest_path = _backup_manifest_path(backup_path)
    if not backup_path.exists() or not manifest_path.exists():
        raise BackupVerificationError("SQLite backup artifact and verified manifest are required")
    manifest = _load_json(manifest_path)
    if (
        manifest.get("manifest_version") != BACKUP_MANIFEST_VERSION
        or manifest.get("dialect") != "sqlite"
        or manifest.get("target_fingerprint") != target.fingerprint
        or manifest.get("baseline_fingerprint") != baseline_fingerprint
        or int(manifest.get("size_bytes") or -1) != backup_path.stat().st_size
        or manifest.get("sha256") != _hash_bytes(backup_path)
        or not _sqlite_integrity_ok(backup_path)
    ):
        raise BackupVerificationError("SQLite backup verification failed")
    return {
        "dialect": "sqlite",
        "artifact": backup_path.name,
        "sha256": str(manifest["sha256"]),
        "size_bytes": int(manifest["size_bytes"]),
        "integrity_check": "ok",
    }


def _create_and_verify_sqlite_backup(
    backup_path: Path,
    target: DatabaseTarget,
    baseline_fingerprint: str,
) -> Dict[str, Any]:
    if target.sqlite_path is None or not target.sqlite_path.exists():
        raise BackupRequiredError("SQLite source database file is unavailable for a verified backup")
    resolved_backup = backup_path.expanduser().resolve()
    if resolved_backup == target.sqlite_path:
        raise BackupRequiredError("SQLite backup path must not be the database target")
    if not resolved_backup.parent.exists():
        raise BackupRequiredError("SQLite backup directory must already exist")
    if resolved_backup.exists() or _backup_manifest_path(resolved_backup).exists():
        return _verify_sqlite_backup(resolved_backup, target, baseline_fingerprint)

    try:
        with sqlite3.connect(str(target.sqlite_path)) as source_connection:
            with sqlite3.connect(str(resolved_backup)) as backup_connection:
                source_connection.backup(backup_connection)
    except sqlite3.Error as exc:
        raise BackupVerificationError("SQLite online backup could not be created") from exc

    if not _sqlite_integrity_ok(resolved_backup):
        raise BackupVerificationError("SQLite backup integrity check failed")
    manifest = {
        "manifest_version": BACKUP_MANIFEST_VERSION,
        "dialect": "sqlite",
        "target_fingerprint": target.fingerprint,
        "baseline_fingerprint": baseline_fingerprint,
        "artifact": resolved_backup.name,
        "sha256": _hash_bytes(resolved_backup),
        "size_bytes": resolved_backup.stat().st_size,
        "integrity_check": "ok",
        "created_at": _utc_now(),
    }
    manifest_path = _backup_manifest_path(resolved_backup)
    temporary_manifest = Path(f"{manifest_path}.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return _verify_sqlite_backup(resolved_backup, target, baseline_fingerprint)


def _verify_postgres_backup(
    manifest_path: Path,
    target: DatabaseTarget,
    baseline_fingerprint: str,
    *,
    target_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise BackupVerificationError("PostgreSQL backup manifest is required")
    manifest = _load_json(manifest_path)
    artifact_value = str(manifest.get("artifact") or "").strip()
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = (manifest_path.parent / artifact_path).resolve()
    backup_format = str(manifest.get("backup_format") or "").strip().casefold()
    dump_tool = str(manifest.get("dump_tool") or "").strip().casefold()
    if (
        manifest.get("manifest_version") != BACKUP_MANIFEST_VERSION
        or manifest.get("dialect") != "postgresql"
        or manifest.get("target_fingerprint") != (target_fingerprint or target.fingerprint)
        or manifest.get("baseline_fingerprint") != baseline_fingerprint
        or dump_tool != "pg_dump"
        or backup_format not in {"custom", "plain"}
        or not str(manifest.get("pg_dump_version") or "").strip()
        or not str(manifest.get("dump_completed_at") or "").strip()
        or not str(manifest.get("restore_verified_at") or "").strip()
        or not str(manifest.get("restore_verification_id") or "").strip()
        or not artifact_path.exists()
        or not artifact_path.is_file()
        or int(manifest.get("size_bytes") or -1) != artifact_path.stat().st_size
        or manifest.get("sha256") != _hash_bytes(artifact_path)
    ):
        raise BackupVerificationError("PostgreSQL backup verification failed")
    return {
        "dialect": "postgresql",
        "artifact": artifact_path.name,
        "sha256": str(manifest["sha256"]),
        "size_bytes": int(manifest["size_bytes"]),
        "backup_format": backup_format,
        "restore_verified_at": str(manifest["restore_verified_at"]),
        "restore_verification_id": str(manifest["restore_verification_id"]),
    }


def _backup_preflight(
    target: DatabaseTarget,
    baseline: Optional[Dict[str, Any]],
    sqlite_backup_path: Optional[Path],
    postgres_backup_manifest: Optional[Path],
    *,
    target_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    if baseline is None:
        return {"required": True, "valid": False, "reason": "baseline_unavailable"}
    try:
        if target.dialect == "sqlite":
            if sqlite_backup_path is None:
                return {"required": True, "valid": False, "reason": "backup_path_not_supplied"}
            info = _verify_sqlite_backup(
                sqlite_backup_path.expanduser().resolve(), target, baseline["fingerprint"]
            )
        else:
            if postgres_backup_manifest is None:
                return {"required": True, "valid": False, "reason": "backup_manifest_not_supplied"}
            info = _verify_postgres_backup(
                postgres_backup_manifest.expanduser().resolve(),
                target,
                baseline["fingerprint"],
                target_fingerprint=target_fingerprint,
            )
    except CatalogMigrationError as exc:
        return {"required": True, "valid": False, "reason": exc.code}
    return {"required": True, "valid": True, "backup": info}


async def preflight_catalog_schema(
    database_url: str,
    *,
    sqlite_backup_path: Optional[Path] = None,
    postgres_backup_manifest: Optional[Path] = None,
    postgres_read_only: bool = False,
) -> Dict[str, Any]:
    """Inspect a target and return only safe identity/fingerprint data.

    postgres_read_only is a narrowly scoped guard for an owner-reviewed
    PostgreSQL inspection. It leaves the default migration-runner contract
    unchanged while requiring the database server to reject writes during the
    full preflight transaction.
    """

    target = database_target(database_url)
    if postgres_read_only and target.dialect != "postgresql":
        raise DatabaseTargetError("PostgreSQL read-only mode requires a PostgreSQL target")

    engine = create_migration_engine(target, postgres_read_only=postgres_read_only)
    try:
        async with engine.connect() as conn:
            transaction_started = False
            try:
                if postgres_read_only:
                    # The engine uses AUTOCOMMIT specifically so this literal
                    # statement starts the transaction in PostgreSQL.
                    await conn.exec_driver_sql("BEGIN READ ONLY")
                    transaction_started = True
                report = await _preflight_on_connection(conn, target)
            finally:
                if transaction_started:
                    # Never commit an inspection, including when preflight
                    # raises after partially reading catalog metadata.
                    await conn.exec_driver_sql("ROLLBACK")
        if report["marker"]["state"] == "matching":
            # A matching marker causes upgrade to perform verification only;
            # no schema/data mutation remains to protect with a new backup.
            report["backup"] = {"required": False, "valid": True, "reason": "already_applied"}
        else:
            report["backup"] = _backup_preflight(
                target,
                report.get("baseline"),
                sqlite_backup_path,
                postgres_backup_manifest,
                target_fingerprint=str(report["target"]["fingerprint"]),
            )
        report["can_apply"] = bool(report.get("can_upgrade") and report["backup"].get("valid"))
        return report
    finally:
        await engine.dispose()


def _raise_for_preflight(report: Mapping[str, Any]) -> None:
    issues = list(report.get("issues") or [])
    if not report.get("can_upgrade"):
        if "catalog_marker_checksum_mismatch" in issues:
            raise MigrationChecksumMismatch("Catalog migration marker checksum does not match this runner")
        raise PreflightError("Catalog migration preflight rejected the target schema")


async def _ensure_marker_table(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


async def _ensure_product_catalog_columns(conn: AsyncConnection, target: DatabaseTarget) -> None:
    existing_columns = await _table_columns(conn, target, "products")
    for column_name, column_type in PRODUCT_CATALOG_COLUMNS:
        if column_name not in existing_columns:
            await conn.execute(text(f"ALTER TABLE products ADD COLUMN {column_name} {column_type}"))


async def _ensure_catalog_tables(conn: AsyncConnection) -> None:
    for _table_name, statement in CATALOG_TABLE_DDL:
        await conn.execute(text(statement))


async def _ensure_run_item_immutability(conn: AsyncConnection, target: DatabaseTarget) -> None:
    """Make stored preview rows append-only before the marker can be written."""

    if target.dialect == "sqlite":
        for statement in _SQLITE_RUN_ITEM_IMMUTABILITY_TRIGGERS.values():
            await conn.execute(text(statement))
        return

    await conn.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION {_POSTGRES_RUN_ITEM_IMMUTABILITY_FUNCTION}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'catalog_sync_run_items are immutable';
            END;
            $$
            """
        )
    )
    state = await _run_item_immutability_state(conn, target)
    if state["incompatible"]:
        raise MigrationVerificationError("Catalog run-item immutability guard is incompatible")
    if state["missing"]:
        await conn.execute(
            text(
                f"""
                CREATE TRIGGER {_POSTGRES_RUN_ITEM_IMMUTABILITY_TRIGGER}
                BEFORE UPDATE OR DELETE ON catalog_sync_run_items
                FOR EACH ROW EXECUTE FUNCTION {_POSTGRES_RUN_ITEM_IMMUTABILITY_FUNCTION}()
                """
            )
        )


async def _assert_existing_catalog_ids_valid(conn: AsyncConnection) -> None:
    values = (
        await conn.execute(
            text(
                "SELECT catalog_product_id FROM products "
                "WHERE catalog_product_id IS NOT NULL"
            )
        )
    ).fetchall()
    invalid_ids = sum(
        1 for row in values if not _is_supported_catalog_product_id(row[0])
    )
    duplicate_ids = int(
        (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT catalog_product_id
                        FROM products
                        WHERE catalog_product_id IS NOT NULL
                        GROUP BY catalog_product_id
                        HAVING COUNT(*) > 1
                    ) duplicate_ids
                    """
                )
            )
        ).scalar_one()
        or 0
    )
    if invalid_ids or duplicate_ids:
        raise MigrationVerificationError("Existing catalog product IDs are invalid or not unique")


def _is_supported_catalog_product_id(value: Any) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    try:
        UUID(candidate)
        return True
    except (TypeError, ValueError, AttributeError):
        # ULID remains an allowed portable alternative even though this runner
        # creates UUID4 identifiers itself.
        return bool(re.fullmatch(r"[0-7][0-9A-HJKMNPQRSTVWXYZ]{25}", candidate.upper()))


def _coerce_binary_flag(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    normalized = str(value if value is not None else "").strip().casefold()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return 1
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return 0
    return None


def _store_enabled_from_active(value: Any) -> int:
    normalized = _coerce_binary_flag(value)
    if normalized is None:
        raise MigrationVerificationError("Legacy active values must be explicit binary publication flags")
    return normalized


def _legacy_logical_key(sku: str) -> str:
    normalized_sku = str(sku or "").strip().casefold()
    if not normalized_sku:
        raise MigrationVerificationError("Legacy products must have nonblank SKU values")
    return f"legacy-sku:{normalized_sku}"


async def _existing_legacy_backfill_issues(conn: AsyncConnection) -> List[str]:
    """Reject hand-written partial values rather than silently endorsing them."""

    rows = (
        await conn.execute(
            text(
                """
                SELECT sku, active, catalog_product_id, logical_key,
                       logical_key_version, store_enabled
                FROM products
                ORDER BY sku
                """
            )
        )
    ).fetchall()
    issues: set[str] = set()
    for sku, active, catalog_product_id, logical_key, logical_key_version, store_enabled in rows:
        raw_sku = str(sku or "").strip()
        if not raw_sku:
            issues.add("legacy_sku_invalid")
            continue
        expected_logical_key = _legacy_logical_key(raw_sku)
        active_flag = _coerce_binary_flag(active)
        if active_flag is None:
            issues.add("legacy_active_value_invalid")
        if catalog_product_id is not None and not _is_supported_catalog_product_id(catalog_product_id):
            issues.add("catalog_product_id_invalid")
        if logical_key is not None and str(logical_key) != expected_logical_key:
            issues.add("legacy_logical_key_invalid")
        if logical_key_version is not None and str(logical_key_version) != LEGACY_LOGICAL_KEY_VERSION:
            issues.add("legacy_logical_key_version_invalid")
        if store_enabled is not None:
            stored_flag = _coerce_binary_flag(store_enabled)
            if stored_flag is None:
                issues.add("store_enabled_value_invalid")
            elif active_flag is not None and stored_flag != active_flag:
                issues.add("store_enabled_legacy_bridge_invalid")
    return sorted(issues)


async def _backfill_legacy_products(conn: AsyncConnection) -> Dict[str, int]:
    existing_issues = await _existing_legacy_backfill_issues(conn)
    if existing_issues:
        raise MigrationVerificationError("Existing catalog backfill values are incompatible")
    result = await conn.execute(
        text(
            """
            SELECT sku, active, catalog_product_id, logical_key,
                   logical_key_version, store_enabled
            FROM products
            ORDER BY sku
            """
        )
    )
    created_ids = 0
    completed_rows = 0
    for row in result.fetchall():
        sku, active, catalog_product_id, logical_key, logical_key_version, store_enabled = row
        if catalog_product_id is not None and not str(catalog_product_id).strip():
            raise MigrationVerificationError("Existing catalog product IDs must not be blank")

        updates: Dict[str, Any] = {"sku": sku}
        assignments: List[str] = []
        if catalog_product_id is None:
            updates["catalog_product_id"] = str(uuid4())
            assignments.append("catalog_product_id=:catalog_product_id")
            created_ids += 1
        if logical_key is None:
            updates["logical_key"] = _legacy_logical_key(str(sku))
            assignments.append("logical_key=:logical_key")
        if logical_key_version is None:
            updates["logical_key_version"] = LEGACY_LOGICAL_KEY_VERSION
            assignments.append("logical_key_version=:logical_key_version")
        if store_enabled is None:
            updates["store_enabled"] = _store_enabled_from_active(active)
            assignments.append("store_enabled=:store_enabled")
        if assignments:
            await conn.execute(
                text(f"UPDATE products SET {', '.join(assignments)} WHERE sku=:sku"),
                updates,
            )
        completed_rows += 1
    await _assert_existing_catalog_ids_valid(conn)
    return {"products_seen": completed_rows, "catalog_product_ids_created": created_ids}


async def _ensure_indexes(conn: AsyncConnection) -> None:
    for _index_name, statement in INDEX_STATEMENTS:
        await conn.execute(text(statement))


async def _logical_key_has_unique_index(conn: AsyncConnection, target: DatabaseTarget) -> bool:
    if target.dialect == "sqlite":
        result = await conn.execute(text("PRAGMA index_list(products)"))
        for row in result.fetchall():
            index_name = str(row[1])
            is_unique = bool(row[2])
            if not is_unique:
                continue
            index_identifier = _quote_sqlite_identifier(index_name)
            columns = await conn.execute(text(f"PRAGMA index_info({index_identifier})"))
            if "logical_key" in {str(index_row[2]) for index_row in columns.fetchall()}:
                return True
        return False

    result = await conn.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname=current_schema() AND tablename='products'
            """
        )
    )
    return any(
        "CREATE UNIQUE INDEX" in str(row[0]).upper() and "logical_key" in str(row[0])
        for row in result.fetchall()
    )


async def _verify_on_connection(
    conn: AsyncConnection,
    target: DatabaseTarget,
    *,
    expected_baseline_fingerprint: Optional[str] = None,
    require_marker: bool = True,
) -> Dict[str, Any]:
    report = await _preflight_on_connection(conn, target)
    errors = list(report.get("issues") or [])
    catalog_schema = report["catalog_schema"]
    marker = report["marker"]
    baseline = report.get("baseline")

    if catalog_schema["state"] != "complete":
        errors.append("catalog_schema_not_complete")
    if require_marker and marker["state"] != "matching":
        errors.append("catalog_marker_not_matching")
    if expected_baseline_fingerprint and (
        baseline is None or baseline.get("fingerprint") != expected_baseline_fingerprint
    ):
        errors.append("legacy_baseline_fingerprint_changed")

    if baseline is not None and catalog_schema["state"] in {"complete", "resumable"}:
        backfill_value_issues = await _existing_legacy_backfill_issues(conn)
        if backfill_value_issues:
            errors.append("catalog_backfill_values_incompatible")
        product_count = int(
            (await conn.execute(text("SELECT COUNT(*) FROM products"))).scalar_one() or 0
        )
        ids_present = int(
            (
                await conn.execute(
                    text("SELECT COUNT(*) FROM products WHERE catalog_product_id IS NOT NULL")
                )
            ).scalar_one()
            or 0
        )
        distinct_ids = int(
            (
                await conn.execute(
                    text("SELECT COUNT(DISTINCT catalog_product_id) FROM products")
                )
            ).scalar_one()
            or 0
        )
        bridge_mismatches = int(
            (
                await conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM products
                        WHERE store_enabled IS NULL
                           OR COALESCE(store_enabled, 0) <> COALESCE(active, 0)
                        """
                    )
                )
            ).scalar_one()
            or 0
        )
        incomplete_logical_keys = int(
            (
                await conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM products
                        WHERE logical_key IS NULL OR logical_key_version IS NULL
                        """
                    )
                )
            ).scalar_one()
            or 0
        )
        if ids_present != product_count or distinct_ids != product_count:
            errors.append("catalog_product_identity_backfill_invalid")
        if bridge_mismatches:
            errors.append("store_enabled_legacy_bridge_invalid")
        if incomplete_logical_keys:
            errors.append("legacy_logical_key_backfill_incomplete")
        if await _logical_key_has_unique_index(conn, target):
            errors.append("logical_key_must_not_be_unique")
        report["backfill"] = {
            "product_count": product_count,
            "catalog_ids_present": ids_present,
            "distinct_catalog_ids": distinct_ids,
            "store_enabled_mismatches": bridge_mismatches,
            "incomplete_logical_keys": incomplete_logical_keys,
            "value_issues": backfill_value_issues,
        }

    report["errors"] = sorted(set(errors))
    report["valid"] = not report["errors"]
    return report


async def _write_marker(conn: AsyncConnection) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO schema_migrations (migration_id, checksum)
            VALUES (:migration_id, :checksum)
            """
        ),
        {
            "migration_id": CATALOG_SCHEMA_MIGRATION_ID,
            "checksum": CATALOG_SCHEMA_MIGRATION_CHECKSUM,
        },
    )


async def _upgrade_locked(
    conn: AsyncConnection,
    target: DatabaseTarget,
    *,
    expected_target_fingerprint: str,
    sqlite_backup_path: Optional[Path],
    postgres_backup_manifest: Optional[Path],
) -> Dict[str, Any]:
    preflight = await _preflight_on_connection(conn, target)
    if str(preflight["target"]["fingerprint"]) != str(expected_target_fingerprint or ""):
        raise DatabaseTargetError("Target fingerprint does not match the connected database and schema")
    _raise_for_preflight(preflight)
    marker = preflight["marker"]
    if marker["state"] == "matching":
        verification = await _verify_on_connection(conn, target, require_marker=True)
        if not verification["valid"]:
            raise MigrationVerificationError("Applied catalog migration no longer verifies")
        return {
            "success": True,
            "already_applied": True,
            "migration_id": CATALOG_SCHEMA_MIGRATION_ID,
            "checksum": CATALOG_SCHEMA_MIGRATION_CHECKSUM,
            "target": preflight["target"],
            "verification": verification,
        }

    baseline = preflight.get("baseline")
    if baseline is None:
        raise PreflightError("Catalog migration cannot capture a legacy baseline")
    if target.dialect == "sqlite":
        if sqlite_backup_path is None:
            raise BackupRequiredError("SQLite upgrade requires an explicit backup path")
        backup = _create_and_verify_sqlite_backup(
            sqlite_backup_path, target, baseline["fingerprint"]
        )
    else:
        if postgres_backup_manifest is None:
            raise BackupRequiredError("PostgreSQL upgrade requires a verified pg_dump manifest")
        backup = _verify_postgres_backup(
            postgres_backup_manifest,
            target,
            baseline["fingerprint"],
            target_fingerprint=str(preflight["target"]["fingerprint"]),
        )

    # All DDL and legacy backfill are within the same migration lock/transaction.
    await _ensure_marker_table(conn)
    await _ensure_product_catalog_columns(conn, target)
    await _ensure_catalog_tables(conn)
    await _ensure_run_item_immutability(conn, target)
    backfill = await _backfill_legacy_products(conn)
    await _ensure_indexes(conn)

    verification_before_marker = await _verify_on_connection(
        conn,
        target,
        expected_baseline_fingerprint=baseline["fingerprint"],
        require_marker=False,
    )
    if not verification_before_marker["valid"]:
        raise MigrationVerificationError("Catalog migration validation failed before marker write")

    # The marker is intentionally the final mutation.  A rollback or crash
    # before this statement leaves no completed migration marker.
    await _write_marker(conn)
    marker_result = await conn.execute(
        text("SELECT checksum, applied_at FROM schema_migrations WHERE migration_id=:migration_id"),
        {"migration_id": CATALOG_SCHEMA_MIGRATION_ID},
    )
    marker_row = marker_result.one()
    return {
        "success": True,
        "already_applied": False,
        "migration_id": CATALOG_SCHEMA_MIGRATION_ID,
        "checksum": CATALOG_SCHEMA_MIGRATION_CHECKSUM,
        "target": preflight["target"],
        "backup": backup,
        "backfill": backfill,
        "marker": {"checksum": str(marker_row[0]), "applied_at": str(marker_row[1])},
        "verification": verification_before_marker,
    }


async def _upgrade_sqlite_with_immediate_lock(
    engine: AsyncEngine,
    target: DatabaseTarget,
    *,
    expected_target_fingerprint: str,
    sqlite_backup_path: Optional[Path],
    postgres_backup_manifest: Optional[Path],
) -> Optional[Dict[str, Any]]:
    """Return ``None`` when SQLite's migration lock cannot be acquired.

    Keeping the rejected lock path inside this short-lived coroutine ensures
    that aiosqlite's Windows resources are finalized before the public API
    raises its stable ``MigrationLockError``.
    """

    conn = await engine.connect()
    lock_acquired = False
    try:
        try:
            await conn.exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError:
            return None
        lock_acquired = True
        result = await _upgrade_locked(
            conn,
            target,
            expected_target_fingerprint=expected_target_fingerprint,
            sqlite_backup_path=sqlite_backup_path,
            postgres_backup_manifest=postgres_backup_manifest,
        )
        await conn.commit()
        return result
    except Exception:
        if lock_acquired:
            await conn.rollback()
        raise
    finally:
        await conn.close()


async def upgrade_catalog_schema(
    database_url: str,
    *,
    target_fingerprint: str,
    sqlite_backup_path: Optional[Path] = None,
    postgres_backup_manifest: Optional[Path] = None,
    sqlite_engine_stopped: bool = False,
) -> Dict[str, Any]:
    """Run the catalog migration only after explicit target/backup confirmation."""

    target = database_target(database_url)
    supplied_target_fingerprint = str(target_fingerprint or "")
    if target.dialect == "sqlite" and supplied_target_fingerprint != target.fingerprint:
        raise DatabaseTargetError("Target fingerprint does not match the explicit database target")
    if target.dialect == "sqlite" and not sqlite_engine_stopped:
        raise MigrationLockError("SQLite migration requires explicit engine-stopped acknowledgement")

    engine = create_migration_engine(target)
    try:
        if target.dialect == "sqlite":
            result = await _upgrade_sqlite_with_immediate_lock(
                engine,
                target,
                expected_target_fingerprint=supplied_target_fingerprint,
                sqlite_backup_path=sqlite_backup_path,
                postgres_backup_manifest=postgres_backup_manifest,
            )
            if result is None:
                raise MigrationLockError("SQLite migration lock is unavailable")
            return result

        # The PostgreSQL preflight fingerprint additionally binds the live
        # current database/schema. Reconfirm it before taking the advisory
        # migration lock, and once more inside _upgrade_locked.
        async with engine.connect() as identity_connection:
            resolved_target = await _resolved_target_summary(identity_connection, target)
        if supplied_target_fingerprint != resolved_target["fingerprint"]:
            raise DatabaseTargetError("Target fingerprint does not match the explicit database and schema")

        async with engine.begin() as conn:
            lock_result = await conn.execute(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": _MIGRATION_ADVISORY_LOCK_KEY},
            )
            if not bool(lock_result.scalar_one()):
                raise MigrationLockError("PostgreSQL migration lock is unavailable")
            return await _upgrade_locked(
                conn,
                target,
                expected_target_fingerprint=supplied_target_fingerprint,
                sqlite_backup_path=sqlite_backup_path,
                postgres_backup_manifest=postgres_backup_manifest,
            )
    finally:
        await engine.dispose()


async def verify_catalog_schema(
    database_url: str,
    *,
    target_fingerprint: str,
) -> Dict[str, Any]:
    """Read-only verification of an already-applied catalog migration."""

    target = database_target(database_url)
    engine = create_migration_engine(target)
    try:
        async with engine.connect() as conn:
            report = await _verify_on_connection(conn, target, require_marker=True)
        if str(target_fingerprint or "") != str(report["target"]["fingerprint"]):
            raise DatabaseTargetError("Target fingerprint does not match the explicit database and schema")
        return report
    finally:
        await engine.dispose()


def cli_error_payload(exc: CatalogMigrationError) -> Dict[str, Any]:
    """Safe CLI error shape: never serialize database URLs or DBAPI details."""

    return {"success": False, "error_code": exc.code, "message": str(exc)}

