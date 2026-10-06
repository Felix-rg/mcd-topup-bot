"""Opt-in disposable PostgreSQL coverage for the Phase 1 catalog migration.

This module is deliberately unable to discover an application database.  It
only runs when an operator (or the manual GitHub Actions lane) supplies an
explicit local/disposable target and a separate administrative connection for
throw-away databases.  The application ``DATABASE_URL`` is never read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from catalog_schema import (
    BACKUP_MANIFEST_VERSION,
    CATALOG_SCHEMA_MIGRATION_CHECKSUM,
    CATALOG_SCHEMA_MIGRATION_ID,
    _MIGRATION_ADVISORY_LOCK_KEY,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "migrate_catalog_sync.py"
_DATABASE_NAME_RE = re.compile(r"^catalog_test_phase1_[a-z0-9_]+$")

BOOTSTRAP_PRODUCTS: Tuple[Tuple[str, str, str, int, int, int], ...] = (
    ("ml_10k", "Mobile Legends", "Diamonds 10K", 8500, 10000, 1),
    ("ff_12k", "Free Fire", "Diamonds 12K", 10000, 12000, 1),
    ("pubg_20k", "PUBG Mobile", "UC 20K", 17000, 20000, 1),
)


@dataclass(frozen=True)
class _PostgresTestConfig:
    target_url: URL = field(repr=False)
    admin_url: URL = field(repr=False)


@dataclass(frozen=True)
class _DisposableDatabase:
    name: str
    url: URL = field(repr=False)


@dataclass(frozen=True)
class _CliResult:
    returncode: int
    payload: Mapping[str, Any]


class _RedactedEnvironment(dict[str, str]):
    """Prevent credential-bearing subprocess environments from appearing in reprs."""

    def __repr__(self) -> str:
        return "<redacted subprocess environment>"


def _config_error(message: str) -> RuntimeError:
    return RuntimeError(f"Unsafe PostgreSQL disposable-test configuration: {message}")


def _load_config() -> Optional[_PostgresTestConfig]:
    """Load only explicitly opted-in test URLs and fail closed when malformed."""

    if os.environ.get("CATALOG_TEST_POSTGRES_DISPOSABLE") != "1":
        return None

    required = (
        "CATALOG_TEST_POSTGRES_URL",
        "CATALOG_TEST_POSTGRES_ADMIN_URL",
        "CATALOG_TEST_POSTGRES_ALLOW_RESET",
    )
    missing = [name for name in required if not str(os.environ.get(name) or "").strip()]
    if missing:
        raise _config_error("explicit disposable URL/admin/reset acknowledgement is incomplete")
    if os.environ.get("CATALOG_TEST_POSTGRES_ALLOW_RESET") != "1":
        raise _config_error("CATALOG_TEST_POSTGRES_ALLOW_RESET must be exactly 1")

    try:
        target_url = make_url(str(os.environ["CATALOG_TEST_POSTGRES_URL"]))
        admin_url = make_url(str(os.environ["CATALOG_TEST_POSTGRES_ADMIN_URL"]))
    except Exception:
        # Do not chain a parser error: some parser implementations include the
        # credential-bearing input URL in their exception text.
        raise _config_error("test URL is malformed") from None

    if target_url.drivername != "postgresql+asyncpg" or admin_url.drivername != "postgresql+asyncpg":
        raise _config_error("only postgresql+asyncpg URLs are allowed")
    if not target_url.host or not target_url.database or not admin_url.host or not admin_url.database:
        raise _config_error("target and admin URLs require explicit host and database names")

    allowed_hosts = {
        host.strip().casefold()
        for host in os.environ.get("CATALOG_TEST_POSTGRES_HOST_ALLOWLIST", "127.0.0.1,localhost").split(",")
        if host.strip()
    }
    if not allowed_hosts or target_url.host.casefold() not in allowed_hosts:
        raise _config_error("target host is not in CATALOG_TEST_POSTGRES_HOST_ALLOWLIST")
    if admin_url.host.casefold() not in allowed_hosts:
        raise _config_error("admin host is not in CATALOG_TEST_POSTGRES_HOST_ALLOWLIST")
    if (target_url.host.casefold(), int(target_url.port or 5432)) != (
        admin_url.host.casefold(),
        int(admin_url.port or 5432),
    ):
        raise _config_error("target and admin URLs must point to the same PostgreSQL instance")
    if (target_url.username or "") != (admin_url.username or ""):
        raise _config_error("target and admin URLs must use the same test role")
    if "catalog_test" not in target_url.database.casefold():
        raise _config_error("target database name must contain catalog_test")
    if admin_url.database.casefold() != "postgres":
        raise _config_error("admin URL must connect to the postgres maintenance database")
    if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
        raise _config_error("pg_dump and pg_restore must be available on PATH")

    return _PostgresTestConfig(target_url=target_url, admin_url=admin_url)


pytestmark = pytest.mark.postgres_disposable
_CONFIG = _load_config()
if _CONFIG is None:
    pytest.skip(
        "requires an explicitly marked disposable PostgreSQL target; application databases are never discovered",
        allow_module_level=True,
    )


def _safe_identifier(name: str) -> str:
    if not _DATABASE_NAME_RE.fullmatch(name):
        raise AssertionError("test database name did not satisfy the disposable-name contract")
    return f'"{name}"'


def _engine(database_url: URL):
    return create_async_engine(database_url, poolclass=NullPool, pool_pre_ping=True)


async def _create_database(config: _PostgresTestConfig, database_name: str) -> None:
    engine = create_async_engine(
        config.admin_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f"CREATE DATABASE {_safe_identifier(database_name)}"))
    finally:
        await engine.dispose()


async def _drop_database(config: _PostgresTestConfig, database_name: str) -> None:
    """Drop only a name generated by this test module, after closing its users."""

    identifier = _safe_identifier(database_name)
    engine = create_async_engine(
        config.admin_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname=:database_name AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            await connection.execute(text(f"DROP DATABASE IF EXISTS {identifier}"))
    finally:
        await engine.dispose()


class _DatabaseFactory:
    def __init__(self, config: _PostgresTestConfig) -> None:
        self._config = config
        self._created: list[_DisposableDatabase] = []

    def create(self, label: str) -> _DisposableDatabase:
        normalized_label = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if not normalized_label:
            raise AssertionError("test database label is invalid")
        name = f"catalog_test_phase1_{normalized_label}_{uuid4().hex[:12]}"
        _safe_identifier(name)
        asyncio.run(_create_database(self._config, name))
        database = _DisposableDatabase(
            name=name,
            url=self._config.target_url.set(database=name),
        )
        self._created.append(database)
        return database

    def cleanup(self) -> None:
        errors: list[BaseException] = []
        for database in reversed(self._created):
            try:
                asyncio.run(_drop_database(self._config, database.name))
            except BaseException as exc:  # pragma: no cover - preserves the original test failure
                errors.append(exc)
        if errors:
            raise errors[0]


@pytest.fixture
def disposable_databases() -> Iterator[_DatabaseFactory]:
    factory = _DatabaseFactory(_CONFIG)
    try:
        yield factory
    finally:
        factory.cleanup()


async def _create_legacy_baseline(database_url: URL, *, rich: bool) -> None:
    """Build a minimal app-shaped legacy schema without importing app bootstrap."""

    if rich:
        products_ddl = """
            CREATE TABLE products (
                sku TEXT PRIMARY KEY,
                provider TEXT,
                name TEXT,
                cost_price NUMERIC,
                price NUMERIC,
                active INTEGER,
                category TEXT,
                product_type TEXT,
                brand TEXT,
                description TEXT,
                image_url TEXT,
                logo_url TEXT,
                promo_title TEXT,
                promo_text TEXT,
                promo_badge TEXT,
                promo_url TEXT,
                display_order INTEGER
            )
        """
    else:
        products_ddl = """
            CREATE TABLE products (
                sku TEXT PRIMARY KEY,
                name TEXT,
                cost_price NUMERIC,
                price NUMERIC,
                active INTEGER
            )
        """

    engine = _engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(products_ddl))
            await connection.execute(
                text(
                    """
                    CREATE TABLE topup (
                        id TEXT PRIMARY KEY,
                        nominal TEXT,
                        price NUMERIC,
                        product_cost NUMERIC
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE promos (
                        id INTEGER PRIMARY KEY,
                        code TEXT,
                        target_scope TEXT,
                        target_value TEXT
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE promotion_target_catalog (
                        id INTEGER PRIMARY KEY,
                        promo_id INTEGER,
                        target_type TEXT,
                        target_key TEXT,
                        active INTEGER
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE provider_attempts (
                        id INTEGER PRIMARY KEY,
                        order_id TEXT,
                        provider TEXT,
                        request_state TEXT
                    )
                    """
                )
            )

            if rich:
                rows = [
                    {
                        "sku": sku,
                        "provider": provider,
                        "name": name,
                        "cost_price": cost_price,
                        "price": price,
                        "active": active,
                        "category": provider,
                        "product_type": "prepaid",
                        "brand": provider,
                        "description": f"Description {sku}",
                        "image_url": "image",
                        "logo_url": "logo",
                        "promo_title": "promo",
                        "promo_text": "text",
                        "promo_badge": "badge",
                        "promo_url": "url",
                        "display_order": 1,
                    }
                    for sku, provider, name, cost_price, price, active in BOOTSTRAP_PRODUCTS
                ]
                rows.append(
                    {
                        "sku": "legacy-special",
                        "provider": "Legacy Provider",
                        "name": "Legacy Product",
                        "cost_price": 12345,
                        "price": 15000,
                        "active": 0,
                        "category": "Legacy Category",
                        "product_type": "prepaid",
                        "brand": "Legacy Brand",
                        "description": "Legacy display description",
                        "image_url": "legacy-image",
                        "logo_url": "legacy-logo",
                        "promo_title": "Legacy Promo",
                        "promo_text": "Legacy promo text",
                        "promo_badge": "Legacy badge",
                        "promo_url": "https://example.invalid/promo",
                        "display_order": 77,
                    }
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO products (
                            sku, provider, name, cost_price, price, active, category,
                            product_type, brand, description, image_url, logo_url,
                            promo_title, promo_text, promo_badge, promo_url, display_order
                        ) VALUES (
                            :sku, :provider, :name, :cost_price, :price, :active, :category,
                            :product_type, :brand, :description, :image_url, :logo_url,
                            :promo_title, :promo_text, :promo_badge, :promo_url, :display_order
                        )
                        """
                    ),
                    rows,
                )
            else:
                await connection.execute(
                    text(
                        """
                        INSERT INTO products (sku, name, cost_price, price, active)
                        VALUES (:sku, :name, :cost_price, :price, :active)
                        """
                    ),
                    [
                        {
                            "sku": sku,
                            "name": name,
                            "cost_price": cost_price,
                            "price": price,
                            "active": active,
                        }
                        for sku, _provider, name, cost_price, price, active in BOOTSTRAP_PRODUCTS
                    ],
                )

            await connection.execute(
                text(
                    """
                    INSERT INTO topup (id, nominal, price, product_cost)
                    VALUES ('history-1', 'legacy-special', 15000, 12345)
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO promos (id, code, target_scope, target_value)
                    VALUES (1, 'LEGACY', 'sku', 'legacy-special')
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO promotion_target_catalog (id, promo_id, target_type, target_key, active)
                    VALUES (1, 1, 'sku', 'legacy-special', 1)
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO provider_attempts (id, order_id, provider, request_state)
                    VALUES (1, 'history-1', 'digiflazz', 'SENT_UNKNOWN')
                    """
                )
            )
    finally:
        await engine.dispose()


def _snapshot_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


async def _legacy_snapshot(database_url: URL, *, rich: bool) -> Dict[str, list[Tuple[Any, ...]]]:
    products_query = (
        """
        SELECT sku, provider, name, cost_price, price, active, category,
               product_type, brand, description, image_url, logo_url,
               promo_title, promo_text, promo_badge, promo_url, display_order
        FROM products ORDER BY sku
        """
        if rich
        else "SELECT sku, name, cost_price, price, active FROM products ORDER BY sku"
    )
    queries = {
        "products": products_query,
        "topup": "SELECT id, nominal, price, product_cost FROM topup ORDER BY id",
        "promos": "SELECT id, code, target_scope, target_value FROM promos ORDER BY id",
        "promotion_targets": (
            "SELECT id, promo_id, target_type, target_key, active "
            "FROM promotion_target_catalog ORDER BY id"
        ),
        "provider_attempts": "SELECT id, order_id, provider, request_state FROM provider_attempts ORDER BY id",
    }
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            snapshot: Dict[str, list[Tuple[Any, ...]]] = {}
            for key, query in queries.items():
                rows = (await connection.execute(text(query))).fetchall()
                snapshot[key] = [tuple(_snapshot_value(value) for value in row) for row in rows]
            return snapshot
    finally:
        await engine.dispose()


async def _fetchall(
    database_url: URL,
    query: str,
    params: Optional[Mapping[str, Any]] = None,
) -> list[Tuple[Any, ...]]:
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text(query), dict(params or {}))
            return [tuple(row) for row in result.fetchall()]
    finally:
        await engine.dispose()


