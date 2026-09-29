import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PromotionCheckoutFlowTests(unittest.TestCase):
    """Customer quote/checkout tests that never touch the project database."""

    @classmethod
    def setUpClass(cls):
        cls.temp_db = tempfile.NamedTemporaryFile(suffix="-promo-checkout.db", delete=False)
        cls.temp_db.close()
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        database_path = Path(cls.temp_db.name).resolve()
        project_database_path = (Path(__file__).resolve().parents[1] / "lixafa.db").resolve()
        if database_path == project_database_path:
            raise AssertionError("Checkout promo tests may not use lixafa.db")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"

        import importlib

        from app.core import database as core_database
        from app import database as legacy_database
        from app.promotions import service as promotion_service
        from app.routes import topup_routes

        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(promotion_service)
        importlib.reload(topup_routes)

        cls.core_database = core_database
        cls.database = legacy_database
        cls.promotion_service = promotion_service
        cls.topup_routes = topup_routes

        asyncio.run(legacy_database.init_db())
        cls.app = FastAPI()
        cls.app.include_router(topup_routes.router)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        asyncio.run(cls.core_database.get_engine().dispose())
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url
        if os.path.exists(cls.temp_db.name):
            os.remove(cls.temp_db.name)

        # Restore the process-wide modules after disposing the temporary engine
        # so later test modules cannot inherit a deleted SQLite database.
        import importlib

        from app.core import database as core_database
        from app import database as legacy_database
        from app.promotions import service as promotion_service
        from app.routes import topup_routes

        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(promotion_service)
        importlib.reload(topup_routes)

    def setUp(self):
        for statement in (
            "DELETE FROM promotion_redemptions",
            "DELETE FROM promotion_customer_targets",
            "DELETE FROM promotion_targets",
            "DELETE FROM promos",
            "DELETE FROM notification_outbox",
            "DELETE FROM topup",
        ):
            asyncio.run(self.database.db_execute(statement))

    def _query(self, query, params=None):
        return asyncio.run(self.database.db_query(query, params or {}))

    def _seed_voucher(self):
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promos (
                    title, internal_code, code, promo_type, lifecycle_status,
                    rules_version, rule_type, target_scope, calculation_type,
                    discount_type, discount_value, minimum_transaction,
                    usage_limit, quota_daily, customer_segment, timezone,
                    stackable, exclusive, max_promotions_per_order,
                    show_on_website, active, legacy_compatible
                )
                VALUES (
                    'Voucher SAVE2000', 'CHECKOUT-SAVE2000', 'SAVE2000',
                    'voucher', 'active', 'v2', 'price', 'all', 'fixed',
                    'fixed', 2000, 0, 50, 50, 'all', 'Asia/Jakarta',
                    1, 0, 2, 1, 1, 0
                )
                """
            )
        )
        return int(self._query("SELECT id FROM promos WHERE code='SAVE2000'")[0][0])

    def _seed_free_voucher(self):
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promos (
                    title, internal_code, code, promo_type, lifecycle_status,
                    rules_version, rule_type, target_scope, calculation_type,
                    discount_type, discount_value, customer_segment, timezone,
                    stackable, exclusive, max_promotions_per_order,
                    show_on_website, active, legacy_compatible
                )
                VALUES (
                    'Voucher FREE100', 'CHECKOUT-FREE100', 'FREE100',
                    'voucher', 'active', 'v2', 'price', 'all', 'percent',
                    'percent', 100, 'all', 'Asia/Jakarta',
                    1, 0, 2, 1, 1, 0
                )
                """
            )
        )
        return int(self._query("SELECT id FROM promos WHERE code='FREE100'")[0][0])

    def _seed_ffdana(self):
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO products (
                    sku, provider, name, cost_price, price, active, category,
                    product_type, brand, provider_type, buyer_product_status,
                    seller_product_status, stock, unlimited_stock, multi
                )
                VALUES (
                    'ff50', 'FREE FIRE', 'Free Fire 50 Diamond', 6180, 7416, 1, 'Games',
                    'prepaid', 'FREE FIRE', 'Umum', 1, 1, 0, 1, 1
                )
                ON CONFLICT(sku) DO UPDATE SET
                    provider=excluded.provider,
                    name=excluded.name,
                    cost_price=excluded.cost_price,
                    price=excluded.price,
                    active=excluded.active,
                    category=excluded.category,
                    product_type=excluded.product_type,
                    brand=excluded.brand,
                    provider_type=excluded.provider_type,
                    buyer_product_status=excluded.buyer_product_status,
                    seller_product_status=excluded.seller_product_status,
                    stock=excluded.stock,
                    unlimited_stock=excluded.unlimited_stock,
                    multi=excluded.multi
                """
            )
        )
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promotion_target_catalog (target_type, target_key, label, active)
                VALUES ('sku', 'ff50', 'Free Fire 50 Diamond', 1)
                ON CONFLICT(target_type, target_key) DO UPDATE SET
                    label=excluded.label,
                    active=excluded.active
                """
            )
        )
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promos (
                    title, internal_code, code, promo_type, lifecycle_status,
                    rules_version, rule_type, target_scope, target_value,
                    calculation_type, discount_type, discount_value, max_discount,
                    minimum_transaction, usage_limit, quota_daily, budget_limit,
                    max_per_customer, max_per_customer_daily, max_per_phone,
                    max_per_target, customer_segment, timezone, starts_at, ends_at,
                    active_days, payment_methods, stackable, exclusive,
                    max_promotions_per_order, show_on_website, active, legacy_compatible
                )
                VALUES (
                    'KHUSUS PENGGUNA DANA', 'TEST-FFDANA', 'FFDANA',
                    'voucher', 'active', 'v2', 'price', 'sku', 'ff50',
                    'percent', 'percent', 100, 50000, 0, 100, 0, 50000,
                    1, 0, 1, 2, 'all', 'Asia/Jakarta',
                    '2020-01-01T00:00:00', '2099-12-31T23:59:59',
                    '[0,1,2,3,4,5,6]', '["DANA"]', 0, 0, 1, 1, 1, 0
                )
                """
            )
        )
        promo_id = int(self._query("SELECT id FROM promos WHERE code='FFDANA'")[0][0])
        target_option_id = int(
            self._query(
                "SELECT id FROM promotion_target_catalog WHERE target_type='sku' AND target_key='ff50'"
            )[0][0]
        )
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promotion_targets (promo_id, target_option_id, excluded)
                VALUES (:promo_id, :target_option_id, 0)
                """,
                {"promo_id": promo_id, "target_option_id": target_option_id},
            )
        )
        return promo_id

    def _create_member(self, phone="081299900001", *, created_at=None):
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO customer_accounts (name, phone, password, active)
                VALUES ('Promo Member', :phone, 'unused-test-password', 1)
                ON CONFLICT(phone) DO UPDATE SET name=excluded.name, active=1
                """,
                {"phone": phone},
            )
        )
        customer_id = int(self._query("SELECT id FROM customer_accounts WHERE phone=:phone", {"phone": phone})[0][0])
        if created_at is not None:
            asyncio.run(
                self.database.db_execute(
                    "UPDATE customer_accounts SET created_at=:created_at WHERE id=:customer_id",
                    {"created_at": created_at, "customer_id": customer_id},
                )
            )
        token = self.topup_routes.create_access_token(
            {"customer_id": customer_id, "sub": phone, "role": "customer"}
        )
        return customer_id, token

    def _set_customer_active(self, customer_id, active):
        asyncio.run(
            self.database.db_execute(
                "UPDATE customer_accounts SET active=:active WHERE id=:customer_id",
                {"active": int(bool(active)), "customer_id": int(customer_id)},
            )
        )

    def _set_ffdana_segment(self, segment):
        """Isolate customer-segment behavior from unrelated usage-limit rules."""

        asyncio.run(
            self.database.db_execute(
                """
                UPDATE promos
                SET customer_segment=:segment,
                    max_per_customer=0,
                    max_per_customer_daily=0,
                    max_per_phone=0,
                    max_per_target=0
                WHERE code='FFDANA'
                """,
                {"segment": segment},
            )
        )

    def _set_ffdana_eligibility(self, rules):
        """Persist canonical rules while disabling unrelated identity limits."""

        asyncio.run(
            self.database.db_execute(
                """
                UPDATE promos
                SET eligibility_rules=:rules,
                    customer_segment='all',
                    max_per_customer=0,
                    max_per_customer_daily=0,
                    max_per_phone=0,
                    max_per_target=0
                WHERE code='FFDANA'
                """,
                {"rules": json.dumps(rules, separators=(",", ":"))},
            )
        )

    @staticmethod
    def _eligibility(*conditions, operator="all"):
        return {
            "version": 1,
            "operator": operator,
            "conditions": list(conditions),
        }

    def _quote_ffdana(self, *, phone=None, target_id="segment-target", token=None):
        payload = {
            "sku": "ff50",
            "method": "DANA",
            "promo_code": "FFDANA",
        }
        if phone is not None:
            payload["phone"] = phone
        if target_id is not None:
            payload["target_id"] = target_id
        headers = {"customer-token": token} if token else None
        return self.client.post("/api/promos/quote", headers=headers, json=payload)

    def _seed_history_order(
        self,
        order_id,
        *,
        phone=None,
        customer_id=None,
        topup_status="SUCCESS",
        payment_status="PAID",
        amount=10_000,
        created_at=None,
    ):
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, customer_id, target_id, nominal,
                    price, amount, payment_status, topup_status, created_at
                ) VALUES (
                    :id, :phone, :customer_id, 'history-target', 'ff50',
                    :amount, :amount, :payment_status, :topup_status,
                    COALESCE(:created_at, CURRENT_TIMESTAMP)
                )
                """,
                {
                    "id": order_id,
                    "phone": phone,
                    "customer_id": customer_id,
                    "payment_status": payment_status,
                    "topup_status": topup_status,
                    "amount": amount,
                    "created_at": created_at,
                },
            )
        )

    def _post_topup(
        self,
        *,
        promo_code=None,
        nominal="ml_10k",
        method="QRIS",
        phone="081234567899",
        target_id="customer-game-123",
        token=None,
        extra=None,
    ):
        payload = {
            "phone": phone,
            "target_id": target_id,
            "nominal": nominal,
            "method": method,
            "nickname": "Promo Checkout Test",
        }
        if promo_code:
            payload["promo_code"] = promo_code
        if extra:
            payload.update(extra)
        headers = {"customer-token": token} if token else None
        return self.client.post("/topup", headers=headers, json=payload)

    @staticmethod
    def _invoice_result():
        return {
            "reference": "TRIPAY-CHECKOUT-TEST",
            "checkout_url": "https://tripay.test/checkout",
            "payment_name": "QRIS Test",
            "pay_code": "",
            "qr_url": "https://tripay.test/qr.png",
        }

    def test_ffdana_guest_and_member_receive_same_canonical_free_quote(self):
        self._seed_ffdana()
        _, member_token = self._create_member()

        guest = self.client.post(
            "/api/promos/quote",
            json={
                "sku": " FF50 ",
                "method": " dana ",
                "promo_code": " ffdana ",
                "phone": "081299900002",
                "target_id": " Guest-Target ",
            },
        )
        member = self.client.post(
            "/api/promos/quote",
            headers={"customer-token": member_token},
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": "081299900001",
                "target_id": "member-target",
            },
        )

        self.assertEqual(guest.status_code, 200, guest.text)
        self.assertEqual(member.status_code, 200, member.text)
        for payload in (guest.json(), member.json()):
            self.assertTrue(payload["code_valid"])
            self.assertEqual(payload["sku"], "ff50")
            self.assertEqual(payload["requested_code"], "FFDANA")
            self.assertEqual(payload["base_price"], 7416)
            self.assertEqual(payload["discount_amount"], 7416)
            self.assertEqual(payload["final_price"], 0)
            self.assertEqual(payload["method"], "FREE_PROMO")
            self.assertEqual(payload["payment_fee"], 0)
            self.assertEqual(payload["total"], 0)
            self.assertTrue(payload["free_checkout"])
            self.assertNotIn("rejections", payload)

    def test_ffdana_missing_identity_fields_have_specific_reason_codes(self):
        self._seed_ffdana()

        no_phone = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "target_id": "target-present",
            },
        )
        self.assertEqual(no_phone.status_code, 400, no_phone.text)
        self.assertEqual(no_phone.json()["reason_code"], "PHONE_REQUIRED")

        no_target = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": "081299900003",
            },
        )
        self.assertEqual(no_target.status_code, 400, no_target.text)
        self.assertEqual(no_target.json()["reason_code"], "TARGET_ID_REQUIRED")

    def test_member_and_guest_only_segments_use_backend_account_status(self):
        self._seed_ffdana()
        member_id, member_token = self._create_member("081299900004")
        self._set_ffdana_segment("members_only")

        guest = self._quote_ffdana(phone="081299900004")
        self.assertEqual(guest.status_code, 400, guest.text)
        self.assertEqual(
            guest.json(),
            {
                "valid": False,
                "reason_code": "MEMBER_LOGIN_REQUIRED",
                "message": "Masuk ke akun untuk menggunakan promo ini.",
            },
        )

        guest_checkout = self._post_topup(
            promo_code="FFDANA",
            nominal="ff50",
            method="DANA",
            phone="081299900004",
            target_id="segment-target",
        )
        self.assertEqual(guest_checkout.status_code, 400, guest_checkout.text)
        self.assertEqual(
            guest_checkout.json(),
            {
                "valid": False,
                "reason_code": "MEMBER_LOGIN_REQUIRED",
                "message": "Masuk ke akun untuk menggunakan promo ini.",
            },
        )

        member = self._quote_ffdana(phone="081299900004", token=member_token)
        self.assertEqual(member.status_code, 200, member.text)

        self._set_customer_active(member_id, False)
        inactive_member = self._quote_ffdana(phone="081299900004", token=member_token)
        self.assertEqual(inactive_member.status_code, 400, inactive_member.text)
        self.assertEqual(
            inactive_member.json(),
            {
                "valid": False,
                "reason_code": "ACCOUNT_INACTIVE",
                "message": "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
            },
        )

        # An inactive authenticated account is not silently downgraded to guest.
        self._set_ffdana_segment("guests_only")
        inactive_guest_only = self._quote_ffdana(
            phone="081299900004",
            token=member_token,
        )
        self.assertEqual(inactive_guest_only.status_code, 400, inactive_guest_only.text)
        self.assertEqual(inactive_guest_only.json()["reason_code"], "ACCOUNT_INACTIVE")

        self._set_customer_active(member_id, True)
        member_rejected = self._quote_ffdana(phone="081299900004", token=member_token)
        self.assertEqual(member_rejected.status_code, 400, member_rejected.text)
        self.assertEqual(
            member_rejected.json(),
            {
                "valid": False,
                "reason_code": "GUEST_ONLY_PROMO",
                "message": "Promo ini hanya berlaku untuk checkout tanpa akun.",
            },
        )
        self.assertEqual(self._quote_ffdana(phone="081299900005").status_code, 200)

    def test_atomic_persist_rechecks_account_active_after_initial_quote(self):
        self._seed_ffdana()
        member_id, _ = self._create_member("081299900019")
        self._set_ffdana_segment("members_only")
        stale_identity = self.topup_routes.PromoIdentity(
            customer_id=member_id,
            phone="081299900019",
            target_id="atomic-segment-target",
            is_authenticated=True,
            account_active=True,
        )
        product = {
            "sku": "ff50",
            "provider": "Free Fire",
            "name": "Free Fire 50 Diamond",
            "price": 7416,
            "category": "Games",
        }
        expected_quote = asyncio.run(
            self.promotion_service.quote_product(
                product,
                promo_code="FFDANA",
                payment_method="DANA",
                identity=stale_identity,
            )
        )
        self.assertTrue(expected_quote.code_valid)
        self.assertTrue(expected_quote.applied)

        # Simulate deactivation after the public quote but before atomic persist.
        self._set_customer_active(member_id, False)
        persist_order = AsyncMock()
        with self.assertRaises(self.promotion_service.PromotionCodeError) as raised:
            asyncio.run(
                self.promotion_service.persist_order_with_promotions(
                    order_id="atomic-account-deactivation",
                    product=product,
                    expected_quote=expected_quote,
                    persist_order=persist_order,
                    promo_code="FFDANA",
                    payment_method="DANA",
                    identity=stale_identity,
                )
            )
        self.assertEqual(raised.exception.reason, "account_inactive")
        self.assertEqual(
            raised.exception.detail,
            "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
        )
        persist_order.assert_not_awaited()
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM promotion_redemptions WHERE order_id='atomic-account-deactivation'"
            )[0][0],
            0,
        )

    def test_postgres_account_refresh_uses_row_lock(self):
        connection = MagicMock()
        connection.dialect.name = "postgresql"
        result = MagicMock()
        result.first.return_value = ("081299900018", 1)
        connection.execute = AsyncMock(return_value=result)
        identity = self.topup_routes.PromoIdentity(
            customer_id=918,
            phone="081299900018",
            is_authenticated=True,
            account_active=True,
        )

        refreshed = asyncio.run(
            self.promotion_service._refresh_authenticated_identity(connection, identity)
        )
        statement = str(connection.execute.await_args.args[0]).upper()
        self.assertIn("FOR UPDATE", statement)
        self.assertTrue(refreshed.account_active)

    def test_new_and_existing_segments_use_authoritative_success_history(self):
        self._seed_ffdana()
        member_id, member_token = self._create_member("081299900020")
        success_guest_phone = "081299900021"
        failed_guest_phone = "081299900022"
        fresh_guest_phone = "081299900023"
        self._seed_history_order(
            "segment-success-member",
            phone="081299900099",
            customer_id=member_id,
        )
        self._seed_history_order(
            "segment-success-guest",
            phone=success_guest_phone,
        )
        self._seed_history_order(
            "segment-failed-guest",
            phone=failed_guest_phone,
            topup_status="FAILED",
        )

        self._set_ffdana_segment("new")
        self.assertEqual(self._quote_ffdana(phone=fresh_guest_phone).status_code, 200)
        self.assertEqual(self._quote_ffdana(phone="+6281299900022").status_code, 200)

        returning_guest = self._quote_ffdana(phone="6281299900021")
        self.assertEqual(returning_guest.status_code, 400, returning_guest.text)
        self.assertEqual(
            returning_guest.json(),
            {
                "valid": False,
                "reason_code": "FIRST_PURCHASE_ONLY",
                "message": "Promo ini hanya berlaku untuk pembeli pertama.",
            },
        )
        returning_member = self._quote_ffdana(
            phone="081200000001",
            token=member_token,
        )
        self.assertEqual(returning_member.status_code, 400, returning_member.text)
        self.assertEqual(returning_member.json()["reason_code"], "FIRST_PURCHASE_ONLY")

        missing_new_identity = self._quote_ffdana(phone=None)
        self.assertEqual(missing_new_identity.status_code, 400, missing_new_identity.text)
        self.assertEqual(missing_new_identity.json()["reason_code"], "CUSTOMER_IDENTITY_REQUIRED")

        self._set_ffdana_segment("existing")
        self.assertEqual(self._quote_ffdana(phone="+6281299900021").status_code, 200)
        self.assertEqual(
            self._quote_ffdana(phone="081200000002", token=member_token).status_code,
            200,
        )

        new_guest = self._quote_ffdana(phone=fresh_guest_phone)
        self.assertEqual(new_guest.status_code, 400, new_guest.text)
        self.assertEqual(
            new_guest.json(),
            {
                "valid": False,
                "reason_code": "EXISTING_CUSTOMER_ONLY",
                "message": "Promo ini hanya berlaku untuk pelanggan yang sudah pernah bertransaksi.",
            },
        )
        failed_is_not_success = self._quote_ffdana(phone="6281299900022")
        self.assertEqual(failed_is_not_success.status_code, 400, failed_is_not_success.text)
        self.assertEqual(failed_is_not_success.json()["reason_code"], "EXISTING_CUSTOMER_ONLY")

        missing_existing_identity = self._quote_ffdana(phone=None)
        self.assertEqual(missing_existing_identity.status_code, 400, missing_existing_identity.text)
        self.assertEqual(missing_existing_identity.json()["reason_code"], "CUSTOMER_IDENTITY_REQUIRED")

    def test_member_new_rules_use_authoritative_account_age_and_authentication(self):
        self._seed_ffdana()
        self._set_ffdana_eligibility(
            self._eligibility(
                {
                    "field": "authentication_status",
                    "operator": "equals",
                    "value": "member",
                },
                {
                    "field": "account_status",
                    "operator": "equals",
                    "value": "active",
                },
                {
                    "field": "account_age_days",
                    "operator": "less_than_or_equal",
                    "value": 7,
                },
            )
        )
        now = datetime.now(timezone.utc)
        _, fresh_token = self._create_member(
            "081299901101",
            created_at=(now - timedelta(days=3)).isoformat(),
        )
        _, old_token = self._create_member(
            "081299901102",
            created_at=(now - timedelta(days=10)).isoformat(),
        )

        fresh = self._quote_ffdana(phone="081299901101", token=fresh_token)
        old = self._quote_ffdana(phone="081299901102", token=old_token)
        guest = self._quote_ffdana(phone="081299901103")

        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertEqual(old.status_code, 400, old.text)
        self.assertEqual(old.json()["reason_code"], "ACCOUNT_TOO_OLD")
        self.assertEqual(guest.status_code, 400, guest.text)
        self.assertEqual(guest.json()["reason_code"], "MEMBER_LOGIN_REQUIRED")

    def test_loyal_and_winback_rules_use_authoritative_success_aggregates(self):
        self._seed_ffdana()
        self._set_ffdana_eligibility(
            self._eligibility(
                {
                    "field": "successful_order_count",
                    "operator": "greater_than_or_equal",
                    "value": 2,
                },
                {
                    "field": "successful_order_total",
                    "operator": "greater_than_or_equal",
                    "value": 500_000,
                },
                {
                    "field": "has_successful_order",
                    "operator": "is_true",
                    "value": None,
                },
                {
                    "field": "days_since_last_successful_order",
                    "operator": "greater_than",
                    "value": 30,
                },
            )
        )
        now = datetime.now(timezone.utc)
        eligible_phone = "081299901201"
        recent_phone = "081299901202"
        low_spend_phone = "081299901203"
        for suffix, age in (("a", 45), ("b", 40)):
            self._seed_history_order(
                f"eligible-{suffix}",
                phone=eligible_phone,
                amount=300_000,
                created_at=(now - timedelta(days=age)).isoformat(),
            )
        for suffix, age in (("a", 45), ("b", 5)):
            self._seed_history_order(
                f"recent-{suffix}",
                phone=recent_phone,
                amount=300_000,
                created_at=(now - timedelta(days=age)).isoformat(),
            )
        for suffix, age in (("a", 45), ("b", 40)):
            self._seed_history_order(
                f"low-spend-{suffix}",
                phone=low_spend_phone,
                amount=100_000,
                created_at=(now - timedelta(days=age)).isoformat(),
            )
        self._seed_history_order(
            "failed-does-not-count",
            phone=low_spend_phone,
            amount=1_000_000,
            created_at=(now - timedelta(days=50)).isoformat(),
            topup_status="FAILED",
        )

        eligible = self._quote_ffdana(phone=eligible_phone)
        recent = self._quote_ffdana(phone=recent_phone)
        low_spend = self._quote_ffdana(phone=low_spend_phone)

        self.assertEqual(eligible.status_code, 200, eligible.text)
        self.assertEqual(recent.status_code, 400, recent.text)
        self.assertEqual(recent.json()["reason_code"], "ELIGIBILITY_RULE_NOT_MET")
        self.assertEqual(low_spend.status_code, 400, low_spend.text)
        self.assertEqual(low_spend.json()["reason_code"], "MIN_SUCCESSFUL_SPEND_NOT_MET")

    def test_request_body_cannot_forge_member_or_success_history_facts(self):
        self._seed_ffdana()
        member_rules = self._eligibility(
            {
                "field": "authentication_status",
                "operator": "equals",
                "value": "member",
            },
            {
                "field": "account_status",
                "operator": "equals",
                "value": "active",
            },
        )
        self._set_ffdana_eligibility(member_rules)
        forged = {
            "is_member": True,
            "authentication_status": "member",
            "account_status": "active",
            "has_customer_account": True,
            "account_age_days": 0,
            "successful_order_count": 999,
            "successful_order_total": 999_999_999,
            "has_successful_order": True,
        }
        quote = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": "081299901301",
                "target_id": "forged-quote",
                **forged,
            },
        )
        self.assertNotEqual(quote.status_code, 200, quote.text)
        if quote.status_code == 400:
            self.assertEqual(quote.json()["reason_code"], "MEMBER_LOGIN_REQUIRED")

        self._set_ffdana_eligibility(
            self._eligibility(
                {
                    "field": "successful_order_count",
                    "operator": "greater_than_or_equal",
                    "value": 10,
                }
            )
        )
        before_orders = int(self._query("SELECT COUNT(*) FROM topup")[0][0])
        before_redemptions = int(
            self._query("SELECT COUNT(*) FROM promotion_redemptions")[0][0]
        )
        checkout = self._post_topup(
            promo_code="FFDANA",
            nominal="ff50",
            method="DANA",
            phone="081299901302",
            target_id="forged-checkout",
            extra=forged,
        )
        self.assertNotEqual(checkout.status_code, 200, checkout.text)
        if checkout.status_code == 400:
            self.assertEqual(
                checkout.json()["reason_code"],
                "MIN_SUCCESSFUL_ORDERS_NOT_MET",
            )
        self.assertEqual(int(self._query("SELECT COUNT(*) FROM topup")[0][0]), before_orders)
        self.assertEqual(
            int(self._query("SELECT COUNT(*) FROM promotion_redemptions")[0][0]),
            before_redemptions,
        )

    def test_invalid_explicit_rule_stored_in_database_fails_closed_at_runtime(self):
        self._seed_ffdana()
        asyncio.run(
            self.database.db_execute(
                """
                UPDATE promos
                SET eligibility_rules=:rules,
                    customer_segment='all',
                    max_per_customer=0, max_per_customer_daily=0,
                    max_per_phone=0, max_per_target=0
                WHERE code='FFDANA'
                """,
                {
                    "rules": (
                        '{"version":1,"operator":"all","conditions":['
                        '{"field":"raw_sql","operator":"equals","value":"1=1"}]}'
                    )
                },
            )
        )
        response = self._quote_ffdana(phone="081299901350")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["reason_code"], "INVALID_ELIGIBILITY_RULE")

    def test_atomic_persist_recomputes_history_after_initial_first_purchase_quote(self):
        self._seed_ffdana()
        self._set_ffdana_eligibility(
            self._eligibility(
                {
                    "field": "successful_order_count",
                    "operator": "equals",
                    "value": 0,
                }
            )
        )
        member_id, _ = self._create_member("081299901401")
        identity = self.topup_routes.PromoIdentity(
            customer_id=member_id,
            phone="081299901401",
            target_id="atomic-history-target",
            is_authenticated=True,
            account_active=True,
        )
        product = {
            "sku": "ff50",
            "provider": "Free Fire",
            "name": "Free Fire 50 Diamond",
            "price": 7416,
            "category": "Games",
        }
        expected_quote = asyncio.run(
            self.promotion_service.quote_product(
                product,
                promo_code="FFDANA",
                payment_method="DANA",
                identity=identity,
            )
        )
        self.assertTrue(expected_quote.code_valid)
        self.assertTrue(expected_quote.applied)

        # A SUCCESS order appears after the public quote. The locked persist
        # path must rebuild facts rather than trust that stale quote/profile.
        self._seed_history_order(
            "atomic-history-success",
            phone="081299901499",
            customer_id=member_id,
            amount=7416,
        )
        persist_order = AsyncMock()
        with self.assertRaises(self.promotion_service.PromotionCodeError) as raised:
            asyncio.run(
                self.promotion_service.persist_order_with_promotions(
                    order_id="atomic-first-purchase-order",
                    product=product,
                    expected_quote=expected_quote,
                    persist_order=persist_order,
                    promo_code="FFDANA",
                    payment_method="DANA",
                    identity=identity,
                )
            )
        self.assertEqual(raised.exception.reason, "first_purchase_only")
        persist_order.assert_not_awaited()
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM promotion_redemptions WHERE order_id='atomic-first-purchase-order'"
            )[0][0],
            0,
        )

    def test_quote_validate_and_checkout_call_the_same_central_evaluator(self):
        self._seed_ffdana()
        self._set_ffdana_eligibility(self._eligibility())
        from app.promotions import engine as promotion_engine

        original_evaluator = promotion_engine.evaluate_promotion_eligibility
        with patch.object(
            promotion_engine,
            "evaluate_promotion_eligibility",
            wraps=original_evaluator,
        ) as evaluator:
            quote = self._quote_ffdana(
                phone="081299901501",
                target_id="reuse-quote",
            )
            quote_calls = evaluator.call_count
            validate = self.client.get(
                "/api/promos/validate",
                params={
                    "sku": "ff50",
                    "code": "FFDANA",
                    "method": "DANA",
                    "phone": "081299901502",
                    "target_id": "reuse-validate",
                },
            )
            validate_calls = evaluator.call_count
            checkout = self._post_topup(
                promo_code="FFDANA",
                nominal="ff50",
                method="DANA",
                phone="081299901503",
                target_id="reuse-checkout",
            )
            checkout_calls = evaluator.call_count

        self.assertEqual(quote.status_code, 200, quote.text)
        self.assertGreater(quote_calls, 0)
        self.assertEqual(validate.status_code, 200, validate.text)
        self.assertGreater(validate_calls, quote_calls)
        self.assertEqual(checkout.status_code, 200, checkout.text)
        # Checkout evaluates once for its public quote and again in the locked
        # reservation transaction.
        self.assertGreaterEqual(checkout_calls - validate_calls, 2)

    def test_specific_segment_accepts_only_selected_active_member(self):
        promo_id = self._seed_ffdana()
        selected_id, selected_token = self._create_member("081299900030")
        _, other_token = self._create_member("081299900031")
        self._set_ffdana_segment("specific")
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promotion_customer_targets (promo_id, customer_id)
                VALUES (:promo_id, :customer_id)
                """,
                {"promo_id": promo_id, "customer_id": selected_id},
            )
        )

        selected = self._quote_ffdana(phone="081299900030", token=selected_token)
        self.assertEqual(selected.status_code, 200, selected.text)

        for label, response in (
            ("other member", self._quote_ffdana(phone="081299900031", token=other_token)),
            ("guest", self._quote_ffdana(phone="081299900030")),
        ):
            with self.subTest(identity=label):
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json(),
                    {
                        "valid": False,
                        "reason_code": "CUSTOMER_NOT_TARGETED",
                        "message": "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
                    },
                )

    def test_unknown_segment_fails_closed_and_daily_customer_limit_requires_guest_phone(self):
        self._seed_ffdana()
        body = {
            "sku": "ff50",
            "method": "DANA",
            "promo_code": "FFDANA",
            "phone": "081299900010",
            "target_id": "identity-target",
        }

        asyncio.run(
            self.database.db_execute(
                "UPDATE promos SET customer_segment='vip_only' WHERE code='FFDANA'"
            )
        )
        unknown_segment = self.client.post("/api/promos/quote", json=body)
        self.assertEqual(unknown_segment.status_code, 400, unknown_segment.text)
        self.assertEqual(unknown_segment.json()["reason_code"], "CUSTOMER_NOT_ELIGIBLE")

        asyncio.run(
            self.database.db_execute(
                """
                UPDATE promos
                SET customer_segment='all', max_per_customer=0,
                    max_per_customer_daily=1, max_per_phone=0, max_per_target=0
                WHERE code='FFDANA'
                """
            )
        )
        missing_guest_phone = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
            },
        )
        self.assertEqual(missing_guest_phone.status_code, 400, missing_guest_phone.text)
        self.assertEqual(missing_guest_phone.json()["reason_code"], "PHONE_REQUIRED")

    def test_ffdana_rejections_have_public_reason_codes(self):
        self._seed_ffdana()

        wrong_payment = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "QRIS",
                "promo_code": "FFDANA",
                "phone": "081299900005",
                "target_id": "reason-target",
            },
        )
        self.assertEqual(wrong_payment.status_code, 400, wrong_payment.text)
        self.assertEqual(
            wrong_payment.json(),
            {
                "valid": False,
                "reason_code": "PAYMENT_METHOD_NOT_ELIGIBLE",
                "message": "Metode pembayaran tidak sesuai dengan promo",
            },
        )

        wrong_product = self.client.get(
            "/api/promos/validate",
            params={
                "sku": "ff_12k",
                "code": "FFDANA",
                "method": "DANA",
                "phone": "081299900005",
                "target_id": "reason-target",
            },
        )
        self.assertEqual(wrong_product.status_code, 400, wrong_product.text)
        self.assertEqual(wrong_product.json()["reason_code"], "PRODUCT_NOT_ELIGIBLE")
        self.assertEqual(wrong_product.json()["message"], "Produk tidak termasuk dalam promo")

    def test_ffdana_checkout_is_zero_total_free_promo_without_invoice(self):
        promo_id = self._seed_ffdana()
        invoice = AsyncMock(return_value=self._invoice_result())
        with patch.object(self.topup_routes, "create_invoice", new=invoice):
            response = self._post_topup(
                promo_code=" ffdana ",
                nominal=" FF50 ",
                method=" dana ",
                phone="081234567899",
                target_id="free-fire-user-50",
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["payment_method"], "FREE_PROMO")
        self.assertEqual(payload["payment_fee"], 0)
        self.assertEqual(payload["total"], 0)
        self.assertTrue(payload["free_checkout"])
        invoice.assert_not_awaited()

        order = self._query(
            """
            SELECT nominal, price, amount, payment_method, payment_fee,
                   payment_status, topup_status, promo_id, promo_code,
                   promo_original_price, promo_discount_amount
            FROM topup WHERE id=:order_id
            """,
            {"order_id": payload["id"]},
        )[0]
        self.assertEqual(order[0], "ff50")
        self.assertEqual(int(order[1]), 0)
        self.assertEqual(int(order[2]), 0)
        self.assertEqual(order[3], "FREE_PROMO")
        self.assertEqual(int(order[4]), 0)
        self.assertEqual(order[5], "PAID")
        self.assertEqual(order[6], "PROCESSING")
        self.assertEqual(int(order[7]), promo_id)
        self.assertEqual(order[8], "FFDANA")
        self.assertEqual(int(order[9]), 7416)
        self.assertEqual(int(order[10]), 7416)

        redemption = self._query(
            """
            SELECT status, product_sku, payment_method, original_amount,
                   discount_amount, final_amount, voucher_code
            FROM promotion_redemptions WHERE order_id=:order_id
            """,
            {"order_id": payload["id"]},
        )[0]
        self.assertEqual(redemption[0], "REDEEMED")
        self.assertEqual(redemption[1], "ff50")
        self.assertEqual(redemption[2], "DANA")
        self.assertEqual(int(redemption[3]), 7416)
        self.assertEqual(int(redemption[4]), 7416)
        self.assertEqual(int(redemption[5]), 0)
        self.assertEqual(redemption[6], "FFDANA")

    def test_ffdana_phone_normalization_and_guest_usage_are_isolated(self):
        promo_id = self._seed_ffdana()
        used_phone = "081234567899"
        asyncio.run(
            self.database.db_execute(
                """
                INSERT INTO promotion_redemptions (
                    promo_id, order_id, status, customer_hash, target_hash, product_sku,
                    payment_method, original_amount, discount_amount, final_amount,
                    voucher_code, application_source, redeemed_at
                )
                VALUES (
                    :promo_id, 'existing-ffdana-order', 'REDEEMED', :customer_hash, :target_hash,
                    'ff50', 'DANA', 7416, 7416, 0, 'FFDANA', 'voucher', CURRENT_TIMESTAMP
                )
                """,
                {
                    "promo_id": promo_id,
                    "customer_hash": self.promotion_service.customer_hash(used_phone),
                    "target_hash": self.promotion_service.target_hash("ff50", "used-target"),
                },
            )
        )

        same_guest = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": "6281234567899",
                "target_id": "unused-target",
            },
        )
        self.assertEqual(same_guest.status_code, 400, same_guest.text)
        self.assertEqual(same_guest.json()["reason_code"], "CUSTOMER_LIMIT_REACHED")

        asyncio.run(
            self.database.db_execute(
                "UPDATE promos SET max_per_customer=0 WHERE code='FFDANA'"
            )
        )
        phone_limited = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": used_phone,
                "target_id": "unused-target",
            },
        )
        self.assertEqual(phone_limited.status_code, 400, phone_limited.text)
        self.assertEqual(phone_limited.json()["reason_code"], "PHONE_LIMIT_REACHED")

        asyncio.run(
            self.database.db_execute(
                "UPDATE promos SET max_per_phone=0, max_per_target=1 WHERE code='FFDANA'"
            )
        )
        target_limited = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": "081234560000",
                "target_id": " USED-TARGET ",
            },
        )
        self.assertEqual(target_limited.status_code, 400, target_limited.text)
        self.assertEqual(target_limited.json()["reason_code"], "TARGET_LIMIT_REACHED")

        fresh_phone = "081234567898"
        fresh_target = "fresh-guest-target"
        fresh_guest = self.client.post(
            "/api/promos/quote",
            json={
                "sku": "ff50",
                "method": "DANA",
                "promo_code": "FFDANA",
                "phone": fresh_phone,
                "target_id": fresh_target,
            },
        )
        self.assertEqual(fresh_guest.status_code, 200, fresh_guest.text)

        invoice = AsyncMock(return_value=self._invoice_result())
        with patch.object(self.topup_routes, "create_invoice", new=invoice):
            checkout = self._post_topup(
                promo_code="FFDANA",
                nominal="ff50",
                method="DANA",
                phone=fresh_phone,
                target_id=fresh_target,
            )
        self.assertEqual(checkout.status_code, 200, checkout.text)
        invoice.assert_not_awaited()

        hashes = self._query(
            """
            SELECT customer_hash, target_hash
            FROM promotion_redemptions
            WHERE order_id=:order_id
            """,
            {"order_id": checkout.json()["id"]},
        )[0]
        self.assertEqual(hashes[0], self.promotion_service.customer_hash(fresh_phone))
        self.assertEqual(hashes[1], self.promotion_service.target_hash("ff50", fresh_target))
        self.assertNotEqual(hashes[0], self.promotion_service.customer_hash(used_phone))

    def test_guest_and_member_no_promo_quotes_remain_identical(self):
        self._seed_ffdana()
        _, member_token = self._create_member("081299900006")
        quote_fee = AsyncMock(return_value=(111, "test"))
        body = {"sku": "ff50", "method": "DANA", "promo_code": None}
        with patch.object(self.topup_routes, "_quote_payment_fee", new=quote_fee):
            guest = self.client.post("/api/promos/quote", json=body)
            member = self.client.post(
                "/api/promos/quote",
                headers={"customer-token": member_token},
                json=body,
            )

        self.assertEqual(guest.status_code, 200, guest.text)
        self.assertEqual(member.status_code, 200, member.text)
        for payload in (guest.json(), member.json()):
            self.assertEqual(payload["base_price"], 7416)
            self.assertEqual(payload["final_price"], 7416)
            self.assertEqual(payload["discount_amount"], 0)
            self.assertEqual(payload["payment_fee"], 111)
            self.assertEqual(payload["total"], 7527)
            self.assertFalse(payload["free_checkout"])
        self.assertEqual(quote_fee.await_count, 2)

    def test_public_quote_and_checkout_without_promo_keep_base_price(self):
        quote = asyncio.run(
            self.promotion_service.quote_product(
                {
                    "sku": "ml_10k",
                    "provider": "Mobile Legends",
                    "name": "Diamonds 10K",
                    "price": 10000,
                    "category": "Mobile Legends",
                },
                payment_method="QRIS",
                phone="081234567899",
                target_id="customer-game-123",
            )
        )
        self.assertEqual(quote.base_price, 10000)
        self.assertEqual(quote.final_price, 10000)
        self.assertEqual(quote.applied, ())

        invoice = AsyncMock(return_value=self._invoice_result())
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
        ):
            response = self._post_topup()

        self.assertEqual(response.status_code, 200, response.text)
        order_id = response.json()["id"]
        order = self._query(
            """
            SELECT price, amount, promo_id, promo_code, promo_original_price,
                   promo_discount_amount, promo_snapshot_version
            FROM topup WHERE id=:order_id
            """,
            {"order_id": order_id},
        )[0]
        self.assertEqual(int(order[0]), 10000)
        self.assertEqual(int(order[1]), 10000)
        self.assertIsNone(order[2])
        self.assertIsNone(order[3])
        self.assertEqual(int(order[4]), 10000)
        self.assertEqual(int(order[5]), 0)
        self.assertEqual(int(order[6]), 0)
        self.assertEqual(
            self._query("SELECT COUNT(*) FROM promotion_redemptions WHERE order_id=:order_id", {"order_id": order_id})[0][0],
            0,
        )
        invoice.assert_awaited_once()
        self.assertEqual(invoice.await_args.kwargs["amount"], 10000)

    def test_public_validate_and_checkout_persist_voucher_snapshot_and_reservation(self):
        promo_id = self._seed_voucher()
        validation = self.client.get(
            "/api/promos/validate",
            params={
                "sku": "ml_10k",
                "code": "SAVE2000",
                "method": "QRIS",
                "phone": "081234567899",
                "target_id": "customer-game-123",
            },
        )
        self.assertEqual(validation.status_code, 200, validation.text)
        self.assertTrue(validation.json()["valid"])
        self.assertEqual(validation.json()["original_price"], 10000)
        self.assertEqual(validation.json()["discount_amount"], 2000)
        self.assertEqual(validation.json()["final_price"], 8000)

        invoice = AsyncMock(return_value=self._invoice_result())
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
        ):
            response = self._post_topup(promo_code="SAVE2000")

        self.assertEqual(response.status_code, 200, response.text)
        order_id = response.json()["id"]
        order = self._query(
            """
            SELECT price, amount, promo_id, promo_code, promo_original_price,
                   promo_discount_amount, promo_name, promo_type,
                   promo_discount_type, promo_snapshot, promo_snapshot_version
            FROM topup WHERE id=:order_id
            """,
            {"order_id": order_id},
        )[0]
        self.assertEqual(int(order[0]), 8000)
        self.assertEqual(int(order[1]), 8000)
        self.assertEqual(int(order[2]), promo_id)
        self.assertEqual(order[3], "SAVE2000")
        self.assertEqual(int(order[4]), 10000)
        self.assertEqual(int(order[5]), 2000)
        self.assertEqual(order[6], "Voucher SAVE2000")
        self.assertEqual(order[7], "voucher")
        self.assertEqual(order[8], "fixed")
        self.assertEqual(int(order[10]), 2)
        snapshot = json.loads(order[9])
        self.assertEqual(snapshot["base_price"], 10000)
        self.assertEqual(snapshot["discount_amount"], 2000)
        self.assertEqual(snapshot["final_price"], 8000)
        self.assertEqual(snapshot["promotions"][0]["id"], promo_id)

        redemption = self._query(
            """
            SELECT promo_id, status, product_sku, payment_method, original_amount,
                   discount_amount, final_amount, voucher_code, application_source,
                   snapshot_json
            FROM promotion_redemptions WHERE order_id=:order_id
            """,
            {"order_id": order_id},
        )[0]
        self.assertEqual(int(redemption[0]), promo_id)
        self.assertEqual(redemption[1], "RESERVED")
        self.assertEqual(redemption[2], "ml_10k")
        self.assertEqual(redemption[3], "QRIS")
        self.assertEqual(int(redemption[4]), 10000)
        self.assertEqual(int(redemption[5]), 2000)
        self.assertEqual(int(redemption[6]), 8000)
        self.assertEqual(redemption[7], "SAVE2000")
        self.assertEqual(redemption[8], "voucher")
        self.assertEqual(json.loads(redemption[9])["id"], promo_id)
        invoice.assert_awaited_once()
        self.assertEqual(invoice.await_args.kwargs["amount"], 8000)

    def test_redemption_failure_rolls_back_order_before_external_invoice(self):
        self._seed_voucher()
        invoice = AsyncMock(return_value=self._invoice_result())
        failed_redemption = AsyncMock(side_effect=RuntimeError("forced redemption failure"))
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
            patch.object(self.promotion_service, "_insert_redemptions", new=failed_redemption),
        ):
            response = self._post_topup(promo_code="SAVE2000")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Gagal menyimpan pesanan")
        self.assertNotIn("forced redemption failure", response.json()["detail"])
        self.assertEqual(self._query("SELECT COUNT(*) FROM topup")[0][0], 0)
        self.assertEqual(self._query("SELECT COUNT(*) FROM promotion_redemptions")[0][0], 0)
        failed_redemption.assert_awaited_once()
        invoice.assert_not_awaited()

    def test_external_invoice_failure_releases_reserved_promo(self):
        promo_id = self._seed_voucher()
        invoice = AsyncMock(
            return_value={
                "error": "forced Tripay rejection",
                "_invoice_outcome": "DEFINITIVE_FAILURE",
            }
        )
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
        ):
            response = self._post_topup(promo_code="SAVE2000")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Pembayaran belum dapat dibuat. Silakan coba lagi.")
        order = self._query(
            "SELECT id, payment_status, topup_status FROM topup WHERE promo_id=:promo_id",
            {"promo_id": promo_id},
        )[0]
        self.assertEqual(order[1], "FAILED")
        self.assertEqual(order[2], "FAILED")
        redemption = self._query(
            "SELECT status, released_at FROM promotion_redemptions WHERE order_id=:order_id",
            {"order_id": order[0]},
        )[0]
        self.assertEqual(redemption[0], "RELEASED")
        self.assertIsNotNone(redemption[1])
        invoice.assert_awaited_once()

    def test_ambiguous_invoice_holds_promo_until_signed_paid_callback(self):
        self._seed_voucher()
        invoice = AsyncMock(return_value={"_invoice_outcome": "SENT_UNKNOWN"})
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
        ):
            response = self._post_topup(promo_code="SAVE2000")

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertTrue(payload["payment_creation_pending"])
        order_id = payload["id"]
        order = self._query(
            "SELECT payment_status, topup_status, payment_creation_outcome, amount FROM topup WHERE id=:id",
            {"id": order_id},
        )[0]
        self.assertEqual(order[:3], ("UNPAID", "PENDING_PAYMENT", "SENT_UNKNOWN"))
        redemption = self._query(
            "SELECT status, expires_at FROM promotion_redemptions WHERE order_id=:id",
            {"id": order_id},
        )[0]
        self.assertEqual(redemption[0], "RESERVED")
        self.assertIsNone(redemption[1])

        cancel = self.client.post(
            f"/topup/{order_id}/cancel",
            headers={"x-order-access-token": payload["order_access_token"]},
        )
        self.assertEqual(cancel.status_code, 409)

        callback_payload = {
            "merchant_ref": order_id,
            "reference": "TRIPAY-AMBIGUOUS-PAID",
            "status": "PAID",
            "amount": int(order[3]),
        }
        raw_body = json.dumps(callback_payload, separators=(",", ":")).encode()
        callback_secret = "test-callback-key"
        signature = hmac.new(callback_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        with patch.object(
            self.topup_routes,
            "get_tripay_config",
            new=AsyncMock(return_value={"private_key": callback_secret}),
        ):
            callback = self.client.post(
                "/callback",
                content=raw_body,
                headers={"X-Callback-Signature": signature, "Content-Type": "application/json"},
            )
        self.assertEqual(callback.status_code, 200)
        settled_order = self._query(
            "SELECT payment_status, payment_creation_outcome FROM topup WHERE id=:id",
            {"id": order_id},
        )[0]
        self.assertEqual(settled_order, ("PAID", "SUCCESS"))
        settled_redemption = self._query(
            "SELECT status FROM promotion_redemptions WHERE order_id=:id",
            {"id": order_id},
        )[0]
        self.assertEqual(settled_redemption[0], "REDEEMED")

    def test_free_promo_is_redeemed_immediately_without_external_invoice(self):
        promo_id = self._seed_free_voucher()
        invoice = AsyncMock(return_value=self._invoice_result())
        with (
            patch.object(self.topup_routes, "_quote_payment_fee", new=AsyncMock(return_value=(0, "test"))),
            patch.object(self.topup_routes, "create_invoice", new=invoice),
        ):
            response = self._post_topup(promo_code="FREE100")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["free_checkout"])
        self.assertEqual(payload["payment_method"], "FREE_PROMO")
        self.assertEqual(payload["total"], 0)
        order = self._query(
            """
            SELECT payment_status, topup_status, amount, payment_method,
                   promo_id, promo_discount_amount
            FROM topup WHERE id=:order_id
            """,
            {"order_id": payload["id"]},
        )[0]
        self.assertEqual(order[0], "PAID")
        self.assertEqual(order[1], "PROCESSING")
        self.assertEqual(int(order[2]), 0)
        self.assertEqual(order[3], "FREE_PROMO")
        self.assertEqual(int(order[4]), promo_id)
        self.assertEqual(int(order[5]), 10000)
        redemption = self._query(
            "SELECT status, redeemed_at, final_amount FROM promotion_redemptions WHERE order_id=:order_id",
            {"order_id": payload["id"]},
        )[0]
        self.assertEqual(redemption[0], "REDEEMED")
        self.assertIsNotNone(redemption[1])
        self.assertEqual(int(redemption[2]), 0)
        invoice.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
