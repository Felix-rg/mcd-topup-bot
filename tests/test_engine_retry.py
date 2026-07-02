import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import text


class EngineRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"

        import importlib

        import core.database as core_database
        import database as legacy_database
        import app.engine as engine_module

        self.core_database = importlib.reload(core_database)
        self.legacy_database = importlib.reload(legacy_database)
        self.engine_module = importlib.reload(engine_module)

        await self.legacy_database.init_db()
        engine = self.legacy_database.engine
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS topup (
                    id TEXT PRIMARY KEY,
                    phone TEXT,
                    target_id TEXT,
                    nickname TEXT,
                    nominal TEXT,
                    price NUMERIC,
                    amount NUMERIC,
                    payment_method TEXT,
                    payment_status TEXT,
                    topup_status TEXT,
                    sn TEXT,
                    note TEXT,
                    invoice_url TEXT,
                    provider_retry_count INTEGER DEFAULT 0,
                    provider_last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
                )
            )
            await conn.execute(
                text("""
                INSERT INTO topup (id, phone, nominal, payment_status, topup_status, provider_retry_count)
                VALUES (:id, :phone, :nominal, :payment_status, :topup_status, :retry)
                """),
                {
                    "id": "order-1",
                    "phone": "08123456789",
                    "nominal": "ml_10k",
                    "payment_status": "PAID",
                    "topup_status": "PROCESSING",
                    "retry": 0,
                },
            )

    async def asyncTearDown(self) -> None:
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    async def test_retry_failure_does_not_immediately_mark_failed(self) -> None:
        failure_response = {"data": {"status": "Gagal", "message": "temporary issue"}}

        with patch("app.engine.kirim_digiflazz", new=AsyncMock(return_value=failure_response)):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_retry_count, provider_last_error FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "PROCESSING")
        self.assertEqual(rows[0][1], 1)
        self.assertIn("temporary issue", rows[0][2])

    async def test_retry_failure_becomes_failed_after_max_retry(self) -> None:
        failure_response = {"data": {"status": "Gagal", "message": "temporary issue"}}

        with patch("app.engine.kirim_digiflazz", new=AsyncMock(return_value=failure_response)):
            for _ in range(self.engine_module.MAX_PROVIDER_RETRY):
                await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_retry_count FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "FAILED")
        self.assertGreaterEqual(rows[0][1], self.engine_module.MAX_PROVIDER_RETRY)


if __name__ == "__main__":
    unittest.main()