async def _execute(database_url: URL, query: str, params: Optional[Mapping[str, Any]] = None) -> None:
    engine = _engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(query), dict(params or {}))
    finally:
        await engine.dispose()


def _runner_environment(database_url: URL) -> Dict[str, str]:
    """Keep the CLI hermetic: it receives one explicit disposable URL only."""

    environment = _RedactedEnvironment(os.environ)
    for key in (
        "DATABASE_URL",
        "CATALOG_TEST_POSTGRES_ADMIN_URL",
        "TRIPAY_API_KEY",
        "TRIPAY_PRIVATE_KEY",
        "TRIPAY_MERCHANT_CODE",
        "DIGIFLAZZ_USERNAME",
        "DIGIFLAZZ_API_KEY",
        "DIGIFLAZZ_WEBHOOK_SECRET",
    ):
        environment.pop(key, None)
    environment["CATALOG_TEST_POSTGRES_URL"] = database_url.render_as_string(hide_password=False)
    return environment


def _runner_output_contains_credentials(output: str, database_url: URL) -> bool:
    password = str(database_url.password or "")
    if password and password in output:
        return True
    return bool(re.search(r"(?i)postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s]+", output))


def _run_runner(database_url: URL, command: str, *arguments: str) -> _CliResult:
    process = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            command,
            "--database-url-env",
            "CATALOG_TEST_POSTGRES_URL",
            *arguments,
        ],
        cwd=str(ROOT),
        env=_runner_environment(database_url),
        capture_output=True,
        text=True,
        check=False,
    )
    returncode = process.returncode
    stdout = process.stdout
    stderr = process.stderr
    if _runner_output_contains_credentials(stdout, database_url) or _runner_output_contains_credentials(
        stderr, database_url
    ):
        del process, stdout, stderr
        pytest.fail("catalog migration CLI exposed an explicit database URL")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        del process, stdout, stderr
        raise AssertionError("catalog migration CLI did not emit its JSON contract") from None
    del process, stdout, stderr
    if not isinstance(payload, dict):
        raise AssertionError("catalog migration CLI payload was not an object")
    return _CliResult(returncode=returncode, payload=payload)


