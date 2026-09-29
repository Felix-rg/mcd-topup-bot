import asyncio
import hashlib
import hmac
import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class Phase1TransactionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.previous_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "DEFAULT_ADMIN_USERNAME": os.environ.get("DEFAULT_ADMIN_USERNAME"),
            "DEFAULT_ADMIN_PASSWORD": os.environ.get("DEFAULT_ADMIN_PASSWORD"),
            "TRIPAY_API_KEY": os.environ.get("TRIPAY_API_KEY"),
            "TRIPAY_PRIVATE_KEY": os.environ.get("TRIPAY_PRIVATE_KEY"),
            "TRIPAY_MERCHANT_CODE": os.environ.get("TRIPAY_MERCHANT_CODE"),
            "DIGIFLAZZ_USERNAME": os.environ.get("DIGIFLAZZ_USERNAME"),
            "DIGIFLAZZ_API_KEY": os.environ.get("DIGIFLAZZ_API_KEY"),
            "DIGIFLAZZ_WEBHOOK_SECRET": os.environ.get("DIGIFLAZZ_WEBHOOK_SECRET"),
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"
        os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "lixafa123"
        os.environ["TRIPAY_API_KEY"] = "DEV-test"
        os.environ["TRIPAY_PRIVATE_KEY"] = "tripay-private-test"
        os.environ["TRIPAY_MERCHANT_CODE"] = "T1234"
        os.environ["DIGIFLAZZ_USERNAME"] = "digiflazz-user"
        os.environ["DIGIFLAZZ_API_KEY"] = "digiflazz-key"
        os.environ["DIGIFLAZZ_WEBHOOK_SECRET"] = "digiflazz-webhook-secret"

        from app.core import database as core_database
        from app.core import settings as core_settings
        from app import database as legacy_database
        from app.routes import admin_routes, topup_routes
        from app.services import provider_settings, wallet_service
        import app.engine as engine

        importlib.reload(core_settings)
        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(provider_settings)
        importlib.reload(wallet_service)
        importlib.reload(engine)
        importlib.reload(admin_routes)
        importlib.reload(topup_routes)

        self.db = legacy_database
        self.wallet_service = wallet_service
        self.engine = engine
        self.admin_routes = admin_routes
        self.topup_routes = topup_routes
        self.app = FastAPI()
        self.app.include_router(topup_routes.router)
        self.app.include_router(admin_routes.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def admin_token(self):
        response = self.client.post("/admin/login", json={"username": "admin", "password": "lixafa123"})
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def digiflazz_signature(self, payload: dict) -> tuple[bytes, str]:
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha1=" + hmac.new(
            os.environ["DIGIFLAZZ_WEBHOOK_SECRET"].encode(),
            raw_body,
            hashlib.sha1,
        ).hexdigest()
        return raw_body, signature

    def test_duplicate_wallet_credit_same_reference_is_idempotent(self):
        async def scenario():
            await self.db.db_execute("INSERT INTO customer_accounts (id, name, phone, password) VALUES (1, 'Wallet', '0811', 'x')")
            await asyncio.gather(
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="CREDIT",
                    amount=10000,
                    reference_type="wallet_deposit",
                    reference_id="TRIPAY-DUP-1",
                    note="duplicate credit",
                ),
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="CREDIT",
                    amount=10000,
                    reference_type="wallet_deposit",
                    reference_id="TRIPAY-DUP-1",
                    note="duplicate credit",
                ),
            )
            balance_rows = await self.db.db_query("SELECT balance FROM wallet_accounts WHERE customer_id=1")
            ledger_rows = await self.db.db_query("SELECT COUNT(*) FROM wallet_ledger WHERE reference_id='TRIPAY-DUP-1'")
            return int(balance_rows[0][0]), int(ledger_rows[0][0])

        balance, ledger_count = asyncio.run(scenario())
        self.assertEqual(balance, 10000)
        self.assertEqual(ledger_count, 1)

    def test_duplicate_wallet_debit_same_reference_is_idempotent(self):
        async def scenario():
            await self.db.db_execute("INSERT INTO customer_accounts (id, name, phone, password) VALUES (1, 'Wallet', '0811', 'x')")
            await self.wallet_service.wallet_add_entry(
                customer_id=1,
                entry_type="CREDIT",
                amount=20000,
                reference_type="seed",
                reference_id="seed-1",
                note="seed balance",
            )
            await asyncio.gather(
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="DEBIT",
                    amount=7000,
                    reference_type="topup",
                    reference_id="order-debit-1",
                    note="wallet checkout",
                ),
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="DEBIT",
                    amount=7000,
                    reference_type="topup",
                    reference_id="order-debit-1",
                    note="wallet checkout retry",
                ),
            )
            balance_rows = await self.db.db_query("SELECT balance FROM wallet_accounts WHERE customer_id=1")
            ledger_rows = await self.db.db_query("SELECT COUNT(*) FROM wallet_ledger WHERE reference_id='order-debit-1'")
            return int(balance_rows[0][0]), int(ledger_rows[0][0])

        balance, ledger_count = asyncio.run(scenario())
        self.assertEqual(balance, 13000)
        self.assertEqual(ledger_count, 1)

    def test_concurrent_wallet_debit_cannot_make_balance_negative(self):
        async def scenario():
            await self.db.db_execute("INSERT INTO customer_accounts (id, name, phone, password) VALUES (1, 'Wallet', '0811', 'x')")
            await self.wallet_service.wallet_add_entry(
                customer_id=1,
                entry_type="CREDIT",
                amount=10000,
                reference_type="seed",
                reference_id="seed-2",
                note="seed balance",
            )
            results = await asyncio.gather(
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="DEBIT",
                    amount=7000,
                    reference_type="topup",
                    reference_id="order-debit-a",
                    note="wallet checkout a",
                ),
                self.wallet_service.wallet_add_entry(
                    customer_id=1,
                    entry_type="DEBIT",
                    amount=7000,
                    reference_type="topup",
                    reference_id="order-debit-b",
                    note="wallet checkout b",
                ),
                return_exceptions=True,
            )
            balance_rows = await self.db.db_query("SELECT balance FROM wallet_accounts WHERE customer_id=1")
            debit_rows = await self.db.db_query("SELECT COUNT(*) FROM wallet_ledger WHERE entry_type='DEBIT'")
            failures = [item for item in results if isinstance(item, Exception)]
            return int(balance_rows[0][0]), int(debit_rows[0][0]), len(failures)

        balance, debit_count, failure_count = asyncio.run(scenario())
        self.assertEqual(balance, 3000)
        self.assertEqual(debit_count, 1)
        self.assertEqual(failure_count, 1)

    def test_digiflazz_failed_webhook_after_success_does_not_downgrade_terminal_order(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, payment_status, topup_status, sn, status_updated_at)
                VALUES ('df-success-1', '0811', 'target', 'ml_10k', 'PAID', 'SUCCESS', 'SN-VALID', CURRENT_TIMESTAMP)
                """
            )
        )
        payload = {
            "data": {
                "ref_id": "df-success-1",
                "status": "Gagal",
                "message": "late failed webhook",
                "sn": "",
            }
        }
        raw_body, signature = self.digiflazz_signature(payload)
        response = self.client.post(
            "/api/webhook/digiflazz",
            content=raw_body,
            headers={"X-Hub-Signature": signature, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        rows = asyncio.run(self.db.db_query("SELECT topup_status, sn FROM topup WHERE id='df-success-1'"))
        self.assertEqual(rows[0][0], "SUCCESS")
        self.assertEqual(rows[0][1], "SN-VALID")

    def test_ambiguous_digiflazz_failure_stays_reconcilable(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, payment_status, topup_status,
                    provider_outcome, status_updated_at
                )
                VALUES ('df-ambiguous-1', '0811', 'target', 'ml_10k', 'PAID', 'PROCESSING', 'SENT_UNKNOWN', CURRENT_TIMESTAMP)
                """
            )
        )
        payload = {
            "data": {
                "ref_id": "df-ambiguous-1",
                "status": "Gagal",
                "rc": "01",
                "message": "timeout status is ambiguous",
            }
        }
        raw_body, signature = self.digiflazz_signature(payload)
        response = self.client.post(
            "/api/webhook/digiflazz",
            content=raw_body,
            headers={"X-Hub-Signature": signature, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        rows = asyncio.run(
            self.db.db_query(
                "SELECT topup_status, provider_outcome FROM topup WHERE id='df-ambiguous-1'"
            )
        )
        self.assertEqual(rows[0], ("PROCESSING", "SENT_UNKNOWN"))

    def test_digiflazz_callback_revokes_a_status_lookup_lease(self):
        async def acquire_lease():
            await self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, payment_status, topup_status,
                    provider_outcome, status_updated_at
                )
                VALUES ('df-lease-callback-1', '0811', 'target', 'ml_10k', 'PAID', 'PROCESSING', 'SENT_UNKNOWN', CURRENT_TIMESTAMP)
                """
            )
            return await self.engine.claim_provider_status_reconciliation("df-lease-callback-1")

        lease_id = asyncio.run(acquire_lease())
        self.assertTrue(lease_id)
        payload = {
            "data": {
                "ref_id": "df-lease-callback-1",
                "status": "Sukses",
                "rc": "00",
                "sn": "SN-CALLBACK-WINS",
            }
        }
        raw_body, signature = self.digiflazz_signature(payload)
        response = self.client.post(
            "/api/webhook/digiflazz",
            content=raw_body,
            headers={"X-Hub-Signature": signature, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)

        async def stale_write_count():
            return await self.db.db_execute_rowcount(
                """
                UPDATE topup
                SET topup_status='FAILED'
                WHERE id='df-lease-callback-1'
                  AND provider_claim_id=:lease_id
                  AND provider_claim_expires_at > CURRENT_TIMESTAMP
                """,
                {"lease_id": lease_id},
            )

        self.assertEqual(asyncio.run(stale_write_count()), 0)
        rows = asyncio.run(
            self.db.db_query(
                "SELECT topup_status, sn, provider_claim_id FROM topup WHERE id='df-lease-callback-1'"
            )
        )
        self.assertEqual(rows[0][0], "SUCCESS")
        self.assertEqual(rows[0][1], "SN-CALLBACK-WINS")
        self.assertIsNone(rows[0][2])

    def test_paid_callback_after_success_does_not_requeue_provider(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, payment_status, topup_status, sn, status_updated_at)
                VALUES ('tripay-success-1', '0811', 'target', 'ml_10k', 'UNPAID', 'SUCCESS', 'SN-VALID', CURRENT_TIMESTAMP)
                """
            )
        )
        payload = {"merchant_ref": "tripay-success-1", "reference": "TRIPAY-LATE-PAID", "status": "PAID"}
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(os.environ["TRIPAY_PRIVATE_KEY"].encode(), raw_body, hashlib.sha256).hexdigest()
        response = self.client.post(
            "/callback",
            content=raw_body,
            headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        rows = asyncio.run(self.db.db_query("SELECT payment_status, topup_status, sn FROM topup WHERE id='tripay-success-1'"))
        self.assertEqual(rows[0][0], "PAID")
        self.assertEqual(rows[0][1], "SUCCESS")
        self.assertEqual(rows[0][2], "SN-VALID")

    def test_paid_callback_after_customer_cancel_is_queued_for_manual_refund(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_reference,
                    payment_status, topup_status, status_updated_at
                )
                VALUES (
                    'tripay-cancelled-late-paid-1', '0811222555', 'target', 'ml_10k', 12000,
                    'TRIPAY-CANCELLED-LATE-1', 'CANCELED', 'FAILED', CURRENT_TIMESTAMP
                )
                """
            )
        )
        payload = {
            "merchant_ref": "tripay-cancelled-late-paid-1",
            "reference": "TRIPAY-CANCELLED-LATE-1",
            "status": "PAID",
            "amount": 12000,
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(os.environ["TRIPAY_PRIVATE_KEY"].encode(), raw_body, hashlib.sha256).hexdigest()
        for _ in range(2):
            response = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 200)

        rows = asyncio.run(
            self.db.db_query(
                """
                SELECT payment_status, topup_status, refund_status, payment_reference
                FROM topup
                WHERE id='tripay-cancelled-late-paid-1'
                """
            )
        )
        self.assertEqual(
            rows[0],
            ("CANCELED", "FAILED", "PAYMENT_RECEIVED_AFTER_CANCEL", "TRIPAY-CANCELLED-LATE-1"),
        )
        notifications = asyncio.run(
            self.db.db_query(
                "SELECT COUNT(*) FROM notification_outbox WHERE reference_id='tripay-cancelled-late-paid-1'"
            )
        )
        self.assertEqual(int(notifications[0][0]), 0)

        token = self.admin_token()
        retry = self.client.post(
            "/admin/api/orders/tripay-cancelled-late-paid-1/retry",
            headers={"token": token},
            json={"reason": "must not fulfill a cancelled order"},
        )
        self.assertEqual(retry.status_code, 409)

        for _ in range(2):
            refund = self.client.post(
                "/admin/api/orders/tripay-cancelled-late-paid-1/refund",
                headers={"token": token},
                json={"note": "manual review refund"},
            )
            self.assertEqual(refund.status_code, 200)

        refunded = asyncio.run(
            self.db.db_query(
                "SELECT payment_status, topup_status, refund_status FROM topup WHERE id='tripay-cancelled-late-paid-1'"
            )
        )
        self.assertEqual(refunded[0], ("REFUNDED", "FAILED", "REFUNDED"))

    def test_two_provider_workers_cannot_send_same_processing_order(self):
        async def scenario():
            await self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, product_cost, payment_status, topup_status, provider_retry_count)
                VALUES ('provider-claim-1', '0811', 'target', 'ml_10k', 8000, 'PAID', 'PROCESSING', 0)
                """
            )

            async def slow_provider(*args, **kwargs):
                await asyncio.sleep(0.05)
                return {"data": {"status": "pending", "rc": "03"}}

            original = self.engine.kirim_digiflazz
            mocked = AsyncMock(side_effect=slow_provider)
            self.engine.kirim_digiflazz = mocked
            try:
                await asyncio.gather(self.engine.polling_status_engine(), self.engine.polling_status_engine())
                return mocked.await_count
            finally:
                self.engine.kirim_digiflazz = original

        send_count = asyncio.run(scenario())
        self.assertEqual(send_count, 1)

    def test_admin_retry_reconciles_unknown_before_any_new_provider_send(self):
        token = self.admin_token()
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, payment_status, topup_status,
                    provider_outcome, status_updated_at
                )
                VALUES (
                    'admin-unknown-reconcile-1', '0811', 'target', 'ml_10k', 'PAID', 'PROCESSING',
                    'SENT_UNKNOWN', CURRENT_TIMESTAMP
                )
                """
            )
        )
        provider_lookup = AsyncMock(
            return_value={"data": {"status": "Sukses", "rc": "00", "sn": "SN-REC"}}
        )
        original_lookup = self.admin_routes.cek_status_digiflazz
        self.admin_routes.cek_status_digiflazz = provider_lookup
        try:
            response = self.client.post(
                "/admin/api/orders/admin-unknown-reconcile-1/retry",
                headers={"token": token},
                json={"reason": "verify unknown safely"},
            )
        finally:
            self.admin_routes.cek_status_digiflazz = original_lookup

        self.assertEqual(response.status_code, 409)
        provider_lookup.assert_awaited_once_with("ml_10k", "target", "admin-unknown-reconcile-1")
        rows = asyncio.run(
            self.db.db_query(
                "SELECT topup_status, provider_outcome FROM topup WHERE id='admin-unknown-reconcile-1'"
            )
        )
        self.assertEqual(rows[0][0], "SUCCESS")
        self.assertEqual(rows[0][1], "SUCCESS")

    def test_duplicate_digiflazz_success_webhook_queues_notification_once(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, payment_status, topup_status, status_updated_at)
                VALUES ('df-dup-success-1', '0811222333', 'target', 'ml_10k', 'PAID', 'PENDING_PROVIDER', CURRENT_TIMESTAMP)
                """
            )
        )
        payload = {
            "data": {
                "ref_id": "df-dup-success-1",
                "status": "Sukses",
                "rc": "00",
                "sn": "SN-DUP",
            }
        }
        raw_body, signature = self.digiflazz_signature(payload)
        for _ in range(2):
            response = self.client.post(
                "/api/webhook/digiflazz",
                content=raw_body,
                headers={"X-Hub-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 200)
        rows = asyncio.run(
            self.db.db_query(
                "SELECT COUNT(*) FROM notification_outbox WHERE reference_id='df-dup-success-1' AND subject='Topup sukses'"
            )
        )
        self.assertEqual(int(rows[0][0]), 1)

    def test_duplicate_tripay_paid_callback_queues_payment_notification_once(self):
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, payment_status, topup_status, status_updated_at)
                VALUES ('tripay-dup-paid-1', '0811222444', 'target', 'ml_10k', 'UNPAID', 'PENDING_PAYMENT', CURRENT_TIMESTAMP)
                """
            )
        )
        payload = {"merchant_ref": "tripay-dup-paid-1", "reference": "TRIPAY-DUP-PAID", "status": "PAID"}
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(os.environ["TRIPAY_PRIVATE_KEY"].encode(), raw_body, hashlib.sha256).hexdigest()
        for _ in range(2):
            response = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 200)
        rows = asyncio.run(
            self.db.db_query(
                "SELECT COUNT(*) FROM notification_outbox WHERE reference_id='tripay-dup-paid-1' AND subject='Pembayaran diterima'"
            )
        )
        self.assertEqual(int(rows[0][0]), 1)

    def test_duplicate_wallet_refund_credits_once(self):
        token = self.admin_token()
        async def seed():
            await self.db.db_execute("INSERT INTO customer_accounts (id, name, phone, password) VALUES (1, 'Wallet', '08129991111', 'x')")
            await self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, customer_id, target_id, nominal, amount, payment_method,
                    payment_status, topup_status, status_updated_at
                )
                VALUES ('refund-wallet-1', '08129991111', 1, 'target', 'ml_10k', 12000, 'WALLET', 'PAID', 'FAILED', CURRENT_TIMESTAMP)
                """
            )

        asyncio.run(seed())
        payload = {"note": "refund duplicate safety"}
        first = self.client.post("/admin/api/orders/refund-wallet-1/refund", headers={"token": token}, json=payload)
        second = self.client.post("/admin/api/orders/refund-wallet-1/refund", headers={"token": token}, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        rows = asyncio.run(self.db.db_query("SELECT balance FROM wallet_accounts WHERE customer_id=1"))
        ledger_rows = asyncio.run(self.db.db_query("SELECT COUNT(*) FROM wallet_ledger WHERE reference_type='refund' AND reference_id='refund-wallet-1'"))
        self.assertEqual(int(rows[0][0]), 12000)
        self.assertEqual(int(ledger_rows[0][0]), 1)

    def test_admin_rejects_illegal_success_to_processing_transition(self):
        token = self.admin_token()
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (id, phone, target_id, nominal, payment_status, topup_status, sn, status_updated_at)
                VALUES ('admin-illegal-1', '0811', 'target', 'ml_10k', 'PAID', 'SUCCESS', 'SN-VALID', CURRENT_TIMESTAMP)
                """
            )
        )
        response = self.client.put(
            "/admin/api/orders/admin-illegal-1/status",
            headers={"token": token},
            json={"topup_status": "PROCESSING", "note": "illegal downgrade"},
        )
        self.assertEqual(response.status_code, 409)

    def test_admin_cannot_sync_terminal_provider_order(self):
        token = self.admin_token()
        asyncio.run(
            self.db.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, payment_status, topup_status,
                    provider_outcome, status_updated_at
                )
                VALUES ('admin-terminal-sync-1', '0811', 'target', 'ml_10k', 'PAID', 'SUCCESS', 'SUCCESS', CURRENT_TIMESTAMP)
                """
            )
        )
        response = self.client.post(
            "/admin/api/orders/admin-terminal-sync-1/sync-provider",
            headers={"token": token},
        )
        self.assertEqual(response.status_code, 409)

    def test_admin_wallet_adjustment_idempotency_key_prevents_double_adjust(self):
        token = self.admin_token()
        asyncio.run(
            self.db.db_execute(
                "INSERT INTO customer_accounts (id, name, phone, password) VALUES (1, 'Wallet', '08129990000', 'x')"
            )
        )
        payload = {
            "phone": "08129990000",
            "entry_type": "CREDIT",
            "amount": 15000,
            "note": "manual correction",
            "idempotency_key": "admin-adjust-1",
        }
        first = self.client.post("/admin/api/customers/wallet-adjust", headers={"token": token}, json=payload)
        second = self.client.post("/admin/api/customers/wallet-adjust", headers={"token": token}, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        rows = asyncio.run(self.db.db_query("SELECT balance FROM wallet_accounts WHERE customer_id=1"))
        ledger_rows = asyncio.run(self.db.db_query("SELECT COUNT(*) FROM wallet_ledger WHERE reference_id='admin-adjust-1'"))
        self.assertEqual(int(rows[0][0]), 15000)
        self.assertEqual(int(ledger_rows[0][0]), 1)
