import importlib
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


class CatalogPhase0SafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.previous_env = {
            key: os.environ.get(key)
            for key in (
                "APP_ENV",
                "DATABASE_URL",
                "DIGIFLAZZ_USERNAME",
                "DIGIFLAZZ_API_KEY",
                "DIGIFLAZZ_WEBHOOK_SECRET",
                "DIGIFLAZZ_BASE_URL",
                "DIGIFLAZZ_CATALOG_PRICE_LIST_ENABLED",
                "DIGIFLAZZ_TRANSACTION_SEND_ENABLED",
                "DIGIFLAZZ_TRANSACTION_STATUS_ENABLED",
                "DIGIFLAZZ_BALANCE_WATCH_ENABLED",
                "DIGIFLAZZ_DEPOSIT_ENABLED",
            )
        }
        os.environ["APP_ENV"] = "test"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"
        os.environ["DIGIFLAZZ_USERNAME"] = ""
        os.environ["DIGIFLAZZ_API_KEY"] = ""
        os.environ["DIGIFLAZZ_WEBHOOK_SECRET"] = ""
        os.environ["DIGIFLAZZ_BASE_URL"] = "https://catalog-environment.example.invalid/v1"
        os.environ["DIGIFLAZZ_CATALOG_PRICE_LIST_ENABLED"] = "false"
        os.environ["DIGIFLAZZ_TRANSACTION_SEND_ENABLED"] = "false"
        os.environ["DIGIFLAZZ_TRANSACTION_STATUS_ENABLED"] = "false"
        os.environ["DIGIFLAZZ_BALANCE_WATCH_ENABLED"] = "false"
        os.environ["DIGIFLAZZ_DEPOSIT_ENABLED"] = "false"

        from app import database as app_database
        from app.core import database as core_database
        from app.core import settings as core_settings
        import app.engine as engine
        from app.services import catalog_sync_service, digiflazz_capabilities, digiflazz_service, provider_settings

        self.core_settings = importlib.reload(core_settings)
        self.core_database = importlib.reload(core_database)
        self.database = importlib.reload(app_database)
        self.capabilities = importlib.reload(digiflazz_capabilities)
        self.provider_settings = importlib.reload(provider_settings)
        self.digiflazz_service = importlib.reload(digiflazz_service)
        self.catalog_sync_service = importlib.reload(catalog_sync_service)
        self.engine = importlib.reload(engine)
        await self.database.init_db()

    async def asyncTearDown(self) -> None:
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    async def test_legacy_catalog_facade_is_disabled_without_provider_or_product_mutation(self) -> None:
        before = await self.database.db_query("SELECT COUNT(*) FROM products")
        with patch.object(
            self.catalog_sync_service,
            "get_digiflazz_products",
            new=AsyncMock(side_effect=AssertionError("provider must not be called")),
        ) as provider_fetch:
            result = await self.catalog_sync_service.sync_digiflazz_products()
        after = await self.database.db_query("SELECT COUNT(*) FROM products")

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "legacy_catalog_sync_disabled")
        self.assertEqual(result["processed"], 0)
        provider_fetch.assert_not_awaited()
        self.assertEqual(before, after)

    async def test_embedded_engine_never_calls_legacy_catalog_sync(self) -> None:
        with patch.object(
            self.catalog_sync_service,
            "sync_digiflazz_products",
            new=AsyncMock(side_effect=AssertionError("legacy sync must stay isolated")),
        ) as legacy_sync:
            await self.engine.product_catalog_sync_engine()

        legacy_sync.assert_not_awaited()

    async def test_database_override_cannot_restore_legacy_sync_or_credentials(self) -> None:
        await self.database.db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, 'test')
            """,
            {"key": "provider.digiflazz_product_auto_sync_enabled", "value": "1"},
        )
        await self.database.db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, 'test')
            """,
            {"key": "provider.digiflazz_username", "value": "database-username"},
        )
        await self.database.db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, 'test')
            """,
            {"key": "provider.digiflazz_api_key", "value": "database-api-key"},
        )

        runtime = await self.provider_settings.get_provider_runtime_settings()

        self.assertEqual(runtime["digiflazz_product_auto_sync_enabled"], "0")
        self.assertEqual(runtime["digiflazz_username"], "")
        self.assertEqual(runtime["digiflazz_api_key"], "")

    async def test_catalog_destination_ignores_database_runtime_override(self) -> None:
        await self.database.db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, 'test')
            """,
            {"key": "provider.digiflazz_base_url", "value": "https://database-override.example.invalid/v1"},
        )

        runtime = await self.provider_settings.get_provider_runtime_settings()
        catalog_config = await self.provider_settings.get_digiflazz_catalog_config()

        self.assertEqual(runtime["digiflazz_base_url"], "https://database-override.example.invalid/v1")
        self.assertEqual(catalog_config["base_url"], "https://catalog-environment.example.invalid/v1")

    async def test_catalog_capability_denial_stops_before_configuration_or_http(self) -> None:
        with patch.object(
            self.digiflazz_service,
            "_runtime_config",
            new=AsyncMock(side_effect=AssertionError("configuration must not be read when disabled")),
        ):
            result = await self.digiflazz_service.get_digiflazz_products()

        self.assertIn("belum diizinkan", result)

    async def test_incomplete_transaction_configuration_is_proven_not_sent(self) -> None:
        with patch.object(self.digiflazz_service, "is_digiflazz_capability_enabled", return_value=True), patch.object(
            self.digiflazz_service,
            "_runtime_config",
            new=AsyncMock(
                return_value={
                    "username": "test-user",
                    "api_key": "",
                    "base_url": "https://provider.invalid/v1",
                }
            ),
        ), patch.object(
            self.digiflazz_service,
            "_call_digiflazz",
            new=AsyncMock(side_effect=AssertionError("HTTP path must not be reached")),
        ) as provider_call:
            result = await self.digiflazz_service.kirim_digiflazz("sku", "0812", "order-test")

        self.assertEqual(result["_provider_outcome"], "NOT_SENT")
        self.assertEqual(result["_error_type"], "DigiflazzConfigurationIncomplete")
        provider_call.assert_not_awaited()

    async def test_disabled_send_keeps_order_not_sent_without_provider_attempt(self) -> None:
        await self.database.db_execute(
            """
            INSERT INTO topup (
                id, phone, target_id, nominal, price, product_cost, amount,
                payment_status, topup_status, provider_outcome
            )
            VALUES (
                'phase0-disabled-send', '08123456789', 'target', 'sku-test', 10000, 8000, 10000,
                'PAID', 'PROCESSING', 'NOT_SENT'
            )
            """
        )

        await self.engine.polling_status_engine()

        rows = await self.database.db_query(
            """
            SELECT topup_status, provider_outcome, provider_claim_id
            FROM topup WHERE id='phase0-disabled-send'
            """
        )
        attempts = await self.database.db_query(
            "SELECT COUNT(*) FROM provider_attempts WHERE order_id='phase0-disabled-send'"
        )
        self.assertEqual(rows[0], ("PROCESSING", "NOT_SENT", None))
        self.assertEqual(attempts[0][0], 0)

    def test_admin_payload_masks_all_digiflazz_credentials(self) -> None:
        username = "phase0-username-secret"
        api_key = "phase0-api-key-secret"
        webhook_secret = "phase0-webhook-secret"
        runtime = {
            "digiflazz_username": username,
            "digiflazz_api_key": api_key,
            "digiflazz_webhook_secret": webhook_secret,
        }

        payload = self.provider_settings.build_provider_admin_payload(runtime)
        serialized = str(payload)

        self.assertNotIn(username, serialized)
        self.assertNotIn(api_key, serialized)
        self.assertNotIn(webhook_secret, serialized)
        self.assertNotIn("username", payload["providers"]["digiflazz"])
        self.assertTrue(payload["providers"]["digiflazz"]["credentials"][0]["configured"])

    def test_provider_response_redaction_removes_credential_fields(self) -> None:
        sanitized = self.digiflazz_service._redact_sensitive_provider_response(
            {
                "username": "phase0-username-secret",
                "sign": "phase0-sign-secret",
                "data": {"api_key": "phase0-api-key-secret", "status": "Sukses"},
            }
        )

        self.assertEqual(sanitized["username"], "[redacted]")
        self.assertEqual(sanitized["sign"], "[redacted]")
        self.assertEqual(sanitized["data"]["api_key"], "[redacted]")
        self.assertEqual(sanitized["data"]["status"], "Sukses")


if __name__ == "__main__":
    unittest.main()