def _require_success(result: _CliResult) -> Mapping[str, Any]:
    if result.returncode != 0 or not result.payload.get("success"):
        raise AssertionError(
            "catalog migration CLI failed with "
            f"{result.payload.get('error_code', 'an unexpected result')}"
        )
    return result.payload


def _require_preflight_report(result: _CliResult) -> Mapping[str, Any]:
    """Validate the successful, read-only preflight report contract.

    Preflight reports schema suitability separately from apply authorization.
    They intentionally have no top-level ``success`` field because they do not
    perform an upgrade; command exit status and their report shape establish
    successful inspection.
    """

    if result.returncode != 0 or result.payload.get("success") is False or result.payload.get("error_code"):
        raise AssertionError(
            "catalog preflight CLI failed with "
            f"{result.payload.get('error_code', 'an unexpected result')}"
        )
    required = ("target", "can_upgrade", "can_apply", "backup", "backfill_precheck")
    missing = [field for field in required if field not in result.payload]
    if missing:
        raise AssertionError(f"catalog preflight CLI omitted required fields: {', '.join(missing)}")
    if not isinstance(result.payload["target"], Mapping) or not str(
        result.payload["target"].get("fingerprint") or ""
    ):
        raise AssertionError("catalog preflight CLI omitted target fingerprint")
    if not isinstance(result.payload["can_upgrade"], bool) or not isinstance(
        result.payload["can_apply"], bool
    ):
        raise AssertionError("catalog preflight CLI returned non-boolean readiness fields")
    if not isinstance(result.payload["backup"], Mapping):
        raise AssertionError("catalog preflight CLI returned an invalid backup report")
    return result.payload


