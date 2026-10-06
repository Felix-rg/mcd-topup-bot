"""Disposable SQLite coverage for the explicit Phase 1 catalog migration."""

from __future__ import annotations

import asyncio
import gc
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import text

from catalog_schema import (
    CATALOG_SCHEMA_MIGRATION_CHECKSUM,
    CATALOG_SCHEMA_MIGRATION_ID,
    BackupRequiredError,
    BackupVerificationError,
    DatabaseTargetError,
    MigrationChecksumMismatch,
    MigrationLockError,
    PreflightError,
    preflight_catalog_schema,
    upgrade_catalog_schema,
    verify_catalog_schema,
)


BOOTSTRAP_PRODUCTS = (
    ("ml_10k", "Mobile Legends", "Diamonds 10K", 8500, 10000, 1),
    ("ff_12k", "Free Fire", "Diamonds 12K", 10000, 12000, 1),
    ("pubg_20k", "PUBG Mobile", "UC 20K", 17000, 20000, 1),
)


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


@contextmanager
def _sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Commit/rollback and close explicitly; sqlite's context manager does not close."""

    connection = sqlite3.connect(str(path))
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def _create_baseline(path: Path, *, rich: bool = False) -> None:
    """Build an app-shaped legacy database without using init_db()."""

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

    with _sqlite_connection(path) as connection:
        connection.executescript(
            f"""
            {products_ddl};
            CREATE TABLE topup (
                id TEXT PRIMARY KEY,
                nominal TEXT,
                price NUMERIC,
                product_cost NUMERIC
            );
            CREATE TABLE promos (
                id INTEGER PRIMARY KEY,
                code TEXT,
                target_scope TEXT,
                target_value TEXT
            );
            CREATE TABLE promotion_target_catalog (
                id INTEGER PRIMARY KEY,
                promo_id INTEGER,
                target_type TEXT,
                target_key TEXT,
                active INTEGER
            );
            CREATE TABLE provider_attempts (
                id INTEGER PRIMARY KEY,
                order_id TEXT,
                provider TEXT,
                request_state TEXT
            );
            """
        )
        if rich:
            connection.executemany(
                """
                INSERT INTO products (
                    sku, provider, name, cost_price, price, active, category,
                    product_type, brand, description, image_url, logo_url,
                    promo_title, promo_text, promo_badge, promo_url, display_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepaid', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        *product,
                        product[1],
                        product[1],
                        f"Description {product[0]}",
                        "image",
                        "logo",
                        "promo",
                        "text",
                        "badge",
                        "url",
                        1,
                    )
                    for product in BOOTSTRAP_PRODUCTS
                ]
                + [
                    (
                        "legacy-special",
                        "Legacy Provider",
                        "Legacy Product",
                        12345,
                        15000,
                        0,
                        "Legacy Category",
                        "Legacy Brand",
                        "Legacy display description",
                        "legacy-image",
                        "legacy-logo",
                        "Legacy Promo",
                        "Legacy promo text",
                        "Legacy badge",
                        "https://example.invalid/promo",
                        77,
                    )
                ],
            )
        else:
            connection.executemany(
                "INSERT INTO products (sku, name, cost_price, price, active) VALUES (?, ?, ?, ?, ?)",
                [(sku, name, cost, price, active) for sku, _provider, name, cost, price, active in BOOTSTRAP_PRODUCTS],
            )

        connection.execute(
            "INSERT INTO topup (id, nominal, price, product_cost) VALUES ('history-1', 'legacy-special', 15000, 12345)"
        )
        connection.execute(
            "INSERT INTO promos (id, code, target_scope, target_value) VALUES (1, 'LEGACY', 'sku', 'legacy-special')"
        )
        connection.execute(
            """
            INSERT INTO promotion_target_catalog (id, promo_id, target_type, target_key, active)
            VALUES (1, 1, 'sku', 'legacy-special', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO provider_attempts (id, order_id, provider, request_state)
            VALUES (1, 'history-1', 'digiflazz', 'SENT_UNKNOWN')
            """
        )


def _rows(path: Path, query: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    with _sqlite_connection(path) as connection:
        return connection.execute(query, params).fetchall()


def _legacy_snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "products": _rows(
            path,
            """
            SELECT sku, provider, name, cost_price, price, active, category,
                   product_type, brand, description, image_url, logo_url,
                   promo_title, promo_text, promo_badge, promo_url, display_order
            FROM products ORDER BY sku
            """,
        ),
        "topup": _rows(path, "SELECT id, nominal, price, product_cost FROM topup ORDER BY id"),
        "promos": _rows(path, "SELECT id, code, target_scope, target_value FROM promos ORDER BY id"),
        "promotion_targets": _rows(
            path,
            "SELECT id, promo_id, target_type, target_key, active FROM promotion_target_catalog ORDER BY id",
        ),
        "provider_attempts": _rows(
            path,
            "SELECT id, order_id, provider, request_state FROM provider_attempts ORDER BY id",
        ),
    }


class CatalogSchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="lixafa-catalog-phase1-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _new_database(self, name: str, *, rich: bool = False) -> tuple[Path, str]:
        path = self.root / name
        _create_baseline(path, rich=rich)
        return path, _sqlite_url(path)

    def _preflight(self, url: str) -> dict[str, Any]:
        return asyncio.run(preflight_catalog_schema(url))

    def _upgrade(
        self,
        url: str,
        target_fingerprint: str,
        backup_path: Optional[Path],
    ) -> dict[str, Any]:
        return asyncio.run(
            upgrade_catalog_schema(
                url,
                target_fingerprint=target_fingerprint,
                sqlite_backup_path=backup_path,
                sqlite_engine_stopped=True,
            )
        )

    def test_nonexistent_sqlite_target_is_rejected_without_creating_a_database(self) -> None:
        missing_path = self.root / "missing-target.db"
        missing_url = _sqlite_url(missing_path)
        with self.assertRaises(DatabaseTargetError):
            self._preflight(missing_url)
        self.assertFalse(missing_path.exists())

    def test_fresh_sqlite_adds_complete_schema_and_safe_legacy_backfill(self) -> None:
        path, url = self._new_database("fresh.db")
        preflight = self._preflight(url)
        self.assertTrue(preflight["can_upgrade"])
        self.assertFalse(preflight["can_apply"])
        self.assertEqual(preflight["catalog_schema"]["state"], "absent")
        self.assertFalse(preflight["backup"]["valid"])
        self.assertEqual(preflight["backup"]["reason"], "backup_path_not_supplied")
        self.assertTrue(str(preflight["target"]["fingerprint"]))

        result = self._upgrade(
            url,
            preflight["target"]["fingerprint"],
            self.root / "fresh.backup.db",
        )
        self.assertTrue(result["success"])
        self.assertFalse(result["already_applied"])
        self.assertEqual(result["migration_id"], CATALOG_SCHEMA_MIGRATION_ID)
        self.assertEqual(result["checksum"], CATALOG_SCHEMA_MIGRATION_CHECKSUM)
        self.assertEqual(result["backfill"]["catalog_product_ids_created"], 3)

        verify = asyncio.run(
            verify_catalog_schema(url, target_fingerprint=preflight["target"]["fingerprint"])
        )
        self.assertTrue(verify["valid"])
        self.assertEqual(verify["marker"]["state"], "matching")

        columns = {row[1] for row in _rows(path, "PRAGMA table_info(products)")}
        self.assertTrue(
            {
                "catalog_product_id",
                "logical_key",
                "logical_key_version",
                "denomination_text",
                "denomination_value",
                "denomination_unit",
                "product_semantics",
                "store_enabled",
            }.issubset(columns)
        )
        column_metadata = {row[1]: row for row in _rows(path, "PRAGMA table_info(products)")}
        for column_name in (
            "catalog_product_id",
            "logical_key",
            "logical_key_version",
            "denomination_text",
            "denomination_value",
            "denomination_unit",
            "product_semantics",
            "store_enabled",
        ):
            self.assertEqual(int(column_metadata[column_name][3]), 0)
            self.assertIsNone(column_metadata[column_name][4])
        tables = {row[0] for row in _rows(path, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(
            {
                "provider_products",
                "catalog_sync_runs",
                "catalog_sync_run_items",
                "catalog_mapping_reviews",
                "catalog_provider_sync_state",
                "catalog_sync_locks",
                "schema_migrations",
            }.issubset(tables)
        )
        products = _rows(
            path,
            """
            SELECT sku, catalog_product_id, logical_key, logical_key_version,
                   denomination_text, denomination_value, denomination_unit,
                   product_semantics, active, store_enabled
            FROM products ORDER BY sku
            """,
        )
        self.assertEqual(len(products), 3)
        self.assertTrue(all(row[1] for row in products))
        self.assertEqual(len({row[1] for row in products}), 3)
        for sku, _catalog_id, logical_key, logical_version, denomination_text, denomination_value, denomination_unit, semantics, active, store_enabled in products:
            self.assertEqual(logical_key, f"legacy-sku:{sku.casefold()}")
            self.assertEqual(logical_version, "legacy-sku-v1")
            self.assertIsNone(denomination_text)
            self.assertIsNone(denomination_value)
            self.assertIsNone(denomination_unit)
            self.assertIsNone(semantics)
            self.assertEqual(store_enabled, active)
        self.assertEqual(_rows(path, "SELECT COUNT(*) FROM provider_products"), [(0,)])
        provider_columns = {row[1] for row in _rows(path, "PRAGMA table_info(provider_products)")}
        self.assertTrue(
            {
                "provider_product_id",
                "provider_code",
                "catalog_scope",
                "provider_sku",
                "seller_identity",
                "source_identity_fingerprint",
                "catalog_product_id",
                "mapping_state",
                "raw_attributes_json",
                "cost_price",
                "buyer_product_status",
                "seller_product_status",
                "stock",
                "unlimited_stock",
                "availability_state",
                "first_seen_run_id",
                "last_seen_run_id",
            }.issubset(provider_columns)
        )

        index_rows = _rows(path, "PRAGMA index_list(products)")
        unique_indexes = {row[1] for row in index_rows if row[2]}
        self.assertIn("uq_products_catalog_product_id", unique_indexes)
        for index_name in unique_indexes:
            index_columns = {row[2] for row in _rows(path, f"PRAGMA index_info({index_name})")}
            self.assertNotIn("logical_key", index_columns)

    def test_legacy_fields_orders_promotions_and_provider_attempts_are_preserved(self) -> None:
        path, url = self._new_database("legacy.db", rich=True)
        before = _legacy_snapshot(path)
        preflight = self._preflight(url)
        result = self._upgrade(url, preflight["target"]["fingerprint"], self.root / "legacy.backup.db")
        self.assertTrue(result["success"])
        self.assertEqual(_legacy_snapshot(path), before)
        self.assertEqual(
            _rows(path, "SELECT sku FROM products WHERE sku IN ('ml_10k', 'ff_12k', 'pubg_20k') ORDER BY sku"),
            [("ff_12k",), ("ml_10k",), ("pubg_20k",)],
        )
        self.assertEqual(
            _rows(path, "SELECT store_enabled FROM products WHERE sku='legacy-special'"),
            [(0,)],
        )

    def test_idempotent_rerun_and_interrupted_backfill_recovery_keep_existing_ids(self) -> None:
        path, url = self._new_database("recovery.db", rich=True)
        preflight = self._preflight(url)
        backup_path = self.root / "recovery.backup.db"
        self._upgrade(url, preflight["target"]["fingerprint"], backup_path)
        initial_ids = dict(_rows(path, "SELECT sku, catalog_product_id FROM products"))

        second = self._upgrade(url, preflight["target"]["fingerprint"], None)
        self.assertTrue(second["already_applied"])
        self.assertEqual(dict(_rows(path, "SELECT sku, catalog_product_id FROM products")), initial_ids)

        with _sqlite_connection(path) as connection:
            connection.execute(
                "DELETE FROM schema_migrations WHERE migration_id=?",
                (CATALOG_SCHEMA_MIGRATION_ID,),
            )
            connection.execute(
                """
                UPDATE products
                SET catalog_product_id=NULL, logical_key=NULL,
                    logical_key_version=NULL, store_enabled=NULL
                WHERE sku='legacy-special'
                """
            )

        resumed_preflight = self._preflight(url)
        self.assertTrue(resumed_preflight["can_upgrade"])
        self.assertEqual(resumed_preflight["catalog_schema"]["state"], "complete")
        resumed = self._upgrade(
            url,
            resumed_preflight["target"]["fingerprint"],
            backup_path,
        )
        self.assertFalse(resumed["already_applied"])
        recovered_ids = dict(_rows(path, "SELECT sku, catalog_product_id FROM products"))
        for sku, catalog_id in initial_ids.items():
            if sku != "legacy-special":
                self.assertEqual(recovered_ids[sku], catalog_id)
        self.assertTrue(recovered_ids["legacy-special"])
        self.assertEqual(
            _rows(
                path,
                "SELECT logical_key, logical_key_version, store_enabled FROM products WHERE sku='legacy-special'",
            ),
            [("legacy-sku:legacy-special", "legacy-sku-v1", 0)],
        )

    def test_missing_backup_fails_before_any_catalog_ddl_or_marker(self) -> None:
        path, url = self._new_database("missing-backup.db")
        preflight = self._preflight(url)
        with self.assertRaises(BackupRequiredError):
            self._upgrade(url, preflight["target"]["fingerprint"], None)
        columns = {row[1] for row in _rows(path, "PRAGMA table_info(products)")}
        self.assertNotIn("catalog_product_id", columns)
        tables = {row[0] for row in _rows(path, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("schema_migrations", tables)
        self.assertNotIn("provider_products", tables)

    def test_unverified_existing_backup_fails_closed_before_catalog_ddl(self) -> None:
        path, url = self._new_database("invalid-backup.db")
        preflight = self._preflight(url)
        invalid_backup = self.root / "invalid.backup.db"
        invalid_backup.write_bytes(b"not a sqlite backup")
        with self.assertRaises(BackupVerificationError):
            self._upgrade(url, preflight["target"]["fingerprint"], invalid_backup)
        self.assertNotIn(
            "catalog_product_id",
            {row[1] for row in _rows(path, "PRAGMA table_info(products)")},
        )

    def test_checksum_mismatch_fails_closed_without_repairing_marker(self) -> None:
        path, url = self._new_database("checksum.db")
        preflight = self._preflight(url)
        self._upgrade(url, preflight["target"]["fingerprint"], self.root / "checksum.backup.db")
        with _sqlite_connection(path) as connection:
            connection.execute(
                "UPDATE schema_migrations SET checksum='unexpected-checksum' WHERE migration_id=?",
                (CATALOG_SCHEMA_MIGRATION_ID,),
            )
        with self.assertRaises(MigrationChecksumMismatch):
            self._upgrade(url, preflight["target"]["fingerprint"], None)
        self.assertEqual(
            _rows(
                path,
                "SELECT checksum FROM schema_migrations WHERE migration_id=?",
                (CATALOG_SCHEMA_MIGRATION_ID,),
            ),
            [("unexpected-checksum",)],
        )

    def test_partial_schema_is_rejected_instead_of_broad_repair(self) -> None:
        path, url = self._new_database("partial.db")
        with _sqlite_connection(path) as connection:
            connection.execute("ALTER TABLE products ADD COLUMN catalog_product_id TEXT")
        preflight = self._preflight(url)
        self.assertFalse(preflight["can_upgrade"])
        self.assertIn("catalog_product_columns_partial", preflight["issues"])
        with self.assertRaises(PreflightError):
            self._upgrade(url, preflight["target"]["fingerprint"], self.root / "partial.backup.db")
        self.assertNotIn(
            "provider_products",
            {row[0] for row in _rows(path, "SELECT name FROM sqlite_master WHERE type='table'")},
        )
    def test_concurrent_sqlite_migration_is_rejected_by_immediate_lock(self) -> None:
        path, url = self._new_database("locked.db")
        preflight = self._preflight(url)
        # A direct sqlite3 holder models another process without leaving an
        # aiosqlite worker thread attached to the Windows temporary file.
        connection = sqlite3.connect(str(path), timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(MigrationLockError):
                self._upgrade(
                    url,
                    preflight["target"]["fingerprint"],
                    self.root / "locked.backup.db",
                )
        finally:
            connection.rollback()
            connection.close()
        self.assertNotIn(
            "provider_products",
            {row[0] for row in _rows(path, "SELECT name FROM sqlite_master WHERE type='table'")},
        )
        # The rejected aiosqlite lock path owns a short-lived DBAPI error cycle
        # on Windows.  Collect it before TemporaryDirectory unlinks its file;
        # the assertion above still verifies the actual migration behavior.
        gc.collect()

    def test_duplicate_candidates_and_many_candidates_for_one_logical_product_are_allowed(self) -> None:
        path, url = self._new_database("candidates.db")
        preflight = self._preflight(url)
        self._upgrade(url, preflight["target"]["fingerprint"], self.root / "candidates.backup.db")
        catalog_product_id = _rows(
            path,
            "SELECT catalog_product_id FROM products WHERE sku='ml_10k'",
        )[0][0]
        with _sqlite_connection(path) as connection:
            for provider_product_id in ("candidate-1", "candidate-2"):
                connection.execute(
                    """
                    INSERT INTO provider_products (
                        provider_product_id, provider_code, catalog_scope,
                        provider_sku, seller_identity,
                        source_identity_fingerprint, source_identity_fingerprint_version,
                        catalog_product_id, mapping_state
                    ) VALUES (?, 'digiflazz', 'prepaid', 'same-sku', 'same-seller',
                              'same-fingerprint', 'v1', ?, 'MAPPED')
                    """,
                    (provider_product_id, catalog_product_id),
                )
        self.assertEqual(
            _rows(
                path,
                "SELECT COUNT(*) FROM provider_products WHERE catalog_product_id=?",
                (catalog_product_id,),
            ),
            [(2,)],
        )

    def test_migration_never_makes_an_http_provider_or_catalog_request(self) -> None:
        path, url = self._new_database("no-provider.db")
        preflight = self._preflight(url)

        async def scenario() -> None:
            with patch.object(
                __import__("httpx").AsyncClient,
                "post",
                new=AsyncMock(side_effect=AssertionError("provider HTTP must never be called")),
            ) as provider_post:
                await upgrade_catalog_schema(
                    url,
                    target_fingerprint=preflight["target"]["fingerprint"],
                    sqlite_backup_path=self.root / "no-provider.backup.db",
                    sqlite_engine_stopped=True,
                )
            provider_post.assert_not_awaited()

        asyncio.run(scenario())

    def test_dedicated_cli_runs_preflight_upgrade_and_verify_without_printing_url(self) -> None:
        path, url = self._new_database("cli.db")
        script = Path(__file__).resolve().parents[1] / "scripts" / "migrate_catalog_sync.py"
        preflight_process = subprocess.run(
            [sys.executable, str(script), "preflight", "--database-url", url],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(preflight_process.returncode, 0, preflight_process.stderr)
        self.assertNotIn(url, preflight_process.stdout)
        preflight = json.loads(preflight_process.stdout)
        target_fingerprint = preflight["target"]["fingerprint"]
        backup_path = self.root / "cli.backup.db"

        upgrade_process = subprocess.run(
            [
                sys.executable,
                str(script),
                "upgrade",
                "--database-url",
                url,
                "--target-fingerprint",
                target_fingerprint,
                "--sqlite-backup-path",
                str(backup_path),
                "--sqlite-engine-stopped",
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(upgrade_process.returncode, 0, upgrade_process.stderr)
        self.assertNotIn(url, upgrade_process.stdout)
        self.assertTrue(json.loads(upgrade_process.stdout)["success"])

        verify_process = subprocess.run(
            [
                sys.executable,
                str(script),
                "verify",
                "--database-url",
                url,
                "--target-fingerprint",
                target_fingerprint,
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(verify_process.returncode, 0, verify_process.stderr)
        self.assertNotIn(url, verify_process.stdout)
        self.assertTrue(json.loads(verify_process.stdout)["valid"])

    def test_upgrade_rejects_wrong_target_fingerprint_and_missing_engine_stop_acknowledgement(self) -> None:
        _path, url = self._new_database("fingerprint.db")
        preflight = self._preflight(url)
        with self.assertRaises(DatabaseTargetError):
            self._upgrade(url, "not-the-preflight-fingerprint", self.root / "fingerprint.backup.db")
        with self.assertRaises(MigrationLockError):
            asyncio.run(
                upgrade_catalog_schema(
                    url,
                    target_fingerprint=preflight["target"]["fingerprint"],
                    sqlite_backup_path=self.root / "fingerprint.backup.db",
                    sqlite_engine_stopped=False,
                )
            )

    def test_preflight_rejects_missing_bootstrap_and_casefolded_sku_collision(self) -> None:
        missing_bootstrap_path, missing_bootstrap_url = self._new_database("missing-bootstrap.db")
        with _sqlite_connection(missing_bootstrap_path) as connection:
            connection.execute("DELETE FROM products WHERE sku='ml_10k'")
        missing_bootstrap = self._preflight(missing_bootstrap_url)
        self.assertFalse(missing_bootstrap["can_upgrade"])
        self.assertIn("bootstrap_products_missing", missing_bootstrap["issues"])

        collision_path, collision_url = self._new_database("casefold-collision.db")
        with _sqlite_connection(collision_path) as connection:
            connection.execute(
                "INSERT INTO products (sku, name, cost_price, price, active) VALUES ('ML_10K', 'Duplicate', 1, 1, 1)"
            )
        collision = self._preflight(collision_url)
        self.assertFalse(collision["can_upgrade"])
        self.assertIn("sku_collision_detected", collision["issues"])

    def test_preflight_rejects_incompatible_marker_and_catalog_column_shape(self) -> None:
        marker_path, marker_url = self._new_database("bad-marker.db")
        with _sqlite_connection(marker_path) as connection:
            connection.execute(
                "CREATE TABLE schema_migrations (migration_id TEXT, checksum TEXT, applied_at TEXT)"
            )
        marker_preflight = self._preflight(marker_url)
        self.assertFalse(marker_preflight["can_upgrade"])
        self.assertIn("schema_migrations_incompatible", marker_preflight["issues"])

        shape_path, shape_url = self._new_database("bad-product-shape.db")
        with _sqlite_connection(shape_path) as connection:
            connection.execute(
                "ALTER TABLE products ADD COLUMN catalog_product_id BLOB NOT NULL DEFAULT X'00'"
            )
            connection.execute("ALTER TABLE products ADD COLUMN logical_key TEXT")
            connection.execute("ALTER TABLE products ADD COLUMN logical_key_version TEXT")
            connection.execute("ALTER TABLE products ADD COLUMN denomination_text TEXT")
            connection.execute("ALTER TABLE products ADD COLUMN denomination_value NUMERIC")
            connection.execute("ALTER TABLE products ADD COLUMN denomination_unit TEXT")
            connection.execute("ALTER TABLE products ADD COLUMN product_semantics TEXT")
            connection.execute("ALTER TABLE products ADD COLUMN store_enabled INTEGER")
        shape_preflight = self._preflight(shape_url)
        self.assertFalse(shape_preflight["can_upgrade"])
        self.assertIn(
            "catalog_column_type_incompatible:products.catalog_product_id",
            shape_preflight["issues"],
        )
        self.assertIn(
            "catalog_column_nullability_incompatible:products.catalog_product_id",
            shape_preflight["issues"],
        )

    def test_preflight_rejects_wrong_named_index_and_malformed_support_table(self) -> None:
        index_path, index_url = self._new_database("bad-index.db")
        index_preflight = self._preflight(index_url)
        self._upgrade(index_url, index_preflight["target"]["fingerprint"], self.root / "bad-index.backup.db")
        with _sqlite_connection(index_path) as connection:
            connection.execute("DROP INDEX uq_products_catalog_product_id")
            connection.execute("CREATE UNIQUE INDEX uq_products_catalog_product_id ON products (logical_key)")
        index_preflight = self._preflight(index_url)
        self.assertFalse(index_preflight["can_upgrade"])
        self.assertIn("catalog_index_incompatible:uq_products_catalog_product_id", index_preflight["issues"])

        table_path, table_url = self._new_database("bad-table.db")
        table_preflight = self._preflight(table_url)
        self._upgrade(table_url, table_preflight["target"]["fingerprint"], self.root / "bad-table.backup.db")
        with _sqlite_connection(table_path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE migration_id=?", (CATALOG_SCHEMA_MIGRATION_ID,))
            connection.execute("DROP TABLE catalog_sync_run_items")
            connection.execute(
                "CREATE TABLE catalog_sync_run_items AS "
                "SELECT * FROM catalog_mapping_reviews WHERE 0"
            )
        table_preflight = self._preflight(table_url)
        self.assertFalse(table_preflight["can_upgrade"])
        self.assertIn("catalog_table_incompatible:catalog_sync_run_items", table_preflight["issues"])

    def test_run_items_are_nonnull_keyed_and_append_only(self) -> None:
        path, url = self._new_database("immutable-run-items.db")
        preflight = self._preflight(url)
        self._upgrade(url, preflight["target"]["fingerprint"], self.root / "immutable-run-items.backup.db")
        metadata = {row[1]: row for row in _rows(path, "PRAGMA table_info(catalog_sync_run_items)")}
        self.assertEqual(int(metadata["run_item_id"][3]), 1)
        with _sqlite_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO catalog_sync_runs (
                    run_id, provider_code, catalog_scope, operation, status, started_at
                ) VALUES ('run-immutable', 'test', 'prepaid', 'PREVIEW', 'READY', CURRENT_TIMESTAMP)
                """
            )
            connection.execute(
                "INSERT INTO catalog_sync_run_items (run_item_id, run_id, sequence_no) "
                "VALUES ('item-immutable', 'run-immutable', 1)"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE catalog_sync_run_items SET sequence_no=2 WHERE run_item_id='item-immutable'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM catalog_sync_run_items WHERE run_item_id='item-immutable'")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO catalog_sync_runs (
                        run_id, provider_code, catalog_scope, operation, status, started_at
                    ) VALUES (NULL, 'test', 'prepaid', 'PREVIEW', 'READY', CURRENT_TIMESTAMP)
                    """
                )

    def test_invalid_resumable_backfill_values_fail_closed(self) -> None:
        path, url = self._new_database("invalid-backfill.db")
        preflight = self._preflight(url)
        backup_path = self.root / "invalid-backfill.backup.db"
        self._upgrade(url, preflight["target"]["fingerprint"], backup_path)
        with _sqlite_connection(path) as connection:
            connection.execute("DELETE FROM schema_migrations WHERE migration_id=?", (CATALOG_SCHEMA_MIGRATION_ID,))
            connection.execute("UPDATE products SET catalog_product_id='' WHERE sku='ml_10k'")
        resumed_preflight = self._preflight(url)
        self.assertFalse(resumed_preflight["can_upgrade"])
        self.assertEqual(resumed_preflight["backfill_precheck"]["valid"], False)
        self.assertIn("catalog_backfill_values_incompatible", resumed_preflight["issues"])
        with self.assertRaises(PreflightError):
            self._upgrade(url, resumed_preflight["target"]["fingerprint"], backup_path)

    def test_transaction_rollback_from_actual_backfill_interrupt_can_be_retried(self) -> None:
        path, url = self._new_database("actual-interrupt.db")
        preflight = self._preflight(url)
        backup_path = self.root / "actual-interrupt.backup.db"
        with patch("catalog_schema.uuid4", side_effect=[uuid4(), RuntimeError("simulated interruption")]):
            with self.assertRaises(RuntimeError):
                self._upgrade(url, preflight["target"]["fingerprint"], backup_path)
        self.assertNotIn(
            "catalog_product_id",
            {row[1] for row in _rows(path, "PRAGMA table_info(products)")},
        )
        self.assertTrue(backup_path.exists())
        retry = self._upgrade(url, preflight["target"]["fingerprint"], backup_path)
        self.assertTrue(retry["success"])

