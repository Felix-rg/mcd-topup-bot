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

    async def test_explicit_definitive_failure_marks_failed_without_retry(self) -> None:
        failure_response = {"data": {"status": "Gagal", "rc": "02", "message": "Transaksi Gagal"}}

        provider = AsyncMock(return_value=failure_response)
        with patch("app.engine.kirim_digiflazz", new=provider):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, provider_retry_count, provider_last_error FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "FAILED")
        self.assertEqual(rows[0][1], "FAILED")
        self.assertEqual(rows[0][2], 0)
        self.assertIn("Transaksi Gagal", rows[0][3])
        self.assertEqual(provider.await_count, 1)

    async def test_retryable_not_sent_failure_stays_processing_before_max_retry(self) -> None:
        failure_response = {
            "data": {"message": "provider configuration is temporarily unavailable", "rc": "99"},
            "_provider_outcome": "NOT_SENT",
        }

        with patch("app.engine.kirim_digiflazz", new=AsyncMock(return_value=failure_response)):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, provider_retry_count, provider_last_error FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "PROCESSING")
        self.assertEqual(rows[0][1], "NOT_SENT")
        self.assertEqual(rows[0][2], 1)
        self.assertIn("temporarily unavailable", rows[0][3])

    async def test_retryable_not_sent_failure_becomes_failed_after_max_retry(self) -> None:
        failure_response = {
            "data": {"message": "provider configuration is temporarily unavailable", "rc": "99"},
            "_provider_outcome": "NOT_SENT",
        }

        provider = AsyncMock(return_value=failure_response)
        with patch("app.engine.kirim_digiflazz", new=provider):
            for _ in range(self.engine_module.MAX_PROVIDER_RETRY):
                await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, provider_retry_count FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "FAILED")
        self.assertEqual(rows[0][1], "NOT_SENT")
        self.assertEqual(rows[0][2], self.engine_module.MAX_PROVIDER_RETRY)
        self.assertEqual(provider.await_count, self.engine_module.MAX_PROVIDER_RETRY)

    async def test_pending_provider_failed_status_marks_failed(self) -> None:
        await self.legacy_database.db_execute(
            "UPDATE topup SET topup_status='PENDING_PROVIDER', provider_last_check_at=NULL WHERE id=:id",
            {"id": "order-1"},
        )
        failure_response = {"data": {"status": "Gagal", "rc": "02", "message": "final failure"}}

        with patch("app.engine.cek_status_digiflazz", new=AsyncMock(return_value=failure_response)):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_last_error FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "FAILED")
        self.assertIn("final failure", rows[0][1])

    async def test_provider_success_is_terminal_and_does_not_retry(self) -> None:
        response = {"data": {"status": "Sukses", "rc": "00", "sn": "SN-1"}}
        provider = AsyncMock(return_value=response)

        with patch("app.engine.kirim_digiflazz", new=provider):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, provider_retry_count, sn FROM topup WHERE id=:id",
            {"id": "order-1"},
        )
        self.assertEqual(rows[0][0], "SUCCESS")
        self.assertEqual(rows[0][1], "SUCCESS")
        self.assertEqual(rows[0][2], 0)
        self.assertEqual(rows[0][3], "SN-1")
        self.assertEqual(provider.await_count, 1)

    async def test_pending_provider_is_polled_without_second_send(self) -> None:
        pending_response = {"data": {"status": "Pending", "rc": "03", "message": "Transaksi Pending"}}
        send_provider = AsyncMock(return_value=pending_response)
        status_provider = AsyncMock(return_value=pending_response)

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()
            await self.legacy_database.db_execute(
                "UPDATE topup SET provider_last_check_at=NULL WHERE id=:id", {"id": "order-1"}
            )
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "PENDING_PROVIDER")
        self.assertEqual(rows[0][1], "PENDING_PROVIDER")
        self.assertEqual(send_provider.await_count, 1)
        self.assertEqual(status_provider.await_count, 1)

    async def test_timeout_unknown_reconciles_to_success_without_second_send(self) -> None:
        timeout_response = {"data": {"status": "Gagal", "rc": "01", "message": "Timeout"}}
        success_response = {"data": {"status": "Sukses", "rc": "00", "sn": "SN-RECOVERED"}}
        send_provider = AsyncMock(return_value=timeout_response)
        status_provider = AsyncMock(return_value=success_response)

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()
            await self.legacy_database.db_execute(
                "UPDATE topup SET provider_last_check_at=NULL WHERE id=:id", {"id": "order-1"}
            )
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, sn FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "SUCCESS")
        self.assertEqual(rows[0][1], "SUCCESS")
        self.assertEqual(rows[0][2], "SN-RECOVERED")
        self.assertEqual(send_provider.await_count, 1)
        self.assertEqual(status_provider.await_count, 1)

    async def test_timeout_unknown_reconciles_to_pending_without_second_send(self) -> None:
        timeout_response = {"data": {"status": "Gagal", "rc": "01", "message": "Timeout"}}
        pending_response = {"data": {"status": "Pending", "rc": "03", "message": "Transaksi Pending"}}
        send_provider = AsyncMock(return_value=timeout_response)
        status_provider = AsyncMock(return_value=pending_response)

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()
            await self.legacy_database.db_execute(
                "UPDATE topup SET provider_last_check_at=NULL WHERE id=:id", {"id": "order-1"}
            )
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "PENDING_PROVIDER")
        self.assertEqual(rows[0][1], "PENDING_PROVIDER")
        self.assertEqual(send_provider.await_count, 1)
        self.assertEqual(status_provider.await_count, 1)

    async def test_timeout_unknown_reconciles_to_definitive_failure_without_second_send(self) -> None:
        timeout_response = {"data": {"status": "Gagal", "rc": "01", "message": "Timeout"}}
        failed_response = {"data": {"status": "Gagal", "rc": "02", "message": "Transaksi Gagal"}}
        send_provider = AsyncMock(return_value=timeout_response)
        status_provider = AsyncMock(return_value=failed_response)

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()
            await self.legacy_database.db_execute(
                "UPDATE topup SET provider_last_check_at=NULL WHERE id=:id", {"id": "order-1"}
            )
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "FAILED")
        self.assertEqual(rows[0][1], "FAILED")
        self.assertEqual(send_provider.await_count, 1)
        self.assertEqual(status_provider.await_count, 1)

    async def test_expired_lease_after_unknown_outcome_cannot_trigger_second_send(self) -> None:
        timeout_response = {"data": {"status": "Gagal", "rc": "01", "message": "Timeout"}}
        still_unknown = {"data": {"status": "Gagal", "rc": "01", "message": "Timeout"}}
        send_provider = AsyncMock(return_value=timeout_response)
        status_provider = AsyncMock(return_value=still_unknown)

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()
            await self.legacy_database.db_execute(
                """
                UPDATE topup
                SET provider_claim_id='expired-lease', provider_claim_expires_at='2000-01-01 00:00:00',
                    provider_last_check_at=NULL
                WHERE id=:id
                """,
                {"id": "order-1"},
            )
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "PROCESSING")
        self.assertEqual(rows[0][1], "SENT_UNKNOWN")
        self.assertEqual(send_provider.await_count, 1)
        self.assertEqual(status_provider.await_count, 1)

    async def test_unknown_prepaid_older_than_ninety_days_is_not_checked_or_resent(self) -> None:
        await self.legacy_database.db_execute(
            """
            UPDATE topup
            SET provider_outcome='SENT_UNKNOWN', topup_status='PROCESSING', provider_last_check_at=NULL,
                created_at='2000-01-01 00:00:00'
            WHERE id=:id
            """,
            {"id": "order-1"},
        )
        send_provider = AsyncMock()
        status_provider = AsyncMock()

        with patch("app.engine.kirim_digiflazz", new=send_provider), patch(
            "app.engine.cek_status_digiflazz", new=status_provider
        ):
            await self.engine_module.polling_status_engine()

        rows = await self.legacy_database.db_query(
            "SELECT topup_status, provider_outcome, provider_last_error FROM topup WHERE id=:id", {"id": "order-1"}
        )
        self.assertEqual(rows[0][0], "PROCESSING")
        self.assertEqual(rows[0][1], "SENT_UNKNOWN")
        self.assertIn("90 hari", rows[0][2])
        self.assertEqual(send_provider.await_count, 0)
        self.assertEqual(status_provider.await_count, 0)

    async def test_open_payment_reconcile_credits_wallet(self) -> None:
        await self.legacy_database.db_execute(
            """
            INSERT INTO customer_open_payments (customer_id, uuid, merchant_ref, method, active)
            VALUES (1, 'open-payment-uuid', 'WALLET-1', 'QRIS', 1)
            """
        )
        response = {
            "success": True,
            "data": [
                {
                    "reference": "OP-TRX-1",
                    "merchant_ref": "WALLET-1",
                    "status": "PAID",
                    "amount": 15000,
                }
            ],
        }

        with patch("app.engine.list_open_payment_transactions", new=AsyncMock(return_value=response)):
            await self.engine_module.reconcile_open_payment_engine()

        balance_rows = await self.legacy_database.db_query(
            "SELECT balance FROM wallet_accounts WHERE customer_id=1"
        )
        self.assertEqual(float(balance_rows[0][0]), 15000)

        deposit_rows = await self.legacy_database.db_query(
            "SELECT status FROM customer_wallet_deposits WHERE reference='OP-TRX-1'"
        )
        self.assertEqual(deposit_rows[0][0], "CREDITED")

    async def test_wallet_deposit_reconcile_credits_pending_invoice_amount(self) -> None:
        await self.legacy_database.db_execute(
            """
            INSERT INTO customer_wallet_deposits (
                customer_id, provider, reference, merchant_ref, amount, fee, status
            )
            VALUES (1, 'tripay', 'TRIPAY-DEP-1', 'WALLETDEP-1', 20000, 500, 'PENDING')
            """
        )
        response = {
            "success": True,
            "data": {
                "reference": "TRIPAY-DEP-1",
                "merchant_ref": "WALLETDEP-1",
                "status": "PAID",
                "amount": 20500,
                "total_fee": 500,
            },
        }

        with patch("app.engine.check_transaction_status", new=AsyncMock(return_value=response)):
            await self.engine_module.reconcile_wallet_deposit_engine()

        balance_rows = await self.legacy_database.db_query(
            "SELECT balance FROM wallet_accounts WHERE customer_id=1"
        )
        self.assertEqual(float(balance_rows[0][0]), 20000)

        deposit_rows = await self.legacy_database.db_query(
            "SELECT status FROM customer_wallet_deposits WHERE reference='TRIPAY-DEP-1'"
        )
        self.assertEqual(deposit_rows[0][0], "CREDITED")

    async def test_ambiguous_invoice_reconciles_by_merchant_reference(self) -> None:
        await self.legacy_database.db_execute(
            """
            INSERT INTO topup (
                id, phone, nominal, amount, payment_status, topup_status,
                payment_creation_outcome, payment_reference
            )
            VALUES ('invoice-unknown-1', '08123456789', 'ml_10k', 12000, 'UNPAID', 'PENDING_PAYMENT', 'SENT_UNKNOWN', NULL)
            """
        )
        merchant_lookup = AsyncMock(
            return_value={
                "success": True,
                "data": [
                    {
                        "merchant_ref": "invoice-unknown-1",
                        "reference": "TRIPAY-UNKNOWN-1",
                        "status": "PAID",
                        "amount": 12000,
                    }
                ],
            }
        )
        direct_lookup = AsyncMock()

        with (
            patch("app.engine.list_merchant_transactions", new=merchant_lookup),
            patch("app.engine.check_transaction_status", new=direct_lookup),
        ):
            await self.engine_module.reconcile_payment_engine()

        merchant_lookup.assert_awaited_once_with(
            merchant_ref="invoice-unknown-1",
            page=1,
            per_page=10,
        )
        direct_lookup.assert_not_awaited()
        rows = await self.legacy_database.db_query(
            "SELECT payment_status, topup_status, payment_reference, payment_creation_outcome FROM topup WHERE id='invoice-unknown-1'"
        )
        self.assertEqual(rows[0], ("PAID", "PROCESSING", "TRIPAY-UNKNOWN-1", "SUCCESS"))

    async def test_cancelled_invoice_paid_late_is_queued_for_manual_refund(self) -> None:
        await self.legacy_database.db_execute(
            """
            INSERT INTO topup (
                id, phone, nominal, amount, payment_reference, payment_status,
                topup_status, payment_expired_at
            )
            VALUES (
                'invoice-cancelled-late-paid-1', '08123456789', 'ml_10k', 12000,
                'TRIPAY-CANCELLED-LATE-1', 'CANCELED', 'FAILED', '2000-01-01 00:00:00'
            )
            """
        )
        provider = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "reference": "TRIPAY-CANCELLED-LATE-1",
                    "merchant_ref": "invoice-cancelled-late-paid-1",
                    "status": "PAID",
                    "amount": 12000,
                },
            }
        )

        with patch("app.engine.check_transaction_status", new=provider):
            await self.engine_module.reconcile_payment_engine()

        provider.assert_awaited_once_with("TRIPAY-CANCELLED-LATE-1")
        rows = await self.legacy_database.db_query(
            """
            SELECT payment_status, topup_status, refund_status, payment_reference
            FROM topup
            WHERE id='invoice-cancelled-late-paid-1'
            """
        )
        self.assertEqual(
            rows[0],
            ("CANCELED", "FAILED", "PAYMENT_RECEIVED_AFTER_CANCEL", "TRIPAY-CANCELLED-LATE-1"),
        )
        notifications = await self.legacy_database.db_query(
            "SELECT COUNT(*) FROM notification_outbox WHERE reference_id='invoice-cancelled-late-paid-1'"
        )
        self.assertEqual(int(notifications[0][0]), 0)

    async def test_ambiguous_wallet_deposit_reconciles_by_merchant_reference(self) -> None:
        await self.legacy_database.db_execute(
            """
            INSERT INTO customer_wallet_deposits (
                customer_id, provider, reference, merchant_ref, amount, fee, status
            )
            VALUES (1, 'tripay', NULL, 'WALLETDEP-UNKNOWN-1', 20000, 500, 'PENDING')
            """
        )
        merchant_lookup = AsyncMock(
            return_value={
                "success": True,
                "data": [
                    {
                        "merchant_ref": "WALLETDEP-UNKNOWN-1",
                        "reference": "TRIPAY-DEP-UNKNOWN-1",
                        "status": "PAID",
                        "amount": 20500,
                        "total_fee": 500,
                    }
                ],
            }
        )
        direct_lookup = AsyncMock()

        with (
            patch("app.engine.list_merchant_transactions", new=merchant_lookup),
            patch("app.engine.check_transaction_status", new=direct_lookup),
        ):
            await self.engine_module.reconcile_wallet_deposit_engine()

        merchant_lookup.assert_awaited_once_with(
            merchant_ref="WALLETDEP-UNKNOWN-1",
            page=1,
            per_page=10,
        )
        direct_lookup.assert_not_awaited()
        rows = await self.legacy_database.db_query(
            "SELECT status, reference FROM customer_wallet_deposits WHERE merchant_ref='WALLETDEP-UNKNOWN-1'"
        )
        self.assertEqual(rows[0], ("CREDITED", "TRIPAY-DEP-UNKNOWN-1"))


if __name__ == "__main__":
    unittest.main()