def _require_verified_report(result: _CliResult) -> Mapping[str, Any]:
    """Validate a successful, read-only verify report."""

    if result.returncode != 0 or result.payload.get("success") is False or result.payload.get("valid") is not True:
        raise AssertionError(
            "catalog verification CLI failed with "
            f"{result.payload.get('error_code', 'an unexpected result')}"
        )
    return result.payload


def _require_error(result: _CliResult, error_code: str) -> None:
    assert result.returncode in {1, 2}
    assert result.payload.get("success") is False
    assert result.payload.get("error_code") == error_code


def _libpq_environment(database_url: URL) -> Dict[str, str]:
    parsed = make_url(database_url)
    environment = _RedactedEnvironment(os.environ)
    for key in (
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
        "PGSERVICE",
        "CATALOG_TEST_POSTGRES_URL",
        "CATALOG_TEST_POSTGRES_ADMIN_URL",
    ):
        environment.pop(key, None)
    environment["PGHOST"] = str(parsed.host or "")
    environment["PGPORT"] = str(int(parsed.port or 5432))
    environment["PGUSER"] = str(parsed.username or "")
    environment["PGDATABASE"] = str(parsed.database or "")
    if parsed.password is not None:
        environment["PGPASSWORD"] = str(parsed.password)
    return environment


