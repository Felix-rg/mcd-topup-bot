import asyncio
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class PromotionEligibilitySchemaTests(unittest.TestCase):
    """Exercise the additive migration only against a disposable SQLite file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_db = tempfile.NamedTemporaryFile(
            suffix="-promo-eligibility-schema.db",
            delete=False,
        )
        cls.temp_db.close()
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        database_path = Path(cls.temp_db.name).resolve()
        project_database_path = (Path(__file__).resolve().parents[1] / "lixafa.db").resolve()
        if database_path == project_database_path:
            raise AssertionError("Eligibility schema tests may not use lixafa.db")

        # Start with the exact pre-eligibility base table so init_db must take
        # the additive ALTER path while preserving an already-live promo row.
        with sqlite3.connect(database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE promos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    code TEXT,
                    description TEXT,
                    badge TEXT,
                    cta_text TEXT,
                    cta_url TEXT,
                    image_url TEXT,
                    rule_type TEXT DEFAULT 'content',
                    target_scope TEXT DEFAULT 'all',
                    target_value TEXT,
                    discount_type TEXT,
                    discount_value NUMERIC,
                    max_discount NUMERIC,
                    usage_limit INTEGER DEFAULT 0,
                    payment_methods TEXT,
                    budget_limit NUMERIC DEFAULT 0,
                    max_per_customer INTEGER DEFAULT 0,
                    max_per_phone INTEGER DEFAULT 0,
                    max_per_target INTEGER DEFAULT 0,
                    stackable INTEGER DEFAULT 1,
                    priority INTEGER DEFAULT 0,
                    starts_at TIMESTAMP,
                    ends_at TIMESTAMP,
                    show_on_website INTEGER DEFAULT 1,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO promos (
                    title, code, rule_type, target_scope, discount_type,
                    discount_value, active
                ) VALUES (
                    'Legacy first buyer', 'LEGACY-FIRST', 'price', 'all',
                    'fixed', 500, 1
                );
                """
            )

        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"

        from app.core import settings as core_settings
        from app.core import database as core_database
        from app import database as app_database

        importlib.reload(core_settings)
        cls.core_database = importlib.reload(core_database)
        cls.database = importlib.reload(app_database)
        asyncio.run(cls.core_database.init_db())

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            asyncio.run(cls.core_database.get_engine().dispose())
        finally:
            if cls.previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = cls.previous_database_url
            if os.path.exists(cls.temp_db.name):
                os.remove(cls.temp_db.name)

            # Do not leak the deleted disposable engine into later test files.
            from app.core import settings as core_settings
            from app.core import database as core_database
            from app import database as app_database

            importlib.reload(core_settings)
            importlib.reload(core_database)
            importlib.reload(app_database)

    @classmethod
    def _query(cls, statement, params=None):
        return asyncio.run(cls.database.db_query(statement, params or {}))

    def test_additive_column_is_nullable_without_default_and_preserves_legacy_null(self) -> None:
        columns = self._query("PRAGMA table_info(promos)")
        eligibility_columns = [row for row in columns if row[1] == "eligibility_rules"]
        self.assertEqual(len(eligibility_columns), 1)
        column = eligibility_columns[0]
        self.assertEqual(int(column[3]), 0, "eligibility_rules must remain nullable")
        self.assertIsNone(column[4], "eligibility_rules must not have a widening default")

        legacy = self._query(
            "SELECT customer_segment, eligibility_rules FROM promos WHERE code='LEGACY-FIRST'"
        )
        self.assertEqual(legacy, [("all", None)])

    def test_migration_is_idempotent_and_records_one_marker_without_backfill(self) -> None:
        asyncio.run(self.core_database.init_db())
        asyncio.run(self.core_database.init_db())

        columns = self._query("PRAGMA table_info(promos)")
        self.assertEqual(sum(1 for row in columns if row[1] == "eligibility_rules"), 1)
        self.assertEqual(
            self._query("SELECT eligibility_rules FROM promos WHERE code='LEGACY-FIRST'"),
            [(None,)],
        )

        migration_id = getattr(
            self.core_database,
            "PROMOTION_ELIGIBILITY_MIGRATION_ID",
            None,
        )
        self.assertTrue(migration_id, "Eligibility migration must have a stable marker constant")
        markers = self._query(
            "SELECT migration_id, checksum FROM schema_migrations WHERE migration_id=:migration_id",
            {"migration_id": migration_id},
        )
        self.assertEqual(len(markers), 1)
        self.assertIn("eligibility", str(markers[0][0]).casefold())
        self.assertTrue(str(markers[0][1]).strip())


if __name__ == "__main__":
    unittest.main()
