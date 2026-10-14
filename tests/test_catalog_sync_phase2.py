"""Disposable regression coverage for the Phase 2 catalog lifecycle.

This suite deliberately prepares a tiny app-shaped SQLite database and applies
the already-reviewed Phase 1 schema only to that temporary file.  It never
reads application/ Railway credentials and every catalog response is fake.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from catalog_schema import preflight_catalog_schema, upgrade_catalog_schema


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


def _create_baseline(path: Path) -> None:
    """Build only the legacy shape Phase 1 expects; never call app init_db."""

    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            """
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
            );
            CREATE TABLE topup (id TEXT PRIMARY KEY, nominal TEXT, price NUMERIC, product_cost NUMERIC);
            CREATE TABLE promos (id INTEGER PRIMARY KEY, code TEXT, target_scope TEXT, target_value TEXT);
            CREATE TABLE promotion_target_catalog (
                id INTEGER PRIMARY KEY, promo_id INTEGER, target_type TEXT, target_key TEXT, active INTEGER
            );
            CREATE TABLE provider_attempts (id INTEGER PRIMARY KEY, order_id TEXT, provider TEXT, request_state TEXT);
            """
        )
        products = (
            ("ml_10k", "Mobile Legends", "Diamonds 10K", 8500, 10000, 1),
            ("ff_12k", "Free Fire", "Diamonds 12K", 10000, 12000, 1),
            ("pubg_20k", "PUBG Mobile", "UC 20K", 17000, 20000, 1),
        )
        connection.executemany(
            """
            INSERT INTO products (sku, provider, name, cost_price, price, active, category, product_type, brand)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'prepaid', ?)
            """,
            [(sku, provider, name, cost, price, active, provider, provider) for sku, provider, name, cost, price, active in products],
        )
        connection.execute("INSERT INTO topup (id, nominal, price, product_cost) VALUES ('legacy-1', 'ml_10k', 10000, 8500)")
        connection.execute("INSERT INTO promos (id, code, target_scope, target_value) VALUES (1, 'LEGACY', 'sku', 'ml_10k')")
        connection.execute(
            "INSERT INTO promotion_target_catalog (id, promo_id, target_type, target_key, active) VALUES (1, 1, 'sku', 'ml_10k', 1)"
        )
        connection.execute(
            "INSERT INTO provider_attempts (id, order_id, provider, request_state) VALUES (1, 'legacy-1', 'digiflazz', 'SENT_UNKNOWN')"
        )
        connection.commit()
    finally:
        connection.close()


class _FakeCatalogClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    async def fetch_prepaid_price_list(self):
        self.calls += 1
        from app.services.digiflazz_catalog_client import DigiflazzCatalogResponse

        return DigiflazzCatalogResponse(rows=tuple(self.rows))


class _FailingCatalogClient:
    """A fake transport failure; it never creates an HTTP client."""

    def __init__(self, code: str = "catalog_provider_timeout") -> None:
        self.code = code
        self.calls = 0

    async def fetch_prepaid_price_list(self):
        self.calls += 1
        from app.services.digiflazz_catalog_client import DigiflazzCatalogClientError

        raise DigiflazzCatalogClientError(self.code, "Price-list Digiflazz melewati batas waktu.")


def _candidate(sku: str = "ML10", name: str = "Mobile Legends 10K", price: int = 9000) -> dict[str, Any]:
    return {
        "buyer_sku_code": sku,
        "product_name": name,
        "category": "Games",
        "brand": "Mobile Legends",
        "type": "Voucher",
        "seller_name": "Digiflazz",
        "price": price,
        "desc": "candidate description",
        "buyer_product_status": True,
        "seller_product_status": True,
        "stock": 10,
        "unlimited_stock": False,
        "multi": False,
    }


class CatalogSyncPhase2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="lixafa-catalog-phase2-")
        self.root = Path(self.temp_dir.name)
        self.path = self.root / "catalog-phase2.db"
        _create_baseline(self.path)
        self.url = _sqlite_url(self.path)
        preflight = asyncio.run(preflight_catalog_schema(self.url))
        asyncio.run(
            upgrade_catalog_schema(
                self.url,
                target_fingerprint=preflight["target"]["fingerprint"],
                sqlite_backup_path=self.root / "catalog-phase2.backup.db",
                sqlite_engine_stopped=True,
            )
        )
        self.previous_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "APP_ENV": os.environ.get("APP_ENV"),
        }
        os.environ["DATABASE_URL"] = self.url
        # Staging mode prevents app.database from trying to bootstrap schema;
        # the test has already prepared its disposable Phase 1 schema above.
        os.environ["APP_ENV"] = "staging"
        from app.core import database as core_database
        from app.core import settings as core_settings
        from app import database as legacy_database
        from app.services import catalog_sync_service

        importlib.reload(core_settings)
        importlib.reload(core_database)
        importlib.reload(legacy_database)
        self.service = importlib.reload(catalog_sync_service)
        self.core_database = core_database

    def tearDown(self) -> None:
        if self.core_database.engine is not None:
            asyncio.run(self.core_database.engine.dispose())
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def _rows(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(str(self.path))
        try:
            return connection.execute(statement, parameters).fetchall()
        finally:
            connection.close()

    def _execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> None:
        connection = sqlite3.connect(str(self.path))
        try:
            connection.execute(statement, parameters)
            connection.commit()
        finally:
            connection.close()

    def _preview(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        client = _FakeCatalogClient(rows)
        with patch.object(
            self.service,
            "get_digiflazz_products",
            new=AsyncMock(side_effect=AssertionError("legacy client must not be called")),
        ) as legacy_client:
            result = asyncio.run(self.service.preview_catalog_sync(client=client, cooldown_seconds=1))
        self.assertEqual(client.calls, 1)
        legacy_client.assert_not_awaited()
        return result

    def _move_cooldown_to_past(self) -> None:
        self._execute(
            "UPDATE catalog_provider_sync_state SET next_full_fetch_not_before='2000-01-01T00:00:00Z'"
        )

    def _attach_first_review(self, run_id: str) -> str:
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(run_id))
        review_id = next(item["review"]["review_id"] for item in detail["items"] if item.get("review"))
        catalog_product_id = self._rows("SELECT catalog_product_id FROM products WHERE sku='ml_10k'")[0][0]
        asyncio.run(
            self.service.decide_catalog_mapping(
                review_id=review_id,
                decision="attach_existing",
                actor="catalog-reviewer",
                catalog_product_id=catalog_product_id,
            )
        )
        return review_id

    def test_preview_is_snapshot_only_and_keeps_storefront_product_unchanged(self) -> None:
        before = self._rows("SELECT sku, cost_price, price, active, store_enabled FROM products ORDER BY sku")
        result = self._preview([_candidate()])
        run_id = result["run"]["run_id"]
        after = self._rows("SELECT sku, cost_price, price, active, store_enabled FROM products ORDER BY sku")

        self.assertEqual(before, after)
        self.assertEqual(self._rows("SELECT COUNT(*) FROM provider_products"), [(0,)])
        self.assertEqual(self._rows("SELECT COUNT(*) FROM catalog_sync_run_items WHERE run_id=?", (run_id,)), [(1,)])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(run_id))
        self.assertTrue(detail["can_apply"] is False)
        self.assertIn("review_pending", detail["blocked_reasons"])
        self.assertNotIn("candidate description", str(detail))

    def test_apply_inserts_provider_candidate_without_touching_selling_price_and_is_idempotent(self) -> None:
        before = self._rows("SELECT price, active, store_enabled FROM products WHERE sku='ml_10k'")[0]
        preview = self._preview([_candidate()])
        self._attach_first_review(preview["run"]["run_id"])

        applied = asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=preview["run"]["run_id"], actor="owner"))
        self.assertFalse(applied["already_applied"])
        self.assertEqual(
            self._rows("SELECT provider_sku, mapping_state, catalog_product_id FROM provider_products"),
            [("ML10", "APPROVED", self._rows("SELECT catalog_product_id FROM products WHERE sku='ml_10k'")[0][0])],
        )
        self.assertEqual(self._rows("SELECT price, active, store_enabled FROM products WHERE sku='ml_10k'")[0], before)

        repeated = asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=preview["run"]["run_id"], actor="owner"))
        self.assertTrue(repeated["already_applied"])
        self.assertEqual(self._rows("SELECT COUNT(*) FROM provider_products"), [(1,)])

    def test_create_logical_product_mapping_stays_unpublished_and_does_not_copy_provider_cost(self) -> None:
        preview = self._preview([_candidate("FFX", "Free Fire 50K", 40000)])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(preview["run"]["run_id"]))
        review_id = detail["items"][0]["review"]["review_id"]
        decision = asyncio.run(
            self.service.decide_catalog_mapping(
                review_id=review_id,
                decision="create_unpublished",
                actor="catalog-reviewer",
                create_logical_product={
                    "sku": "ff-50-catalog-draft",
                    "name": "Free Fire 50 Draft",
                    "selling_price": 55000,
                    "category": "Games",
                },
            )
        )
        created = decision["created_logical_product"]
        self.assertIsNotNone(created)
        row = self._rows(
            "SELECT price, cost_price, active, store_enabled FROM products WHERE sku='ff-50-catalog-draft'"
        )[0]
        self.assertEqual(row, (55000, None, 0, 0))
        self.assertEqual(created["active"], False)
        self.assertEqual(created["store_enabled"], False)

    def test_malformed_and_duplicate_rows_are_rejected_without_provider_storage(self) -> None:
        malformed = {"product_name": "Missing SKU"}
        duplicate = [_candidate("DUP", "Mobile Legends 10K"), _candidate("DUP", "Mobile Legends 10K")]
        result = self._preview([malformed, *duplicate])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(result["run"]["run_id"]))
        self.assertEqual({item["proposed_action"] for item in detail["items"]}, {"REJECTED"})
        self.assertEqual(self._rows("SELECT COUNT(*) FROM catalog_mapping_reviews"), [(0,)])
        self.assertEqual(self._rows("SELECT COUNT(*) FROM provider_products"), [(0,)])

    def test_ambiguous_denomination_requires_explicit_mapping_review(self) -> None:
        result = self._preview([_candidate("AMB", "Mobile Legends 10K 20K")])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(result["run"]["run_id"]))

        self.assertEqual(detail["items"][0]["proposed_action"], "MAPPING_REQUIRED")
        self.assertTrue(detail["items"][0]["requires_review"])
        self.assertIn("ambiguous_denomination", detail["items"][0]["reason_codes"])
        self.assertEqual(detail["items"][0]["review"]["status"], "PENDING")

    def test_bare_k_is_ambiguous_but_explicit_ribu_is_normalized(self) -> None:
        from app.services.catalog_normalizer import NormalizedCandidate, normalize_digiflazz_prepaid_row

        bare_k = normalize_digiflazz_prepaid_row(_candidate("BAREK", "Mobile Legends 10K"))
        explicit_ribu = normalize_digiflazz_prepaid_row(_candidate("PULSA10", "Pulsa 10 ribu"))

        self.assertIsInstance(bare_k, NormalizedCandidate)
        self.assertTrue(bare_k.ambiguous)
        self.assertIsNone(bare_k.normalized_attributes["denomination_value"])
        self.assertIsInstance(explicit_ribu, NormalizedCandidate)
        self.assertFalse(explicit_ribu.ambiguous)
        self.assertEqual(explicit_ribu.normalized_attributes["denomination_value"], "10000")
        self.assertEqual(explicit_ribu.normalized_attributes["denomination_unit"], "IDR")

    def test_invalid_cost_rejects_snapshot_without_replacing_last_known_good(self) -> None:
        initial = self._preview([_candidate("GOOD-COST", "Pulsa 10 ribu", 9000)])
        self._attach_first_review(initial["run"]["run_id"])
        applied = asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=initial["run"]["run_id"], actor="owner"))
        before_state = asyncio.run(self.service.catalog_sync_status())
        before_candidate = self._rows(
            "SELECT provider_sku, cost_price, availability_state FROM provider_products"
        )
        self.assertEqual(before_state["last_successful_run_id"], applied["run"]["run_id"])

        self._move_cooldown_to_past()
        broken = _candidate("BROKEN-COST", "Pulsa 20 ribu")
        broken["price"] = "not-a-decimal"
        incomplete = self._preview([broken])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(incomplete["run"]["run_id"]))

        self.assertEqual(detail["items"][0]["proposed_action"], "REJECTED")
        self.assertIn("missing_or_invalid_cost_price", detail["items"][0]["reason_codes"])
        self.assertFalse(detail["can_apply"])
        self.assertIn("snapshot_contains_rejected_items", detail["blocked_reasons"])
        self.assertEqual(
            asyncio.run(self.service.catalog_sync_status())["last_successful_run_id"],
            before_state["last_successful_run_id"],
        )
        self.assertEqual(
            self._rows("SELECT provider_sku, cost_price, availability_state FROM provider_products"),
            before_candidate,
        )

    def test_rejected_mapping_never_creates_or_changes_a_provider_candidate(self) -> None:
        before_products = self._rows("SELECT sku, price, active, store_enabled FROM products ORDER BY sku")
        preview = self._preview([_candidate("REJECT", "Mobile Legends 10K")])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(preview["run"]["run_id"]))
        review_id = detail["items"][0]["review"]["review_id"]
        asyncio.run(
            self.service.decide_catalog_mapping(
                review_id=review_id,
                decision="reject",
                actor="catalog-reviewer",
                note="candidate needs manual supplier follow-up",
            )
        )

        asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=preview["run"]["run_id"], actor="owner"))
        self.assertEqual(self._rows("SELECT COUNT(*) FROM provider_products"), [(0,)])
        self.assertEqual(
            self._rows("SELECT sku, price, active, store_enabled FROM products ORDER BY sku"),
            before_products,
        )

    def test_durable_cooldown_and_lease_contention_reject_before_provider_fetch(self) -> None:
        self._preview([_candidate()])
        client = _FakeCatalogClient([_candidate("LATER")])
        with self.assertRaises(self.service.CatalogSyncRateLimited):
            asyncio.run(self.service.preview_catalog_sync(client=client, cooldown_seconds=1))
        self.assertEqual(client.calls, 0)

        self._move_cooldown_to_past()
        self._execute(
            """
            INSERT INTO catalog_sync_locks (
                provider_code, catalog_scope, owner_token, run_id, fencing_generation, acquired_at, expires_at
            ) VALUES ('digiflazz', 'prepaid', 'other-owner', 'other-run', 9, '2026-01-01T00:00:00Z', '2999-01-01T00:00:00Z')
            ON CONFLICT(provider_code, catalog_scope) DO UPDATE SET
                owner_token='other-owner', run_id='other-run', fencing_generation=9,
                expires_at='2999-01-01T00:00:00Z'
            """
        )
        with self.assertRaises(self.service.CatalogSyncBusy):
            asyncio.run(self.service.preview_catalog_sync(client=client, cooldown_seconds=1))
        self.assertEqual(client.calls, 0)

    def test_missing_sku_in_complete_later_snapshot_marks_prior_candidate_stale_without_delete(self) -> None:
        preview = self._preview([_candidate("OLD", "Mobile Legends 10K")])
        self._attach_first_review(preview["run"]["run_id"])
        asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=preview["run"]["run_id"], actor="owner"))
        self._move_cooldown_to_past()

        empty_preview = self._preview([])
        applied = asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=empty_preview["run"]["run_id"], actor="owner"))
        self.assertFalse(applied["already_applied"])
        self.assertEqual(self._rows("SELECT COUNT(*) FROM provider_products"), [(1,)])
        self.assertEqual(self._rows("SELECT availability_state FROM provider_products"), [("STALE",)])

    def test_run_items_are_immutable_after_preview(self) -> None:
        preview = self._preview([_candidate()])
        item_id = self._rows("SELECT run_item_id FROM catalog_sync_run_items WHERE run_id=?", (preview["run"]["run_id"],))[0][0]
        with self.assertRaises(sqlite3.DatabaseError):
            self._execute("UPDATE catalog_sync_run_items SET proposed_action='CHANGED' WHERE run_item_id=?", (item_id,))

    def test_failed_preview_preserves_last_known_good_and_does_not_leak_unknown_fields(self) -> None:
        initial = self._preview([_candidate("GOOD", "Mobile Legends 10K")])
        self._attach_first_review(initial["run"]["run_id"])
        applied = asyncio.run(self.service.apply_catalog_snapshot(preview_run_id=initial["run"]["run_id"], actor="owner"))
        before_state = asyncio.run(self.service.catalog_sync_status())
        before_products = self._rows("SELECT provider_sku, cost_price, availability_state FROM provider_products")
        self.assertEqual(before_state["last_successful_run_id"], applied["run"]["run_id"])

        self._move_cooldown_to_past()
        failing = _FailingCatalogClient()
        with self.assertRaises(self.service.CatalogSyncError):
            asyncio.run(self.service.preview_catalog_sync(client=failing, cooldown_seconds=1))
        self.assertEqual(failing.calls, 1)

        after_state = asyncio.run(self.service.catalog_sync_status())
        self.assertEqual(after_state["last_successful_run_id"], before_state["last_successful_run_id"])
        self.assertEqual(after_state["last_successful_snapshot_hash"], before_state["last_successful_snapshot_hash"])
        self.assertEqual(after_state["last_failure_code"], "catalog_provider_timeout")
        self.assertEqual(self._rows("SELECT provider_sku, cost_price, availability_state FROM provider_products"), before_products)

        self._move_cooldown_to_past()
        row_with_unknown = _candidate("SAFE", "Mobile Legends 10K")
        row_with_unknown["api_key"] = "must-never-persist-or-return"
        safe_preview = self._preview([row_with_unknown])
        detail = asyncio.run(self.service.get_catalog_sync_run_detail(safe_preview["run"]["run_id"]))
        snapshot = self._rows(
            "SELECT safe_raw_attributes_json FROM catalog_sync_run_items WHERE run_id=?",
            (safe_preview["run"]["run_id"],),
        )[0][0]
        self.assertNotIn("must-never-persist-or-return", snapshot)
        self.assertNotIn("must-never-persist-or-return", str(detail))


if __name__ == "__main__":
    unittest.main()