def _safe_disposable_target(database: _DisposableDatabase) -> str:
    """Describe a generated target without rendering its credentialed URL."""

    _safe_identifier(database.name)
    parsed = database.url
    host = str(parsed.host or "")
    host_label = "loopback" if host.casefold() in {"127.0.0.1", "localhost", "::1"} else "masked"
    return f"host={host_label}, port={int(parsed.port or 5432)}, database={database.name}"


def _sanitize_tool_stderr(value: str, database: _DisposableDatabase) -> str:
    """Keep subprocess diagnostics useful without exposing credentials or URLs."""

    sanitized = str(value or "")
    password = str(database.url.password or "")
    if password:
        sanitized = sanitized.replace(password, "[REDACTED]")
    sanitized = re.sub(r"(?i)postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s]+", "[REDACTED_URL]", sanitized)
    sanitized = re.sub(
        r"(?i)\b(?:pgpassword|password)\s*(?:=|:)\s*(?:'[^']*'|\"[^\"]*\"|\S+)",
        "PASSWORD=[REDACTED]",
        sanitized,
    )
    normalized = " ".join(sanitized.split())
    return normalized[:2000] or "<empty>"


def _tool_version(tool: str) -> str:
    process = subprocess.run([tool, "--version"], capture_output=True, text=True, check=False)
    if process.returncode != 0:
        return "unavailable"
    return " ".join(process.stdout.split()) or "unavailable"


def _run_pg_dump(source: _DisposableDatabase, dump_path: Path) -> str:
    process = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dump_path),
        ],
        env=_libpq_environment(source.url),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0 or not dump_path.is_file() or dump_path.stat().st_size <= 0:
        returncode = process.returncode
        stderr = _sanitize_tool_stderr(process.stderr, source)
        del process
        raise AssertionError(
            "pg_dump did not create a usable disposable backup artifact "
            f"(returncode={returncode}, target={_safe_disposable_target(source)}, stderr={stderr})"
        )
    version = _tool_version("pg_dump")
    if version == "unavailable":
        raise AssertionError("pg_dump version metadata is unavailable")
    return version


