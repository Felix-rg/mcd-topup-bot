import hashlib
import hmac
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class CustomerWalletTests(unittest.TestCase):
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
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"
        os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "lixafa123"
        os.environ["TRIPAY_API_KEY"] = "DEV-test"
        os.environ["TRIPAY_PRIVATE_KEY"] = "tripay-private-test"
        os.environ["TRIPAY_MERCHANT_CODE"] = "T1234"

        import importlib

        from app.core import settings as core_settings
        from app.core import database as core_database
        from app import database as legacy_database
        from app.services import provider_settings
        from app.routes import admin_routes, topup_routes

        importlib.reload(core_settings)
        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(provider_settings)
        importlib.reload(admin_routes)
        importlib.reload(topup_routes)

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
        response = self.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def test_customer_wallet_open_payment_creates_and_reuses_tripay_reference(self):
        register = self.client.post(
            "/api/customer/register",
            json={
                "name": "Open Payment Customer",
                "phone": "081234567891",
                "email": "open-payment@test.local",
                "password": "customer123",
            },
        )
        self.assertEqual(register.status_code, 200)
        customer_token = register.json()["token"]

        mocked_create_open_payment = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "uuid": "open-payment-uuid",
                    "payment_name": "QRIS",
                    "pay_code": "",
                    "qr_url": "https://tripay.test/qris.png",
                },
            }
        )
        original_create_open_payment = self.topup_routes.create_open_payment
        self.topup_routes.create_open_payment = mocked_create_open_payment
        try:
            response = self.client.post(
                "/api/customer/wallet/open-payment",
                headers={"customer-token": customer_token},
                json={"method": "QRIS"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertFalse(payload["reused"])
            self.assertEqual(payload["uuid"], "open-payment-uuid")
            self.assertTrue(payload["merchant_ref"].startswith("WALLET-"))
            mocked_create_open_payment.assert_awaited_once()
            self.assertEqual(mocked_create_open_payment.await_args.args[0], "QRIS")
            self.assertEqual(mocked_create_open_payment.await_args.args[1], payload["merchant_ref"])

            reused = self.client.post(
                "/api/customer/wallet/open-payment",
                headers={"customer-token": customer_token},
                json={"method": "QRIS"},
            )
            self.assertEqual(reused.status_code, 200)
            self.assertTrue(reused.json()["reused"])
            self.assertEqual(reused.json()["merchant_ref"], payload["merchant_ref"])
            mocked_create_open_payment.assert_awaited_once()
        finally:
            self.topup_routes.create_open_payment = original_create_open_payment

    def test_public_cancel_only_allows_unpaid_pending_orders(self):
        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_method,
                    payment_status, topup_status, status_updated_at
                )
                VALUES
                    ('cancel-unpaid', '081200000001', 'target-1', 'ml_10k', 10000, 'QRIS', 'UNPAID', 'PENDING_PAYMENT', CURRENT_TIMESTAMP),
                    ('cancel-paid', '081200000002', 'target-2', 'ml_10k', 10000, 'QRIS', 'PAID', 'PROCESSING', CURRENT_TIMESTAMP)
                """
            )
        )

        paid_cancel = self.client.post(
            "/topup/cancel-paid/cancel",
            headers={"X-Order-Access-Token": self.topup_routes._order_access_token("cancel-paid")},
        )
        self.assertEqual(paid_cancel.status_code, 409)

        paid_rows = asyncio.run(
            legacy_database.db_query(
                "SELECT payment_status, topup_status FROM topup WHERE id='cancel-paid'"
            )
        )
        self.assertEqual(paid_rows[0][0], "PAID")
        self.assertEqual(paid_rows[0][1], "PROCESSING")

        unpaid_cancel = self.client.post(
            "/topup/cancel-unpaid/cancel",
            headers={"X-Order-Access-Token": self.topup_routes._order_access_token("cancel-unpaid")},
        )
        self.assertEqual(unpaid_cancel.status_code, 200)

        unpaid_rows = asyncio.run(
            legacy_database.db_query(
                "SELECT payment_status, topup_status FROM topup WHERE id='cancel-unpaid'"
            )
        )
        self.assertEqual(unpaid_rows[0][0], "CANCELED")
        self.assertEqual(unpaid_rows[0][1], "FAILED")

    def test_customer_history_and_tickets_do_not_fall_back_to_matching_phone(self):
        from app import database as legacy_database

        register = self.client.post(
            "/api/customer/register",
            json={
                "name": "Scoped Customer",
                "phone": "08125550001",
                "email": "scoped@test.local",
                "password": "customer123",
            },
        )
        self.assertEqual(register.status_code, 200)
        token = register.json()["token"]
        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_status, topup_status, customer_id
                )
                VALUES ('guest-same-phone-order', '08125550001', 'guest-target', 'ml_10k', 10000, 'UNPAID', 'PENDING_PAYMENT', NULL)
                """
            )
        )
        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO support_tickets (customer_id, phone, subject, message, status, priority)
                VALUES (NULL, '08125550001', 'Guest ticket', 'Must not leak to a new account', 'OPEN', 'NORMAL')
                """
            )
        )

        orders = self.client.get("/api/customer/orders", headers={"customer-token": token})
        tickets = self.client.get("/api/customer/tickets", headers={"customer-token": token})
        self.assertEqual(orders.status_code, 200)
        self.assertEqual(tickets.status_code, 200)
        self.assertEqual(orders.json()["orders"], [])
        self.assertEqual(tickets.json()["tickets"], [])

    def test_public_product_catalog_never_exposes_cost_price(self):
        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO products (sku, provider, name, cost_price, price, active, category)
                VALUES ('public-cost-private', 'Test Provider', 'Public Product', 7500, 10000, 1, 'Games')
                """
            )
        )
        response = self.client.get("/api/products")
        self.assertEqual(response.status_code, 200)
        product = next(
            item
            for item in response.json()["products"]
            if item["sku"] == "public-cost-private"
        )
        self.assertNotIn("cost", product)
        self.assertNotIn("cost_price", product)
        self.assertEqual(product["price"], 10000.0)

    def test_guest_order_status_and_cancel_require_order_access_capability(self):
        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_method,
                    payment_status, topup_status, status_updated_at
                )
                VALUES ('guest-private-1', '081200000099', 'target-private', 'ml_10k', 10000,
                        'QRIS', 'UNPAID', 'PENDING_PAYMENT', CURRENT_TIMESTAMP)
                """
            )
        )

        self.assertEqual(self.client.get("/topup/081200000099").status_code, 404)
        self.assertEqual(self.client.get("/topup/guest-private-1").status_code, 403)
        self.assertEqual(self.client.post("/topup/guest-private-1/cancel").status_code, 403)

        access_header = {"X-Order-Access-Token": self.topup_routes._order_access_token("guest-private-1")}
        status_response = self.client.get("/topup/guest-private-1", headers=access_header)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["target_id"], "target-private")

        cancel_response = self.client.post("/topup/guest-private-1/cancel", headers=access_header)
        self.assertEqual(cancel_response.status_code, 200)

    def test_free_promo_topup_skips_tripay_and_enters_engine_queue(self):
        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO promos (
                    title, code, badge, rule_type, target_scope, target_value,
                    discount_type, discount_value, max_discount, active
                )
                VALUES (
                    'Gratis 100%', 'FREE100', 'Gratis', 'price', 'sku', 'ml_10k',
                    'percent', 100, 0, 1
                )
                """
            )
        )

        mocked_create_invoice = AsyncMock()
        original_create_invoice = self.topup_routes.create_invoice
        self.topup_routes.create_invoice = mocked_create_invoice
        try:
            response = self.client.post(
                "/topup",
                json={
                    "phone": "081234567899",
                    "target_id": "123456789",
                    "nominal": "ml_10k",
                    "method": "QRIS",
                    "promo_code": "FREE100",
                    "nickname": "Free User",
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["free_checkout"])
            self.assertEqual(payload["total"], 0)
            self.assertEqual(payload["payment_method"], "FREE_PROMO")
            mocked_create_invoice.assert_not_awaited()

            rows = asyncio.run(
                legacy_database.db_query(
                    """
                    SELECT payment_status, topup_status, amount, payment_method,
                           payment_fee, promo_code, note
                    FROM topup
                    WHERE id=:id
                    """,
                    {"id": payload["id"]},
                )
            )
            self.assertEqual(rows[0][0], "PAID")
            self.assertEqual(rows[0][1], "PROCESSING")
            self.assertEqual(float(rows[0][2] or 0), 0)
            self.assertEqual(rows[0][3], "FREE_PROMO")
            self.assertEqual(float(rows[0][4] or 0), 0)
            self.assertEqual(rows[0][5], "FREE100")
            self.assertIn("tanpa pembayaran Tripay", rows[0][6])
        finally:
            self.topup_routes.create_invoice = original_create_invoice

    def test_customer_wallet_deposit_invoice_is_credited_by_tripay_callback(self):
        register = self.client.post(
            "/api/customer/register",
            json={
                "name": "Deposit Invoice Customer",
                "phone": "081234567892",
                "email": "deposit-invoice@test.local",
                "password": "customer123",
            },
        )
        self.assertEqual(register.status_code, 200)
        customer_token = register.json()["token"]

        mocked_quote_fee = AsyncMock(return_value=(500, "test"))
        mocked_create_invoice = AsyncMock(
            return_value={
                "reference": "TRIPAY-WALLET-REF",
                "merchant_ref": "filled-by-route",
                "payment_name": "QRIS",
                "checkout_url": "https://tripay.test/checkout",
                "qr_url": "https://tripay.test/qris.png",
                "pay_code": "",
                "instructions": [{"title": "Bayar via QRIS", "steps": ["Scan QR", "Konfirmasi pembayaran"]}],
                "total_fee": 500,
            }
        )
        original_quote_fee = self.topup_routes._quote_payment_fee
        original_create_invoice = self.topup_routes.create_invoice
        self.topup_routes._quote_payment_fee = mocked_quote_fee
        self.topup_routes.create_invoice = mocked_create_invoice
        try:
            response = self.client.post(
                "/api/customer/wallet/open-payment",
                headers={"customer-token": customer_token},
                json={"method": "QRIS", "amount": 20000},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["type"], "invoice")
            self.assertEqual(payload["amount"], 20000)
            self.assertEqual(payload["payment_fee"], 500)
            self.assertEqual(payload["total"], 20500)
            self.assertEqual(payload["invoice_url"], "https://tripay.test/checkout")
            self.assertEqual(payload["payment_instructions"][0]["title"], "Bayar via QRIS")
            mocked_create_invoice.assert_awaited_once()

            callback_payload = {
                "merchant_ref": payload["merchant_ref"],
                "reference": "TRIPAY-WALLET-REF",
                "status": "PAID",
                "amount": 20500,
                "total_fee": 500,
            }
            raw_body = json.dumps(callback_payload, separators=(",", ":")).encode()
            signature = hmac.new(os.environ["TRIPAY_PRIVATE_KEY"].encode(), raw_body, hashlib.sha256).hexdigest()
            callback = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(callback.status_code, 200)

            wallet = self.client.get("/api/customer/wallet", headers={"customer-token": customer_token})
            self.assertEqual(wallet.status_code, 200)
            self.assertEqual(wallet.json()["balance"], 20000)

            duplicate_callback = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(duplicate_callback.status_code, 200)
            wallet_after_duplicate = self.client.get("/api/customer/wallet", headers={"customer-token": customer_token})
            self.assertEqual(wallet_after_duplicate.json()["balance"], 20000)
        finally:
            self.topup_routes._quote_payment_fee = original_quote_fee
            self.topup_routes.create_invoice = original_create_invoice

    def test_ambiguous_wallet_invoice_keeps_callback_matchable(self):
        from app import database as legacy_database

        register = self.client.post(
            "/api/customer/register",
            json={
                "name": "Ambiguous Deposit Customer",
                "phone": "081234567893",
                "email": "ambiguous-deposit@test.local",
                "password": "customer123",
            },
        )
        self.assertEqual(register.status_code, 200)
        customer_token = register.json()["token"]

        original_quote_fee = self.topup_routes._quote_payment_fee
        original_create_invoice = self.topup_routes.create_invoice
        self.topup_routes._quote_payment_fee = AsyncMock(return_value=(500, "test"))
        self.topup_routes.create_invoice = AsyncMock(
            return_value={"_invoice_outcome": "SENT_UNKNOWN"}
        )
        try:
            response = self.client.post(
                "/api/customer/wallet/open-payment",
                headers={"customer-token": customer_token},
                json={"method": "QRIS", "amount": 20000},
            )
            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertTrue(payload["payment_creation_pending"])
            merchant_ref = payload["merchant_ref"]
            before_callback = asyncio.run(
                legacy_database.db_query(
                    "SELECT status, reference FROM customer_wallet_deposits WHERE merchant_ref=:merchant_ref",
                    {"merchant_ref": merchant_ref},
                )
            )
            self.assertEqual(before_callback[0], ("PENDING", None))

            callback_payload = {
                "merchant_ref": merchant_ref,
                "reference": "TRIPAY-WALLET-AMBIGUOUS",
                "status": "PAID",
                "amount": 20500,
                "total_fee": 500,
            }
            raw_body = json.dumps(callback_payload, separators=(",", ":")).encode()
            signature = hmac.new(
                os.environ["TRIPAY_PRIVATE_KEY"].encode(), raw_body, hashlib.sha256
            ).hexdigest()
            callback = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
            self.assertEqual(callback.status_code, 200)
            after_callback = asyncio.run(
                legacy_database.db_query(
                    "SELECT status, reference FROM customer_wallet_deposits WHERE merchant_ref=:merchant_ref",
                    {"merchant_ref": merchant_ref},
                )
            )
            self.assertEqual(after_callback[0], ("CREDITED", "TRIPAY-WALLET-AMBIGUOUS"))
        finally:
            self.topup_routes._quote_payment_fee = original_quote_fee
            self.topup_routes.create_invoice = original_create_invoice

    def test_status_endpoint_returns_embedded_payment_metadata(self):
        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_method, payment_status,
                    topup_status, invoice_url, payment_name, pay_code, pay_url, qr_url, qr_string,
                    tripay_payload, payment_expired_at, status_updated_at
                )
                VALUES (
                    'status-pay-1', '081200000003', 'target-3', 'ml_10k', 12000, 'QRIS', 'UNPAID',
                    'PENDING_PAYMENT', 'https://tripay.test/checkout', 'QRIS', 'PAY-123', '', 'https://tripay.test/qris.png', '000201010212',
                    :payload, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """,
                {
                    "payload": json.dumps(
                        {
                            "instructions": [
                                {"title": "Bayar via QRIS", "steps": ["Buka e-wallet", "Scan QR", "Selesaikan pembayaran"]}
                            ]
                        }
                    )
                },
            )
        )

        response = self.client.get(
            "/topup/status-pay-1",
            headers={"X-Order-Access-Token": self.topup_routes._order_access_token("status-pay-1")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["payment_name"], "QRIS")
        self.assertEqual(payload["pay_code"], "PAY-123")
        self.assertEqual(payload["qr_string"], "000201010212")
        self.assertTrue(payload["can_pay"])
        self.assertEqual(payload["payment_instructions"][0]["title"], "Bayar via QRIS")

    def test_public_active_promos_and_storefront_stats_ignore_expired_promos(self):
        admin_login = self.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        self.assertEqual(admin_login.status_code, 200)
        admin_token = admin_login.json()["token"]

        create_response = self.client.post(
            "/admin/api/promos",
            headers={"token": admin_token},
            json={
                "title": "Promo Expired Website",
                "code": "OLDWEB",
                "description": "Promo website lama",
                "badge": "Old",
                "rule_type": "price",
                "target_scope": "sku",
                "target_value": "old-sku",
                "discount_type": "percent",
                "discount_value": 10,
                "starts_at": "2020-01-01T00:00:00",
                "ends_at": "2020-01-02T00:00:00",
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(create_response.status_code, 200)

        active_promos = self.client.get("/api/promos/active")
        self.assertEqual(active_promos.status_code, 200)
        self.assertEqual(active_promos.json()["promos"], [])

        storefront = self.client.get("/api/storefront/stats")
        self.assertEqual(storefront.status_code, 200)
        self.assertEqual(storefront.json()["active_promos"], 0)

    def test_promo_latest_page_lists_all_active_promos(self):
        admin_login = self.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        self.assertEqual(admin_login.status_code, 200)
        admin_token = admin_login.json()["token"]

        first = self.client.post(
            "/admin/api/promos",
            headers={"token": admin_token},
            json={
                "title": "PROMO MOBILE LEGENDS",
                "code": "ML100",
                "description": "diskon 100%",
                "badge": "Diskon",
                "cta_text": "Buka Detail",
                "cta_url": "https://old-domain.ngrok-free.app/p/promo-mobile-legends",
                "rule_type": "price",
                "target_scope": "sku",
                "target_value": "mlweek",
                "discount_type": "percent",
                "discount_value": 100,
                "max_discount": 50000,
                "starts_at": "2020-01-01T00:00:00",
                "ends_at": "2099-01-01T00:00:00",
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/admin/api/promos",
            headers={"token": admin_token},
            json={
                "title": "PROMO LAUNCHING",
                "code": "LXFA100",
                "description": "Promo pengguna baru",
                "badge": "Launch",
                "cta_text": "Topup Sekarang",
                "cta_url": "game:Free Fire",
                "rule_type": "price",
                "target_scope": "sku",
                "target_value": "ff50",
                "discount_type": "percent",
                "discount_value": 50,
                "max_discount": 25000,
                "starts_at": "2020-01-01T00:00:00",
                "ends_at": "2099-01-01T00:00:00",
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(second.status_code, 200)

        active_promos = self.client.get("/api/promos/active")
        self.assertEqual(active_promos.status_code, 200)
        payload = active_promos.json()["promos"]
        self.assertEqual(len(payload), 2)
        self.assertTrue(any(promo["resolved_cta_url"] == "/p/promo-mobile-legends" for promo in payload))

        promo_page = self.client.get("/promo/latest", follow_redirects=False)
        self.assertEqual(promo_page.status_code, 200)
        self.assertIn("2 Promo Aktif", promo_page.text)
        self.assertIn("PROMO MOBILE LEGENDS", promo_page.text)
        self.assertIn("PROMO LAUNCHING", promo_page.text)
        self.assertIn('href="/p/promo-mobile-legends"', promo_page.text)

    def test_customer_wallet_order_support_and_notifications(self):
        register = self.client.post(
            "/api/customer/register",
            json={
                "name": "Customer Test",
                "phone": "081234567890",
                "email": "customer@test.local",
                "password": "customer123",
            },
        )
        self.assertEqual(register.status_code, 200)
        customer_token = register.json()["token"]

        wallet_before = self.client.get("/api/customer/wallet", headers={"customer-token": customer_token})
        self.assertEqual(wallet_before.status_code, 200)
        self.assertEqual(wallet_before.json()["balance"], 0)

        admin_token = self.admin_token()
        adjust = self.client.post(
            "/admin/api/customers/wallet-adjust",
            headers={"token": admin_token},
            json={
                "phone": "081234567890",
                "amount": 25000,
                "entry_type": "CREDIT",
                "note": "test topup saldo",
            },
        )
        self.assertEqual(adjust.status_code, 200)
        self.assertEqual(adjust.json()["balance"], 25000)

        wallet_order = self.client.post(
            "/topup",
            headers={"customer-token": customer_token},
            json={
                "phone": "081234567890",
                "target_id": "12345678",
                "nominal": "ml_10k",
                "method": "WALLET",
                "nickname": "Tester",
            },
        )
        self.assertEqual(wallet_order.status_code, 200)
        self.assertTrue(wallet_order.json()["wallet_paid"])

        wallet_after = self.client.get("/api/customer/wallet", headers={"customer-token": customer_token})
        self.assertEqual(wallet_after.status_code, 200)
        self.assertEqual(wallet_after.json()["balance"], 15000)

        history = self.client.get("/api/customer/orders", headers={"customer-token": customer_token})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["orders"][0]["payment_method"], "WALLET")
        self.assertEqual(history.json()["orders"][0]["payment_status"], "PAID")
        self.assertIn("product_name", history.json()["orders"][0])

        status = self.client.get(
            f"/topup/{wallet_order.json()['id']}",
            headers={"X-Order-Access-Token": wallet_order.json()["order_access_token"]},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["payment_method"], "WALLET")
        self.assertEqual(status.json()["amount"], 10000)
        self.assertTrue(status.json()["timeline"][1]["done"])

        ticket = self.client.post(
            "/api/support/tickets",
            headers={"customer-token": customer_token},
            json={
                "order_id": wallet_order.json()["id"],
                "subject": "Order test",
                "message": "Mohon cek order test",
            },
        )
        self.assertEqual(ticket.status_code, 200)

        tickets = self.client.get("/api/customer/tickets", headers={"customer-token": customer_token})
        self.assertEqual(tickets.status_code, 200)
        self.assertEqual(tickets.json()["tickets"][0]["subject"], "Order test")

        outbox = self.client.get("/admin/api/notification-outbox", headers={"token": admin_token})
        self.assertEqual(outbox.status_code, 200)
        self.assertGreaterEqual(len(outbox.json()), 2)

        refund = self.client.post(
            f"/admin/api/orders/{wallet_order.json()['id']}/refund",
            headers={"token": admin_token},
            json={"note": "refund test wallet"},
        )
        self.assertEqual(refund.status_code, 200)

        wallet_after_refund = self.client.get("/api/customer/wallet", headers={"customer-token": customer_token})
        self.assertEqual(wallet_after_refund.status_code, 200)
        self.assertEqual(wallet_after_refund.json()["balance"], 25000)


if __name__ == "__main__":
    unittest.main()
