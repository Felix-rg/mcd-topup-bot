"""Focused safety coverage for the staging-only write-quiescence fence."""

import asyncio
import importlib
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient


class WriteQuiescenceGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_admitted_writer_drains_and_no_new_writer_enters_after_enable(self) -> None:
        from app.core.write_quiescence import WriteQuiescenceActive, WriteQuiescenceGate

        gate = WriteQuiescenceGate(enabled=False)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def admitted_writer() -> None:
            async with gate.writer_section("existing"):
                entered.set()
                await release.wait()

        writer = asyncio.create_task(admitted_writer())
        await entered.wait()
        self.assertEqual(gate.active_writers, 1)

        gate.begin_quiescence()
        gate.mark_engine_stopped()
        self.assertFalse(gate.ready)

        with self.assertRaises(WriteQuiescenceActive):
            async with gate.writer_section("new_after_enable"):
                self.fail("a post-enable writer must never enter")

        release.set()
        await writer
        self.assertTrue(await gate.wait_for_drain(timeout=0.1))
        self.assertEqual(gate.active_writers, 0)
        self.assertTrue(gate.ready)

    async def test_concurrent_post_enable_admissions_are_all_rejected(self) -> None:
        from app.core.write_quiescence import WriteQuiescenceActive, WriteQuiescenceGate

        gate = WriteQuiescenceGate(enabled=False)
        gate.begin_quiescence()
        gate.mark_engine_stopped()

        async def contender() -> bool:
            try:
                async with gate.writer_section("concurrent"):
                    return True
            except WriteQuiescenceActive:
                return False

        results = await asyncio.gather(*(contender() for _ in range(32)))
        self.assertEqual(results, [False] * 32)
        self.assertEqual(gate.active_writers, 0)
        self.assertTrue(gate.ready)

    async def test_explicit_multi_worker_runtime_fails_readiness(self) -> None:
        from app.core.write_quiescence import WriteQuiescenceGate

        previous = os.environ.get("WEB_CONCURRENCY")
        os.environ["WEB_CONCURRENCY"] = "2"
        try:
            gate = WriteQuiescenceGate(enabled=True)
            gate.mark_engine_stopped()
            self.assertFalse(gate.ready)
            self.assertFalse(gate.maintenance_payload()["single_process_runtime"])
        finally:
            if previous is None:
                os.environ.pop("WEB_CONCURRENCY", None)
            else:
                os.environ["WEB_CONCURRENCY"] = previous