def _run_pg_restore(restore: _DisposableDatabase, dump_path: Path) -> None:
    _safe_identifier(restore.name)
    if str(restore.url.database or "") != restore.name:
        raise AssertionError("restore URL did not target its generated disposable database")
    process = subprocess.run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            "--dbname",
            restore.name,
            str(dump_path),
        ],
        env=_libpq_environment(restore.url),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        returncode = process.returncode
        stderr = _sanitize_tool_stderr(process.stderr, restore)
        del process
        raise AssertionError(
            "pg_restore did not restore the disposable backup "
            f"(returncode={returncode}, target={_safe_disposable_target(restore)}, "
            f"tool={_tool_version('pg_restore')}, "
            f"stderr={stderr})"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _create_verified_backup_manifest(
    source: _DisposableDatabase,
    restore: _DisposableDatabase,
    preflight: Mapping[str, Any],
    tmp_path: Path,
    *,
    rich: bool,
) -> Path:
    """Dump, hash, restore, compare and only then create the runner manifest."""

    source_snapshot = asyncio.run(_legacy_snapshot(source.url, rich=rich))
    dump_path = tmp_path / f"{source.name}.dump"
    pg_dump_version = _run_pg_dump(source, dump_path)
    _run_pg_restore(restore, dump_path)
    restored_snapshot = asyncio.run(_legacy_snapshot(restore.url, rich=rich))
    assert restored_snapshot == source_snapshot

    target = preflight.get("target") or {}
    baseline = preflight.get("baseline") or {}
    target_fingerprint = str(target.get("fingerprint") or "")
    baseline_fingerprint = str(baseline.get("fingerprint") or "")
    assert target_fingerprint
    assert baseline_fingerprint

    manifest = {
        "manifest_version": BACKUP_MANIFEST_VERSION,
        "dialect": "postgresql",
        "target_fingerprint": target_fingerprint,
        "baseline_fingerprint": baseline_fingerprint,
        "artifact": dump_path.name,
        "sha256": _sha256(dump_path),
        "size_bytes": dump_path.stat().st_size,
        "dump_tool": "pg_dump",
        "backup_format": "custom",
        "pg_dump_version": pg_dump_version,
        "dump_completed_at": _utc_timestamp(),
        "restore_verified_at": _utc_timestamp(),
        "restore_verification_id": uuid4().hex,
    }
    assert manifest["sha256"] == _sha256(dump_path)
    assert int(manifest["size_bytes"]) == dump_path.stat().st_size
    assert manifest["restore_verification_id"]
    manifest_path = tmp_path / f"{source.name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest_path


async def _catalog_objects_absent(database_url: URL) -> bool:
    columns = await _fetchall(
        database_url,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='products'
        """,
    )
    tables = await _fetchall(
        database_url,
        "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()",
    )
    return (
        "catalog_product_id" not in {str(row[0]) for row in columns}
        and "schema_migrations" not in {str(row[0]) for row in tables}
        and "provider_products" not in {str(row[0]) for row in tables}
    )


async def _assert_run_items_are_immutable(database_url: URL) -> None:
    await _execute(
        database_url,
        """
        INSERT INTO catalog_sync_runs (
            run_id, provider_code, catalog_scope, operation, status, started_at
        ) VALUES ('run-immutable', 'test', 'prepaid', 'PREVIEW', 'READY', CURRENT_TIMESTAMP)
        """,
    )
    await _execute(
        database_url,
        """
        INSERT INTO catalog_sync_run_items (run_item_id, run_id, sequence_no)
        VALUES ('item-immutable', 'run-immutable', 1)
        """,
    )

    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "UPDATE catalog_sync_run_items SET sequence_no=2 "
                        "WHERE run_item_id='item-immutable'"
                    )
                )
            await connection.rollback()
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text("DELETE FROM catalog_sync_run_items WHERE run_item_id='item-immutable'")
                )
            await connection.rollback()
    finally:
        await engine.dispose()


def test_postgresql_preflight_without_manifest_is_read_only_and_not_apply_ready(
    disposable_databases: _DatabaseFactory,
) -> None:
    """Schema inspection succeeds without authorizing a PostgreSQL upgrade."""

    for label, rich in (("preflight_fresh", False), ("preflight_legacy", True)):
        source = disposable_databases.create(label)
        asyncio.run(_create_legacy_baseline(source.url, rich=rich))
        before = asyncio.run(_legacy_snapshot(source.url, rich=rich))

        preflight = _require_preflight_report(_run_runner(source.url, "preflight"))

        assert "error_code" not in preflight
        assert str(preflight["target"]["fingerprint"])
        assert preflight["can_upgrade"] is True
        assert preflight["can_apply"] is False
        assert preflight["backup"] == {
            "required": True,
            "valid": False,
            "reason": "backup_manifest_not_supplied",
        }
        assert preflight["backfill_precheck"] is None
        assert asyncio.run(_catalog_objects_absent(source.url))
        assert asyncio.run(_legacy_snapshot(source.url, rich=rich)) == before

        _require_error(
            _run_runner(
                source.url,
                "upgrade",
                "--target-fingerprint",
                str(preflight["target"]["fingerprint"]),
            ),
            "backup_required",
        )
        assert asyncio.run(_catalog_objects_absent(source.url))
        assert asyncio.run(_legacy_snapshot(source.url, rich=rich)) == before


def test_postgresql_fresh_schema_migration_cli_backup_restore_and_integrity(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("fresh")
    restore = disposable_databases.create("fresh_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=False))

    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    assert preflight["can_upgrade"] is True
    assert preflight["can_apply"] is False
    assert preflight["catalog_schema"]["state"] == "absent"
    assert preflight["backup"]["valid"] is False

    manifest_path = _create_verified_backup_manifest(
        source,
        restore,
        preflight,
        tmp_path,
        rich=False,
    )
    result = _require_success(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
            "--postgres-backup-manifest",
            str(manifest_path),
        )
    )
    assert result["migration_id"] == CATALOG_SCHEMA_MIGRATION_ID
    assert result["checksum"] == CATALOG_SCHEMA_MIGRATION_CHECKSUM
    assert result["already_applied"] is False
    assert result["backfill"]["catalog_product_ids_created"] == 3
    assert result["backup"]["backup_format"] == "custom"

    verification = _require_verified_report(
        _run_runner(
            source.url,
            "verify",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
        )
    )
    assert verification["valid"] is True
    assert verification["marker"]["state"] == "matching"

    products = asyncio.run(
        _fetchall(
            source.url,
            """
            SELECT sku, catalog_product_id, logical_key, logical_key_version, active, store_enabled
            FROM products ORDER BY sku
            """,
        )
    )
    assert len(products) == 3
    assert {str(row[0]) for row in products} == {product[0] for product in BOOTSTRAP_PRODUCTS}
    assert len({str(row[1]) for row in products}) == 3
    for sku, catalog_product_id, logical_key, logical_key_version, active, store_enabled in products:
        assert catalog_product_id
        assert logical_key == f"legacy-sku:{str(sku).casefold()}"
        assert logical_key_version == "legacy-sku-v1"
        assert int(store_enabled) == int(active)
    assert asyncio.run(_fetchall(source.url, "SELECT COUNT(*) FROM provider_products")) == [(0,)]
    marker = asyncio.run(
        _fetchall(
            source.url,
            "SELECT checksum FROM schema_migrations WHERE migration_id=:migration_id",
            {"migration_id": CATALOG_SCHEMA_MIGRATION_ID},
        )
    )
    assert marker == [(CATALOG_SCHEMA_MIGRATION_CHECKSUM,)]
    asyncio.run(_assert_run_items_are_immutable(source.url))


def test_postgresql_legacy_preservation_backfill_marker_and_idempotency(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("legacy")
    restore = disposable_databases.create("legacy_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=True))
    before = asyncio.run(_legacy_snapshot(source.url, rich=True))
    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    manifest_path = _create_verified_backup_manifest(source, restore, preflight, tmp_path, rich=True)

    first = _require_success(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
            "--postgres-backup-manifest",
            str(manifest_path),
        )
    )
    assert first["success"] is True
    assert first["backfill"]["products_seen"] == 4
    assert asyncio.run(_legacy_snapshot(source.url, rich=True)) == before

    initial_ids = asyncio.run(
        _fetchall(source.url, "SELECT sku, catalog_product_id, store_enabled FROM products ORDER BY sku")
    )
    assert len(initial_ids) == 4
    assert all(row[1] for row in initial_ids)
    assert len({str(row[1]) for row in initial_ids}) == 4
    assert next(row for row in initial_ids if row[0] == "legacy-special")[2] == 0
    assert asyncio.run(_fetchall(source.url, "SELECT COUNT(*) FROM provider_products")) == [(0,)]

    repeated = _require_success(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
        )
    )
    assert repeated["already_applied"] is True
    assert (
        asyncio.run(_fetchall(source.url, "SELECT sku, catalog_product_id, store_enabled FROM products ORDER BY sku"))
        == initial_ids
    )
    verification = _require_verified_report(
        _run_runner(
            source.url,
            "verify",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
        )
    )
    assert verification["valid"] is True
    assert verification["marker"]["state"] == "matching"


def test_postgresql_missing_invalid_backup_and_wrong_fingerprint_fail_closed(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("fail_closed")
    restore = disposable_databases.create("fail_closed_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=False))
    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    fingerprint = str(preflight["target"]["fingerprint"])

    _require_error(_run_runner(source.url, "upgrade", "--target-fingerprint", fingerprint), "backup_required")
    assert asyncio.run(_catalog_objects_absent(source.url))

    invalid_manifest = tmp_path / "invalid-manifest.json"
    invalid_manifest.write_text('{"manifest_version": "invalid"}', encoding="utf-8")
    _require_error(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            fingerprint,
            "--postgres-backup-manifest",
            str(invalid_manifest),
        ),
        "backup_verification_failed",
    )
    assert asyncio.run(_catalog_objects_absent(source.url))

    manifest_path = _create_verified_backup_manifest(source, restore, preflight, tmp_path, rich=False)
    tampered_manifest = tmp_path / "tampered-target-fingerprint.json"
    tampered_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered_payload["target_fingerprint"] = "tampered-target-fingerprint"
    tampered_manifest.write_text(json.dumps(tampered_payload), encoding="utf-8")
    _require_error(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            fingerprint,
            "--postgres-backup-manifest",
            str(tampered_manifest),
        ),
        "backup_verification_failed",
    )
    assert asyncio.run(_catalog_objects_absent(source.url))

    _require_error(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            "not-a-preflight-fingerprint",
            "--postgres-backup-manifest",
            str(manifest_path),
        ),
        "database_target_invalid",
    )
    assert asyncio.run(_catalog_objects_absent(source.url))


def test_postgresql_advisory_lock_contention_is_rejected_before_ddl(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("advisory_lock")
    restore = disposable_databases.create("advisory_lock_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=False))
    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    manifest_path = _create_verified_backup_manifest(source, restore, preflight, tmp_path, rich=False)

    async def scenario() -> _CliResult:
        engine = _engine(source.url)
        try:
            connection = await engine.connect()
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _MIGRATION_ADVISORY_LOCK_KEY},
                )
                return await asyncio.to_thread(
                    _run_runner,
                    source.url,
                    "upgrade",
                    "--target-fingerprint",
                    str(preflight["target"]["fingerprint"]),
                    "--postgres-backup-manifest",
                    str(manifest_path),
                )
            finally:
                await transaction.rollback()
                await connection.close()
        finally:
            await engine.dispose()

    _require_error(asyncio.run(scenario()), "migration_lock_unavailable")
    assert asyncio.run(_catalog_objects_absent(source.url))


def test_postgresql_interrupted_migration_rolls_back_and_can_retry(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("interrupted")
    restore = disposable_databases.create("interrupted_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=True))
    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    manifest_path = _create_verified_backup_manifest(source, restore, preflight, tmp_path, rich=True)

    asyncio.run(
        _execute(
            source.url,
            """
            CREATE OR REPLACE FUNCTION catalog_test_backfill_interrupt()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'intentional catalog test interruption';
            END;
            $$
            """,
        )
    )
    asyncio.run(
        _execute(
            source.url,
            """
            CREATE TRIGGER catalog_test_backfill_interrupt
            BEFORE UPDATE ON products
            FOR EACH ROW EXECUTE FUNCTION catalog_test_backfill_interrupt()
            """,
        )
    )
    interrupted = _run_runner(
        source.url,
        "upgrade",
        "--target-fingerprint",
        str(preflight["target"]["fingerprint"]),
        "--postgres-backup-manifest",
        str(manifest_path),
    )
    _require_error(interrupted, "unexpected_migration_error")
    assert asyncio.run(_catalog_objects_absent(source.url))

    asyncio.run(_execute(source.url, "DROP TRIGGER catalog_test_backfill_interrupt ON products"))
    asyncio.run(_execute(source.url, "DROP FUNCTION catalog_test_backfill_interrupt()"))
    retried = _require_success(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
            "--postgres-backup-manifest",
            str(manifest_path),
        )
    )
    assert retried["success"] is True
    assert _require_verified_report(
        _run_runner(
            source.url,
            "verify",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
        )
    )["valid"] is True


def test_postgresql_verify_detects_post_migration_schema_integrity_failure(
    disposable_databases: _DatabaseFactory,
    tmp_path: Path,
) -> None:
    source = disposable_databases.create("integrity")
    restore = disposable_databases.create("integrity_restore")
    asyncio.run(_create_legacy_baseline(source.url, rich=False))
    preflight = _require_preflight_report(_run_runner(source.url, "preflight"))
    manifest_path = _create_verified_backup_manifest(source, restore, preflight, tmp_path, rich=False)
    _require_success(
        _run_runner(
            source.url,
            "upgrade",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
            "--postgres-backup-manifest",
            str(manifest_path),
        )
    )
    asyncio.run(_execute(source.url, "DROP INDEX uq_products_catalog_product_id"))
    _require_error(
        _run_runner(
            source.url,
            "verify",
            "--target-fingerprint",
            str(preflight["target"]["fingerprint"]),
        ),
        "migration_verification_failed",
    )