class QuiescenceEnabledRuntimeTests(unittest.TestCase):
    _ENV_KEYS = (
        "APP_ENV",
        "DATABASE_URL",
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "ALLOWED_ORIGINS",
        "DEFAULT_ADMIN_USERNAME",
        "DEFAULT_ADMIN_PASSWORD",
        "LIXAFA_WRITE_QUIESCENCE",
        "WEB_CONCURRENCY",
        "UVICORN_WORKERS",
        "GUNICORN_WORKERS",
    )

    def setUp(self) -> None:
        self.previous_environment = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update(
            {
                "APP_ENV": "staging",
                # The enabled setting intentionally accepts only asyncpg URLs.
                # This non-routable local value is never connected in this test.
                "DATABASE_URL": "postgresql+asyncpg://quiescence@127.0.0.1:65432/lixafa_quiescence_test",
                "SECRET_KEY": "quiescence-test-secret",
                "JWT_SECRET_KEY": "quiescence-test-jwt-secret",
                "ALLOWED_ORIGINS": "https://staging.test",
                "DEFAULT_ADMIN_USERNAME": "",
                "DEFAULT_ADMIN_PASSWORD": "",
                "LIXAFA_WRITE_QUIESCENCE": "1",
            }
        )
        for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
            os.environ.pop(key, None)
        self._reload_runtime()

    def tearDown(self) -> None:
        for key, value in self.previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._reload_runtime()

    def _reload_runtime(self) -> None:
        import app.core.database as core_database
        import app.core.settings as core_settings
        import app.core.write_quiescence as write_quiescence
        import app.database as app_database
        import app.engine as engine
        import app.main as main
        import app.promotions.service as promotion_service
        import app.routes.admin_routes as admin_routes
        import app.routes.topup_routes as topup_routes
        import app.services.catalog_sync_service as catalog_sync_service
        import app.services.wallet_service as wallet_service

        self.core_settings = importlib.reload(core_settings)
        self.core_database = importlib.reload(core_database)
        self.app_database = importlib.reload(app_database)
        self.quiescence_module = importlib.reload(write_quiescence)
        self.wallet_service = importlib.reload(wallet_service)
        self.catalog_sync_service = importlib.reload(catalog_sync_service)
        self.promotion_service = importlib.reload(promotion_service)
        self.engine_module = importlib.reload(engine)
        self.admin_routes = importlib.reload(admin_routes)
        self.topup_routes = importlib.reload(topup_routes)
        self.main = importlib.reload(main)

    def test_enabled_health_is_read_only_and_all_representative_writers_are_blocked(self) -> None:
        init_runtime = AsyncMock()
        engine_loop = AsyncMock()
        topup_write = AsyncMock()
        wallet_write = AsyncMock()
        ensure_admin = AsyncMock()
        provider_catalog = AsyncMock()

        with (
            patch.object(self.main, "initialize_database_runtime", new=init_runtime),
            patch.object(self.main, "auto_engine_loop", new=engine_loop),
            patch.object(self.topup_routes, "db_execute", new=topup_write),
            patch.object(self.wallet_service, "db_execute", new=wallet_write),
            patch.object(self.admin_routes, "_ensure_default_admin", new=ensure_admin),
            patch.object(self.catalog_sync_service, "get_digiflazz_products", new=provider_catalog),
        ):
            with TestClient(self.main.app, raise_server_exceptions=False) as client:
                health = client.get("/admin/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(
                    health.json(),
                    {
                        "write_gate": "enabled",
                        "engine": "stopped",
                        "engine_stopped": True,
                        "in_flight_writers": 0,
                        "database_writes_allowed": False,
                        "ready": True,
                        "single_process_runtime": True,
                    },
                )

                blocked_requests = (
                    ("POST", "/topup", {"json": {}}),
                    ("GET", "/api/customer/wallet", {}),
                    ("POST", "/topup/order-quiesced/cancel", {"json": {}}),
                    ("POST", "/api/customer/wallet/open-payment", {"json": {}}),
                    ("POST", "/callback", {"content": b"{"}),
                    ("POST", "/api/webhook/digiflazz", {"content": b"{"}),
                    ("POST", "/admin/login", {"json": {}}),
                    ("POST", "/admin/api/products", {"json": {}}),
                    ("POST", "/admin/api/promos", {"json": {}}),
                    ("POST", "/admin/api/orders/order-quiesced/refund", {"json": {}}),
                    ("POST", "/admin/sync-products", {"json": {}}),
                    ("POST", "/admin/health", {"json": {}}),
                )
                for method, path, kwargs in blocked_requests:
                    response = client.request(method, path, **kwargs)
                    self.assertEqual(response.status_code, 503, f"{method} {path}")
                    self.assertEqual(response.json()["code"], "WRITE_QUIESCENCE_ACTIVE")
                    self.assertEqual(response.headers["retry-after"], "60")

        init_runtime.assert_awaited_once()
        engine_loop.assert_not_awaited()
        # A blocked request never reaches callback audit, lazy-wallet, admin
        # bootstrap, product sync, or any route-local write helper.
        topup_write.assert_not_awaited()
        wallet_write.assert_not_awaited()
        ensure_admin.assert_not_awaited()
        provider_catalog.assert_not_awaited()

    def test_disabled_default_preserves_the_normal_health_route_and_starts_engine(self) -> None:
        os.environ["LIXAFA_WRITE_QUIESCENCE"] = "0"
        self._reload_runtime()
        self.assertFalse(self.main.write_quiescence.enabled)

        init_runtime = AsyncMock()
        engine_loop = AsyncMock()
        with (
            patch.object(self.main, "initialize_database_runtime", new=init_runtime),
            patch.object(self.main, "auto_engine_loop", new=engine_loop),
        ):
            with TestClient(self.main.app, raise_server_exceptions=False) as client:
                response = client.get("/admin/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "lixafa-admin"})
        init_runtime.assert_awaited_once()
        engine_loop.assert_awaited_once()

    def test_internal_direct_writers_are_rejected_before_database_or_provider_access(self) -> None:
        from app.core.write_quiescence import WriteQuiescenceActive

        async def wallet_scenario() -> None:
            with patch.object(self.wallet_service, "init_db", new=AsyncMock()) as init_db:
                with self.assertRaises(WriteQuiescenceActive):
                    await self.wallet_service.wallet_add_entry(
                        customer_id=1,
                        entry_type="CREDIT",
                        amount=1000,
                        reference_type="test",
                        reference_id="blocked",
                        note="blocked",
                    )
                init_db.assert_not_awaited()

        async def promotion_scenario() -> None:
            with patch.object(self.promotion_service, "get_engine", new=Mock()) as get_engine:
                with self.assertRaises(WriteQuiescenceActive):
                    await self.promotion_service.persist_order_with_promotions(
                        order_id="blocked-order",
                        product={},
                        expected_quote=Mock(),
                        persist_order=AsyncMock(),
                    )
                with self.assertRaises(WriteQuiescenceActive):
                    await self.promotion_service.finalize_order_promotions("blocked-order")
                get_engine.assert_not_called()

        async def catalog_scenario() -> None:
            with (
                patch.object(self.catalog_sync_service, "get_digiflazz_products", new=AsyncMock()) as provider,
                patch.object(self.catalog_sync_service, "db_execute", new=AsyncMock()) as database_write,
            ):
                with self.assertRaises(WriteQuiescenceActive):
                    await self.catalog_sync_service.sync_digiflazz_products()
                provider.assert_not_awaited()
                database_write.assert_not_awaited()

        async def engine_scenario() -> None:
            reconcilers = (
                "reconcile_wallet_paid_orders",
                "reconcile_payment_engine",
                "reconcile_wallet_deposit_engine",
                "reconcile_open_payment_engine",
                "polling_status_engine",
                "provider_balance_watch_engine",
                "product_catalog_sync_engine",
            )
            mocked = {name: AsyncMock() for name in reconcilers}
            with patch.multiple(self.engine_module, **mocked):
                await self.engine_module.auto_engine_loop()
                for name in reconcilers:
                    mocked[name].assert_not_awaited()

        asyncio.run(wallet_scenario())
        asyncio.run(promotion_scenario())
        asyncio.run(catalog_scenario())
        asyncio.run(engine_scenario())

    def test_enabled_database_engine_uses_connection_local_read_only_default(self) -> None:
        self.core_database.engine = None
        sentinel = object()
        with patch.object(self.core_database, "create_async_engine", return_value=sentinel) as create_engine:
            self.assertIs(self.core_database.get_engine(), sentinel)
        _, kwargs = create_engine.call_args
        self.assertEqual(kwargs["connect_args"], {"server_settings": {"default_transaction_read_only": "on"}})
        self.core_database.engine = None


if __name__ == "__main__":
    unittest.main()
