import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PromoManagementTests(unittest.TestCase):
    """End-to-end contract tests for the admin promotion management API.

    The whole class uses a disposable SQLite database.  In particular, these
    tests must never read from or write to the project's ``lixafa.db``.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_db = tempfile.NamedTemporaryFile(suffix="-promo-tests.db", delete=False)
        cls.temp_db.close()
        cls.previous_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "DEFAULT_ADMIN_USERNAME": os.environ.get("DEFAULT_ADMIN_USERNAME"),
            "DEFAULT_ADMIN_PASSWORD": os.environ.get("DEFAULT_ADMIN_PASSWORD"),
        }
        database_path = Path(cls.temp_db.name).resolve()
        project_database_path = (Path(__file__).resolve().parents[1] / "lixafa.db").resolve()
        if database_path == project_database_path:
            raise AssertionError("Promo tests may not use lixafa.db")

        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "lixafa123"

        from app.core import settings as core_settings
        from app.core import database as core_database
        from app import database as app_database
        from app.routes import admin_routes

        importlib.reload(core_settings)
        cls.core_database = importlib.reload(core_database)
        cls.database = importlib.reload(app_database)
        cls.admin_routes = importlib.reload(admin_routes)

        app = FastAPI()
        app.include_router(cls.admin_routes.router)
        cls.client = TestClient(app, raise_server_exceptions=False)
        login = cls.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        if login.status_code != 200:
            raise AssertionError(f"Disposable promo test login failed: {login.text}")
        cls.headers = {"token": login.json()["token"]}

        catalog = cls._query(
            """
            SELECT id, target_key
            FROM promotion_target_catalog
            WHERE target_type='sku' AND COALESCE(active, 1)=1
            ORDER BY id
            """
        )
        if len(catalog) < 2:
            raise AssertionError("The disposable catalog must contain at least two SKU targets")
        cls.first_target_id = int(catalog[0][0])
        cls.first_target_sku = str(catalog[0][1])
        cls.second_target_id = int(catalog[1][0])
        cls.second_target_sku = str(catalog[1][1])

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            asyncio.run(cls.core_database.get_engine().dispose())
        finally:
            cls.client.close()
            for key, value in cls.previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if os.path.exists(cls.temp_db.name):
                os.remove(cls.temp_db.name)

            # Do not leave later test modules pointing at the disposed temporary
            # engine after DATABASE_URL has been restored.
            from app.core import settings as core_settings
            from app.core import database as core_database
            from app import database as app_database

            importlib.reload(core_settings)
            importlib.reload(core_database)
            importlib.reload(app_database)
            importlib.reload(cls.admin_routes)

    def setUp(self) -> None:
        for statement in (
            "DELETE FROM promotion_redemptions",
            "DELETE FROM promotion_customer_targets",
            "DELETE FROM promotion_targets",
            "DELETE FROM audit_logs WHERE entity_type='promo'",
            "DELETE FROM promos",
        ):
            self._execute(statement)

    @classmethod
    def _query(cls, statement, params=None):
        return asyncio.run(cls.database.db_query(statement, params or {}))

    @classmethod
    def _execute(cls, statement, params=None):
        return asyncio.run(cls.database.db_execute(statement, params or {}))

    def _create_customer(self, phone, name="Customer Target", *, active=True):
        self._execute(
            """
            INSERT INTO customer_accounts (name, phone, password, active)
            VALUES (:name, :phone, 'unused-test-password', :active)
            ON CONFLICT(phone) DO UPDATE SET
                name=excluded.name,
                active=excluded.active
            """,
            {"name": name, "phone": phone, "active": int(bool(active))},
        )
        return int(
            self._query(
                "SELECT id FROM customer_accounts WHERE phone=:phone",
                {"phone": phone},
            )[0][0]
        )

    def _create(self, payload, expected_status=200):
        response = self.client.post("/admin/api/promos", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, expected_status, response.text)
        return response

    def _detail(self, promo_id):
        response = self.client.get(f"/admin/api/promos/{promo_id}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _banner(title="Banner Test"):
        return {
            "title": title,
            "promo_type": "banner",
            "lifecycle_status": "draft",
            "rule_type": "content",
            "target_scope": "all",
            "active": 0,
        }

    @staticmethod
    def _discount(title="Diskon Test", calculation_type="percent", discount_value=10):
        return {
            "title": title,
            "promo_type": "automatic",
            "lifecycle_status": "draft",
            "rule_type": "price",
            "target_scope": "all",
            "calculation_type": calculation_type,
            "discount_type": calculation_type,
            "discount_value": discount_value,
            "active": 0,
        }

    @staticmethod
    def _eligibility(*conditions, operator="all"):
        return {
            "version": 1,
            "operator": operator,
            "conditions": list(conditions),
        }

    def test_create_all_canonical_promo_variants(self):
        variants = [
            (
                "banner",
                {
                    **self._banner("Banner canonical"),
                    "active_days": [0, 1, 6],
                },
            ),
            ("automatic", self._discount("Persentase canonical", "percent", 15)),
            ("automatic", self._discount("Nominal canonical", "fixed", 5_000)),
            (
                "voucher",
                {
                    **self._discount("Voucher canonical", "fixed", 2_000),
                    "promo_type": "voucher",
                    "code": "CANON20",
                },
            ),
            (
                "special_price",
                {
                    **self._discount("Harga khusus canonical", "special_price", None),
                    "promo_type": "special_price",
                    "special_price": 8_000,
                },
            ),
            (
                "payment_method",
                {
                    **self._discount("QRIS canonical", "fixed", 1_000),
                    "promo_type": "payment_method",
                    "payment_methods": ["QRIS"],
                },
            ),
        ]

        for expected_type, payload in variants:
            with self.subTest(expected_type=expected_type, title=payload["title"]):
                created = self._create(payload).json()
                promo = self._detail(created["id"])
                self.assertEqual(promo["promo_type"], expected_type)
                self.assertEqual(promo["placements"], ["promo_cards"])
                self.assertIsInstance(promo["target_option_ids"], list)
                self.assertIsInstance(promo["payment_methods"], list)

        banner = next(
            item
            for item in self.client.get("/admin/api/promos", headers=self.headers).json()
            if item["title"] == "Banner canonical"
        )
        self.assertEqual(banner["active_days"], [0, 1, 6])

    def test_optional_numeric_fields_and_unlimited_quota_accept_empty_null_and_zero(self):
        empty_payload = {
            **self._banner("Opsional kosong"),
            "discount_type": "",
            "discount_value": "",
            "max_discount": "",
            "minimum_transaction": "",
            "special_price": "",
            "budget_limit": "",
            "usage_limit": None,
            "quota_daily": "",
            "max_per_customer": None,
            "max_per_customer_daily": "",
            "max_per_phone": None,
            "max_per_target": "",
            "priority": "",
            "display_order": None,
        }
        empty_id = self._create(empty_payload).json()["id"]
        empty = self._detail(empty_id)
        self.assertEqual(empty["usage_limit"], 0)
        self.assertEqual(empty["quota_daily"], 0)
        self.assertIsNone(empty["special_price"])

        zero_id = self._create(
            {
                **self._banner("Kuota nol"),
                "usage_limit": 0,
                "quota_daily": 0,
                "budget_limit": 0,
            }
        ).json()["id"]
        zero = self._detail(zero_id)
        self.assertEqual(zero["usage_limit"], 0)
        self.assertEqual(zero["quota_daily"], 0)
        self.assertEqual(zero["budget_limit"], 0)

    def test_all_and_specific_targets_round_trip_ids_and_sku_strings(self):
        all_id = self._create(
            {
                **self._discount("Semua produk", "fixed", 500),
                "target_scope": "all",
                "target_option_ids": [],
            }
        ).json()["id"]
        all_products = self._detail(all_id)
        self.assertEqual(all_products["target_option_ids"], [])
        self.assertEqual(all_products["targets"], [])
        self.assertEqual(all_products["target_scope"], "all")

        specific_id = self._create(
            {
                **self._discount("SKU tertentu", "fixed", 500),
                "target_option_ids": [self.first_target_id],
            }
        ).json()["id"]
        specific = self._detail(specific_id)
        self.assertEqual(specific["target_option_ids"], [self.first_target_id])
        self.assertIsInstance(specific["target_option_ids"][0], int)
        self.assertEqual(specific["targets"][0]["target_key"], self.first_target_sku)
        self.assertIsInstance(specific["targets"][0]["target_key"], str)
        self.assertEqual(specific["target_value"], self.first_target_sku)

        legacy_sku_id = self._create(
            {
                **self._discount("SKU string legacy", "fixed", 500),
                "target_scope": "sku",
                "target_value": self.first_target_sku,
                "target_option_ids": [],
            }
        ).json()["id"]
        legacy_sku = self._detail(legacy_sku_id)
        self.assertEqual(legacy_sku["target_value"], self.first_target_sku)
        self.assertIsInstance(legacy_sku["target_value"], str)

    def test_payment_method_codes_remain_strings_and_are_deduplicated(self):
        payment_id = self._create(
            {
                **self._discount("Metode pembayaran", "fixed", 1_000),
                "promo_type": "payment_method",
                "payment_methods": ["qris", "BRIVA", "QRIS"],
            }
        ).json()["id"]
        payment = self._detail(payment_id)
        self.assertEqual(payment["payment_methods"], ["QRIS", "BRIVA"])
        self.assertTrue(all(isinstance(code, str) for code in payment["payment_methods"]))

        all_methods_id = self._create(
            {
                **self._discount("Semua metode", "fixed", 1_000),
                "payment_methods": [],
            }
        ).json()["id"]
        self.assertEqual(self._detail(all_methods_id)["payment_methods"], [])

    def test_customer_segment_create_update_roundtrip_and_all_alias(self):
        alias_id = self._create(
            {
                **self._banner("Alias semua customer"),
                "customer_segment": "all_customers",
            }
        ).json()["id"]
        self.assertEqual(self._detail(alias_id)["customer_segment"], "all")
        stored_alias = self._query(
            "SELECT customer_segment FROM promos WHERE id=:promo_id",
            {"promo_id": alias_id},
        )[0][0]
        self.assertEqual(stored_alias, "all")

        promo_id = self._create(
            {
                **self._banner("Segment member"),
                "customer_segment": "MEMBERS_ONLY",
            }
        ).json()["id"]
        self.assertEqual(self._detail(promo_id)["customer_segment"], "members_only")

        updated = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={"customer_segment": "guests_only"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self._detail(promo_id)["customer_segment"], "guests_only")
        stored_updated = self._query(
            "SELECT customer_segment FROM promos WHERE id=:promo_id",
            {"promo_id": promo_id},
        )[0][0]
        self.assertEqual(stored_updated, "guests_only")

        for segment in ("new", "existing"):
            with self.subTest(segment=segment):
                segment_id = self._create(
                    {
                        **self._banner(f"Segment {segment}"),
                        "customer_segment": segment,
                    }
                ).json()["id"]
                self.assertEqual(self._detail(segment_id)["customer_segment"], segment)
                stored = self._query(
                    "SELECT customer_segment FROM promos WHERE id=:promo_id",
                    {"promo_id": segment_id},
                )[0][0]
                self.assertEqual(stored, segment)

    def test_customer_segment_schema_exposes_only_canonical_enum(self):
        expected = ["all", "members_only", "guests_only", "new", "existing", "specific"]
        for model in (
            self.admin_routes.PromoCreateRequest,
            self.admin_routes.PromoUpdateRequest,
            self.admin_routes.PromoSimulationRequest,
        ):
            with self.subTest(model=model.__name__):
                schema = model.model_json_schema()
                self.assertEqual(schema["$defs"]["PromoCustomerSegment"]["enum"], expected)

        compatibility = self.admin_routes.PromoCreateRequest(
            title="Alias schema",
            customer_segment="all_customers",
        )
        self.assertEqual(compatibility.customer_segment, "all")

    def test_customer_segment_options_expose_member_and_guest_metadata(self):
        active_customer_id = self._create_customer(
            "081288880010",
            "Opsi Customer Aktif",
            active=True,
        )
        inactive_customer_id = self._create_customer(
            "081288880011",
            "Opsi Customer Nonaktif",
            active=False,
        )

        async def unavailable_payment_channels():
            return {"success": False, "data": []}

        with patch.object(
            self.admin_routes,
            "get_payment_channels",
            new=unavailable_payment_channels,
        ):
            response = self.client.get("/admin/api/promos/options", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["customer_segments"]
        self.assertEqual(
            [item["value"] for item in rows],
            ["all", "members_only", "guests_only", "new", "existing", "specific"],
        )
        options = {item["value"]: item for item in rows}
        self.assertIn("all_customers", options["all"]["aliases"])
        self.assertEqual(options["all"]["label"], "Semua pelanggan")
        self.assertEqual(options["members_only"]["label"], "Khusus member terdaftar")
        self.assertEqual(
            options["members_only"]["description"],
            "Promo hanya dapat digunakan oleh pelanggan yang sudah masuk ke akun aktif.",
        )
        self.assertEqual(options["guests_only"]["label"], "Khusus guest")
        self.assertEqual(
            options["guests_only"]["description"],
            "Promo hanya dapat digunakan tanpa login akun.",
        )
        self.assertEqual(options["new"]["label"], "Pembeli pertama")
        self.assertEqual(
            options["new"]["description"],
            "Guest atau member yang belum pernah memiliki transaksi berhasil.",
        )
        self.assertEqual(options["existing"]["label"], "Pelanggan lama")
        self.assertEqual(
            options["existing"]["description"],
            "Guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.",
        )
        self.assertEqual(options["specific"]["label"], "Pelanggan tertentu")

        customer_options = response.json()["customer_options"]
        simulation_options = response.json()["simulation_customer_options"]
        self.assertIn(active_customer_id, {int(item["id"]) for item in customer_options})
        self.assertNotIn(inactive_customer_id, {int(item["id"]) for item in customer_options})
        simulation_by_id = {int(item["id"]): item for item in simulation_options}
        self.assertTrue(simulation_by_id[active_customer_id]["active"])
        self.assertFalse(simulation_by_id[inactive_customer_id]["active"])

    def test_promo_customer_options_search_hydration_masking_and_permission(self):
        self._execute(
            """
            WITH RECURSIVE seq(value) AS (
                SELECT 1
                UNION ALL
                SELECT value + 1 FROM seq WHERE value < 505
            )
            INSERT INTO customer_accounts (name, phone, email, password, active)
            SELECT
                printf('Bulk Customer %04d', value),
                printf('0817555%04d', value),
                printf('bulk%04d@example.test', value),
                'unused-test-password',
                1
            FROM seq
            """
        )
        searchable_id = self._create_customer("081799999991", "Needle Customer Beyond Five Hundred")
        searchable_email = "needle.customer@example.test"
        self._execute(
            "UPDATE customer_accounts SET email=:email WHERE id=:customer_id",
            {"email": searchable_email, "customer_id": searchable_id},
        )
        inactive_id = self._create_customer("081799999992", "Hydrate Nonaktif", active=False)

        password = "marketing-promo-test"
        self._execute(
            """
            INSERT INTO admin (username, password, role, permissions, active)
            VALUES ('promo_marketing_test', :password, 'marketing', :permissions, 1)
            ON CONFLICT(username) DO UPDATE SET
                password=excluded.password,
                role=excluded.role,
                permissions=excluded.permissions,
                active=1
            """,
            {
                "password": self.admin_routes.hash_password(password),
                "permissions": '["promos:manage"]',
            },
        )
        login = self.client.post(
            "/admin/login",
            json={"username": "promo_marketing_test", "password": password},
        )
        self.assertEqual(login.status_code, 200, login.text)
        promo_headers = {"token": login.json()["token"]}
        self.assertEqual(
            self.client.get("/admin/api/customers", headers=promo_headers).status_code,
            403,
        )
        self.assertEqual(self.client.get("/admin/api/promos/customer-options").status_code, 401)

        by_name = self.client.get(
            "/admin/api/promos/customer-options",
            headers=promo_headers,
            params={"q": "Needle Customer", "limit": 10},
        )
        self.assertEqual(by_name.status_code, 200, by_name.text)
        self.assertEqual([item["id"] for item in by_name.json()["items"]], [searchable_id])

        by_phone = self.client.get(
            "/admin/api/promos/customer-options",
            headers=promo_headers,
            params={"q": "081799999991"},
        )
        self.assertEqual(by_phone.status_code, 200, by_phone.text)
        self.assertNotIn("081799999991", by_phone.text)
        self.assertNotIn(searchable_email, by_phone.text)
        self.assertIn("phone_masked", by_phone.json()["items"][0])

        normalized_phone_lookup = self.client.get(
            "/admin/api/promos/customer-options",
            headers=promo_headers,
            params={"q": "+6281799999991"},
        )
        self.assertEqual(normalized_phone_lookup.status_code, 200, normalized_phone_lookup.text)
        self.assertEqual(normalized_phone_lookup.json()["items"][0]["id"], searchable_id)
        self.assertNotIn("081799999991", normalized_phone_lookup.text)

        hydrated = self.client.get(
            "/admin/api/promos/customer-options",
            headers=promo_headers,
            params={"ids": str(inactive_id)},
        )
        self.assertEqual(hydrated.status_code, 200, hydrated.text)
        self.assertEqual(hydrated.json()["items"][0]["id"], inactive_id)
        self.assertFalse(hydrated.json()["items"][0]["active"])

        paged = self.client.get(
            "/admin/api/promos/customer-options",
            headers=promo_headers,
            params={"limit": 10},
        )
        self.assertEqual(paged.status_code, 200, paged.text)
        self.assertEqual(len(paged.json()["items"]), 10)
        self.assertTrue(paged.json()["has_more"])

    def test_specific_customer_segment_roundtrips_edit_and_duplicate(self):
        first_customer_id = self._create_customer("081288880001", "Target Pertama")
        second_customer_id = self._create_customer("081288880002", "Target Kedua")
        promo_id = self._create(
            {
                **self._banner("Promo target akun"),
                "customer_segment": "specific",
                "customer_ids": [first_customer_id, first_customer_id, second_customer_id],
            }
        ).json()["id"]

        created = self._detail(promo_id)
        self.assertEqual(created["customer_segment"], "specific")
        self.assertEqual(sorted(created["customer_ids"]), sorted([first_customer_id, second_customer_id]))
        stored_customer_ids = [
            int(row[0])
            for row in self._query(
                "SELECT customer_id FROM promotion_customer_targets WHERE promo_id=:promo_id ORDER BY id",
                {"promo_id": promo_id},
            )
        ]
        self.assertEqual(stored_customer_ids, [first_customer_id, second_customer_id])

        title_only_edit = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={"title": "Promo target akun diedit"},
        )
        self.assertEqual(title_only_edit.status_code, 200, title_only_edit.text)
        edited = self._detail(promo_id)
        self.assertEqual(edited["customer_segment"], "specific")
        self.assertEqual(sorted(edited["customer_ids"]), sorted([first_customer_id, second_customer_id]))

        duplicated = self.client.post(
            f"/admin/api/promos/{promo_id}/duplicate",
            headers=self.headers,
        )
        self.assertEqual(duplicated.status_code, 200, duplicated.text)
        copy = self._detail(duplicated.json()["id"])
        self.assertEqual(copy["customer_segment"], "specific")
        self.assertEqual(sorted(copy["customer_ids"]), sorted([first_customer_id, second_customer_id]))

        changed_target = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "customer_segment": "specific",
                "customer_ids": [second_customer_id, second_customer_id],
            },
        )
        self.assertEqual(changed_target.status_code, 200, changed_target.text)
        self.assertEqual(self._detail(promo_id)["customer_ids"], [second_customer_id])

        changed_to_all = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={"customer_segment": "all", "customer_ids": []},
        )
        self.assertEqual(changed_to_all.status_code, 200, changed_to_all.text)
        changed = self._detail(promo_id)
        self.assertEqual(changed["customer_segment"], "all")
        self.assertEqual(changed["customer_ids"], [])
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM promotion_customer_targets WHERE promo_id=:promo_id",
                {"promo_id": promo_id},
            )[0][0],
            0,
        )

    def test_promo_simulation_uses_segment_engine_and_returns_identity_diagnostics(self):
        active_customer_id = self._create_customer(
            "081288880020",
            "Member Simulasi Aktif",
            active=True,
        )
        other_customer_id = self._create_customer(
            "081288880021",
            "Member Simulasi Lain",
            active=True,
        )
        inactive_customer_id = self._create_customer(
            "081288880022",
            "Member Simulasi Nonaktif",
            active=False,
        )
        returning_phone = "081288880023"
        self._execute(
            """
            INSERT INTO topup (
                id, phone, customer_id, target_id, nominal,
                payment_status, topup_status
            ) VALUES (
                'segment-sim-success', :phone, :customer_id, 'sim-target', :sku,
                'PAID', 'SUCCESS'
            )
            """,
            {
                "phone": returning_phone,
                "customer_id": active_customer_id,
                "sku": self.first_target_sku,
            },
        )

        async def zero_fee(_amount, _method):
            return 0, "test"

        def simulate(segment, **identity):
            from app.promotions import engine as promotion_engine

            payload = {
                **self._discount("Simulasi segment", "fixed", 1_000),
                "lifecycle_status": "active",
                "active": 1,
                "customer_segment": segment,
                "sku": self.first_target_sku,
                "method": "QRIS",
                "target_id": "sim-target",
                **identity,
            }
            with (
                patch.object(self.admin_routes, "_simulate_payment_fee", new=zero_fee),
                patch.object(
                    self.admin_routes,
                    "quote_promotions",
                    wraps=self.admin_routes.quote_promotions,
                ) as quote_engine,
                patch.object(
                    promotion_engine,
                    "evaluate_promotion_eligibility",
                    wraps=promotion_engine.evaluate_promotion_eligibility,
                ) as eligibility_engine,
            ):
                response = self.client.post(
                    "/admin/api/promos/simulate",
                    headers=self.headers,
                    json=payload,
                )
            self.assertTrue(quote_engine.called, "Simulasi harus memakai promo engine checkout yang sama")
            self.assertTrue(
                eligibility_engine.called,
                "Simulasi harus memakai evaluator eligibility terpusat",
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        all_guest = simulate("all", customer_phone="081288880099")
        self.assertTrue(all_guest["eligible"])
        self.assertIsNone(all_guest["reason_code"])
        self.assertEqual(all_guest["diagnostics"]["customer_segment"], "all")
        self.assertTrue(all_guest["diagnostics"]["segment_match"])
        self.assertEqual(all_guest["diagnostics"]["identity"]["status"], "guest")

        member_guest = simulate("members_only", customer_phone="081288880099")
        self.assertFalse(member_guest["eligible"])
        self.assertEqual(member_guest["reason_code"], "MEMBER_LOGIN_REQUIRED")
        self.assertEqual(member_guest["reason"], "Masuk ke akun untuk menggunakan promo ini.")
        self.assertFalse(member_guest["diagnostics"]["segment_match"])

        member_active = simulate("members_only", customer_id=other_customer_id)
        self.assertTrue(member_active["eligible"])
        self.assertTrue(member_active["diagnostics"]["identity"]["is_authenticated"])
        self.assertTrue(member_active["diagnostics"]["identity"]["account_active"])

        member_inactive = simulate("members_only", customer_id=inactive_customer_id)
        self.assertFalse(member_inactive["eligible"])
        self.assertEqual(member_inactive["reason_code"], "ACCOUNT_INACTIVE")
        self.assertEqual(
            member_inactive["reason"],
            "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
        )
        self.assertFalse(member_inactive["diagnostics"]["identity"]["account_active"])

        guest_guest = simulate("guests_only", customer_phone="081288880099")
        self.assertTrue(guest_guest["eligible"])
        guest_member = simulate("guests_only", customer_id=other_customer_id)
        self.assertFalse(guest_member["eligible"])
        self.assertEqual(guest_member["reason_code"], "GUEST_ONLY_PROMO")
        self.assertEqual(
            guest_member["reason"],
            "Promo ini hanya berlaku untuk checkout tanpa akun.",
        )

        new_customer = simulate("new", customer_phone="081288880099")
        self.assertTrue(new_customer["eligible"])
        self.assertEqual(new_customer["diagnostics"]["history"]["status"], "new")
        returning_rejected = simulate("new", customer_phone="+6281288880023")
        self.assertFalse(returning_rejected["eligible"])
        self.assertEqual(returning_rejected["reason_code"], "FIRST_PURCHASE_ONLY")
        self.assertTrue(returning_rejected["diagnostics"]["history"]["has_success_order"])

        returning_customer = simulate("existing", customer_phone="6281288880023")
        self.assertTrue(returning_customer["eligible"])
        self.assertEqual(returning_customer["diagnostics"]["history"]["success_order_count"], 1)
        existing_rejected = simulate("existing", customer_phone="081288880099")
        self.assertFalse(existing_rejected["eligible"])
        self.assertEqual(existing_rejected["reason_code"], "EXISTING_CUSTOMER_ONLY")

        selected = simulate(
            "specific",
            customer_id=active_customer_id,
            customer_ids=[active_customer_id],
        )
        self.assertTrue(selected["eligible"])
        self.assertEqual(
            selected["diagnostics"]["specific_target"],
            {"required": True, "matched": True, "selected_count": 1},
        )
        not_selected = simulate(
            "specific",
            customer_id=other_customer_id,
            customer_ids=[active_customer_id],
        )
        self.assertFalse(not_selected["eligible"])
        self.assertEqual(not_selected["reason_code"], "CUSTOMER_NOT_TARGETED")
        self.assertEqual(
            not_selected["reason"],
            "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
        )

        missing_identity = simulate("new", target_id=None)
        self.assertFalse(missing_identity["eligible"])
        self.assertEqual(missing_identity["reason_code"], "CUSTOMER_IDENTITY_REQUIRED")
        self.assertEqual(missing_identity["diagnostics"]["history"]["status"], "unknown")

    def test_invalid_customer_segment_is_rejected_on_create_and_update(self):
        invalid_create = self._create(
            {
                **self._banner("Segment invalid create"),
                "customer_segment": "vip_only",
            },
            expected_status=422,
        )
        self.assertIn("Segmentasi pelanggan tidak dikenal", invalid_create.text)

        promo_id = self._create(
            {
                **self._banner("Segment valid sebelum update"),
                "customer_segment": "members_only",
            }
        ).json()["id"]
        invalid_update = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={"customer_segment": "unknown_segment"},
        )
        self.assertEqual(invalid_update.status_code, 422, invalid_update.text)
        self.assertIn("Segmentasi pelanggan tidak dikenal", invalid_update.text)
        self.assertEqual(self._detail(promo_id)["customer_segment"], "members_only")

    def test_invalid_integer_arrays_reject_blank_sku_and_objects_with_item_loc(self):
        invalid_values = [
            ("blank", [self.first_target_id, ""], 1),
            ("sku", [self.first_target_sku], 0),
            ("object", [{"id": self.first_target_id}], 0),
        ]
        for label, values, expected_index in invalid_values:
            with self.subTest(label=label):
                response = self._create(
                    {
                        **self._banner(f"Invalid target {label}"),
                        "target_option_ids": values,
                    },
                    expected_status=422,
                )
                detail = response.json()["detail"]
                self.assertTrue(detail, response.text)
                self.assertEqual(detail[0]["loc"], ["body", "target_option_ids", expected_index])

        payment_object = self._create(
            {
                **self._discount("Object payment", "fixed", 1_000),
                "payment_methods": [{"code": "QRIS"}],
            },
            expected_status=422,
        )
        self.assertEqual(payment_object.json()["detail"][0]["loc"], ["body", "payment_methods", 0])

    def test_discount_validation_handles_empty_invalid_fractional_and_over_100(self):
        cases = [
            (
                "empty",
                {**self._discount("Diskon kosong", "percent", None), "discount_value": ""},
                400,
                "Nilai diskon",
            ),
            (
                "invalid",
                {**self._discount("Diskon invalid", "percent", None), "discount_value": "abc"},
                422,
                "discount_value",
            ),
            (
                "fractional",
                {**self._discount("Diskon pecahan", "percent", None), "discount_value": "10.5"},
                400,
                "bulat",
            ),
            (
                "over-100",
                self._discount("Diskon terlalu besar", "percent", 101),
                400,
                "100",
            ),
            (
                "fixed-zero",
                self._discount("Diskon nominal nol", "fixed", 0),
                400,
                "lebih dari 0",
            ),
        ]
        for label, payload, status, marker in cases:
            with self.subTest(label=label):
                response = self._create(payload, expected_status=status)
                self.assertIn(marker, response.text)
                if label == "invalid":
                    self.assertEqual(response.json()["detail"][0]["loc"], ["body", "discount_value"])

    def test_invalid_schedule_and_duplicate_voucher_are_rejected(self):
        invalid_schedule = self._create(
            {
                **self._banner("Jadwal invalid"),
                "starts_at": "2026-08-02T10:00:00",
                "ends_at": "2026-08-01T10:00:00",
            },
            expected_status=400,
        )
        self.assertIn("setelah waktu mulai", invalid_schedule.text)

        malformed_schedule = self._create(
            {**self._banner("Format jadwal invalid"), "starts_at": "bukan-tanggal"},
            expected_status=400,
        )
        self.assertIn("tidak valid", malformed_schedule.text)

        voucher = {
            **self._discount("Voucher pertama", "fixed", 1_000),
            "promo_type": "voucher",
            "code": "HEMAT10",
        }
        self._create(voucher)
        duplicate = self._create(
            {**voucher, "title": "Voucher duplikat", "code": "hemat10"},
            expected_status=409,
        )
        self.assertIn("Kode voucher", duplicate.text)

    def test_legacy_promo_can_be_edited_without_losing_legacy_data(self):
        self._execute(
            """
            INSERT INTO promos (
                title, code, description, rule_type, target_scope, target_value,
                discount_type, discount_value, max_discount, payment_methods,
                show_on_website, active, rules_version, legacy_compatible
            ) VALUES (
                :title, :code, :description, 'price', 'sku', :target_value,
                'fixed', 500, 0, :payment_methods, 1, 1, 'legacy_v1', 1
            )
            """,
            {
                "title": "Promo lama",
                "code": "OLD500",
                "description": "Data lama dipertahankan",
                "target_value": self.first_target_sku,
                "payment_methods": '["LEGACY_GATEWAY"]',
            },
        )
        legacy_id = int(self._query("SELECT id FROM promos WHERE code='OLD500'")[0][0])
        self.assertIsNone(
            self._query(
                "SELECT eligibility_rules FROM promos WHERE id=:promo_id",
                {"promo_id": legacy_id},
            )[0][0]
        )

        adapted_before_edit = self._detail(legacy_id)
        self.assertEqual(adapted_before_edit["eligibility_source"], "legacy_adapter")
        self.assertEqual(adapted_before_edit["eligibility_legacy_segment"], "all")
        self.assertEqual(adapted_before_edit["eligibility_rules"], self._eligibility())
        self.assertIsNone(adapted_before_edit["eligibility_rules_stored"])

        response = self.client.put(
            f"/admin/api/promos/{legacy_id}",
            headers=self.headers,
            json={"title": "Promo lama diedit"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        legacy = self._detail(legacy_id)
        self.assertEqual(legacy["id"], legacy_id)
        self.assertEqual(legacy["title"], "Promo lama diedit")
        self.assertEqual(legacy["code"], "OLD500")
        self.assertEqual(legacy["target_value"], self.first_target_sku)
        self.assertEqual(legacy["payment_methods"], ["LEGACY_GATEWAY"])
        self.assertEqual(legacy["description"], "Data lama dipertahankan")
        self.assertEqual(legacy["customer_segment"], "all")
        self.assertEqual(legacy["eligibility_source"], "legacy_adapter")
        self.assertIsNone(
            self._query(
                "SELECT eligibility_rules FROM promos WHERE id=:promo_id",
                {"promo_id": legacy_id},
            )[0][0],
            "An unrelated legacy edit must not silently persist adapted rules",
        )

        explicit_rules = self._eligibility(
            {
                "field": "successful_order_count",
                "operator": "equals",
                "value": 0,
            }
        )
        explicit = self.client.put(
            f"/admin/api/promos/{legacy_id}",
            headers=self.headers,
            json={"eligibility_rules": explicit_rules},
        )
        self.assertEqual(explicit.status_code, 200, explicit.text)
        converted = self._detail(legacy_id)
        self.assertEqual(converted["eligibility_source"], "explicit")
        self.assertIsNone(converted["eligibility_legacy_segment"])
        self.assertEqual(converted["eligibility_rules"], explicit_rules)
        self.assertEqual(
            json.loads(
                self._query(
                    "SELECT eligibility_rules FROM promos WHERE id=:promo_id",
                    {"promo_id": legacy_id},
                )[0][0]
            ),
            explicit_rules,
        )

    def test_eligibility_rules_create_update_detail_and_duplicate_round_trip(self):
        original_rules = self._eligibility(
            {
                "field": "authentication_status",
                "operator": "equals",
                "value": "member",
            },
            {
                "field": "successful_order_count",
                "operator": "greater_than_or_equal",
                "value": 10,
            },
            {
                "field": "successful_order_total",
                "operator": "greater_than_or_equal",
                "value": 500_000,
            },
        )
        promo_id = self._create(
            {
                **self._discount("Member setia", "fixed", 2_000),
                "eligibility_rules": original_rules,
            }
        ).json()["id"]

        created = self._detail(promo_id)
        self.assertEqual(created["eligibility_rules"], original_rules)
        raw_created = self._query(
            "SELECT eligibility_rules FROM promos WHERE id=:promo_id",
            {"promo_id": promo_id},
        )[0][0]
        self.assertEqual(json.loads(raw_created), original_rules)

        updated_rules = self._eligibility(
            {
                "field": "successful_order_count",
                "operator": "greater_than_or_equal",
                "value": 20,
            },
            {
                "field": "successful_order_total",
                "operator": "greater_than_or_equal",
                "value": 1_000_000,
            },
            operator="any",
        )
        update = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={"eligibility_rules": updated_rules},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(self._detail(promo_id)["eligibility_rules"], updated_rules)

        duplicate = self.client.post(
            f"/admin/api/promos/{promo_id}/duplicate",
            headers=self.headers,
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        duplicate_rules = self._detail(duplicate.json()["id"])["eligibility_rules"]
        self.assertEqual(duplicate_rules, updated_rules)

    def test_explicit_customer_id_condition_atomically_mirrors_customer_relations(self):
        first_customer = self._create_customer("081288881101", "Eligibility Satu")
        second_customer = self._create_customer("081288881102", "Eligibility Dua")
        rules = self._eligibility(
            {
                "field": "customer_id",
                "operator": "in",
                "value": [first_customer, second_customer, second_customer],
            }
        )
        promo_id = self._create(
            {
                **self._discount("Customer rule canonical", "fixed", 1_000),
                "customer_segment": "specific",
                "eligibility_rules": rules,
            }
        ).json()["id"]
        canonical_ids = sorted([first_customer, second_customer])
        created = self._detail(promo_id)
        self.assertEqual(created["customer_ids"], canonical_ids)
        self.assertEqual(
            created["eligibility_rules"]["conditions"][0]["value"],
            canonical_ids,
        )

        narrowed_rules = self._eligibility(
            {
                "field": "customer_id",
                "operator": "in",
                "value": [second_customer],
            }
        )
        narrowed = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "eligibility_rules": narrowed_rules,
                "customer_ids": [second_customer],
            },
        )
        self.assertEqual(narrowed.status_code, 200, narrowed.text)
        self.assertEqual(self._detail(promo_id)["customer_ids"], [second_customer])

        mismatch = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "eligibility_rules": rules,
                "customer_ids": [second_customer],
            },
        )
        self.assertEqual(mismatch.status_code, 400, mismatch.text)
        persisted = self._detail(promo_id)
        self.assertEqual(persisted["eligibility_rules"], narrowed_rules)
        self.assertEqual(persisted["customer_ids"], [second_customer])

    def test_invalid_eligibility_payload_never_creates_or_mutates_a_promo(self):
        invalid_rules = (
            self._eligibility(
                {"field": "database_column", "operator": "equals", "value": 1}
            ),
            self._eligibility(
                {
                    "field": "authentication_status",
                    "operator": "greater_than",
                    "value": "member",
                }
            ),
            self._eligibility(
                {
                    "field": "account_age_days",
                    "operator": "less_than_or_equal",
                    "value": "seven",
                }
            ),
        )
        for index, rules in enumerate(invalid_rules):
            with self.subTest(case=index):
                response = self._create(
                    {
                        **self._discount(f"Invalid eligibility {index}", "fixed", 500),
                        "eligibility_rules": rules,
                    },
                    expected_status=400,
                )
                self.assertIn("eligib", response.text.casefold())

        self.assertEqual(
            self._query("SELECT COUNT(*) FROM promos WHERE title LIKE 'Invalid eligibility %'")[0][0],
            0,
        )

        original_rules = self._eligibility(
            {"field": "successful_order_count", "operator": "equals", "value": 0}
        )
        promo_id = self._create(
            {
                **self._discount("Eligibility tetap utuh", "fixed", 500),
                "eligibility_rules": original_rules,
            }
        ).json()["id"]
        failed_update = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "title": "Tidak boleh tersimpan",
                "eligibility_rules": self._eligibility(
                    {
                        "field": "successful_order_count",
                        "operator": "contains",
                        "value": 1,
                    }
                ),
            },
        )
        self.assertEqual(failed_update.status_code, 400, failed_update.text)
        persisted = self._detail(promo_id)
        self.assertEqual(persisted["title"], "Eligibility tetap utuh")
        self.assertEqual(persisted["eligibility_rules"], original_rules)

    def test_updating_eligibility_never_rewrites_existing_order_or_redemption(self):
        promo_id = self._create(
            {
                **self._discount("Promo dengan histori", "fixed", 1_000),
                "eligibility_rules": self._eligibility(
                    {
                        "field": "successful_order_count",
                        "operator": "greater_than_or_equal",
                        "value": 1,
                    }
                ),
            }
        ).json()["id"]
        order_id = "eligibility-existing-order"
        self.addCleanup(
            self._execute,
            "DELETE FROM topup WHERE id=:order_id",
            {"order_id": order_id},
        )
        self.addCleanup(
            self._execute,
            "DELETE FROM promotion_redemptions WHERE order_id=:order_id",
            {"order_id": order_id},
        )
        self._execute(
            """
            INSERT INTO topup (
                id, phone, target_id, nominal, price, amount, promo_id,
                promo_code, promo_original_price, promo_discount_amount,
                promo_snapshot, promo_snapshot_version,
                payment_status, topup_status
            ) VALUES (
                :id, '081277700001', 'existing-target', 'ff50', 9000, 9000,
                :promo_id, 'HISTORI', 10000, 1000, :snapshot, 2,
                'PAID', 'SUCCESS'
            )
            """,
            {
                "id": order_id,
                "promo_id": promo_id,
                "snapshot": '{"immutable":"order-snapshot"}',
            },
        )
        self._execute(
            """
            INSERT INTO promotion_redemptions (
                promo_id, order_id, status, customer_hash, target_hash,
                product_sku, payment_method, original_amount, discount_amount,
                final_amount, voucher_code, application_source, snapshot_json,
                released_at
            ) VALUES (
                :promo_id, :order_id, 'RELEASED', 'customer-hash', 'target-hash',
                'ff50', 'DANA', 10000, 1000, 9000, 'HISTORI', 'voucher',
                :snapshot, CURRENT_TIMESTAMP
            )
            """,
            {
                "promo_id": promo_id,
                "order_id": order_id,
                "snapshot": '{"immutable":"redemption-snapshot"}',
            },
        )
        order_before = self._query(
            """
            SELECT promo_id, promo_code, promo_original_price,
                   promo_discount_amount, promo_snapshot, promo_snapshot_version,
                   payment_status, topup_status
            FROM topup WHERE id=:order_id
            """,
            {"order_id": order_id},
        )
        redemption_before = self._query(
            """
            SELECT promo_id, status, original_amount, discount_amount,
                   final_amount, snapshot_json, released_at
            FROM promotion_redemptions WHERE order_id=:order_id
            """,
            {"order_id": order_id},
        )

        response = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "eligibility_rules": self._eligibility(
                    {
                        "field": "successful_order_total",
                        "operator": "greater_than_or_equal",
                        "value": 500_000,
                    }
                )
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self._query(
                """
                SELECT promo_id, promo_code, promo_original_price,
                       promo_discount_amount, promo_snapshot, promo_snapshot_version,
                       payment_status, topup_status
                FROM topup WHERE id=:order_id
                """,
                {"order_id": order_id},
            ),
            order_before,
        )
        self.assertEqual(
            self._query(
                """
                SELECT promo_id, status, original_amount, discount_amount,
                       final_amount, snapshot_json, released_at
                FROM promotion_redemptions WHERE order_id=:order_id
                """,
                {"order_id": order_id},
            ),
            redemption_before,
        )

    def test_update_targets_and_payment_methods_replaces_relations_without_duplicates(self):
        promo_id = self._create(
            {
                **self._discount("Edit relasi", "fixed", 1_000),
                "target_option_ids": [self.first_target_id, self.first_target_id],
                "payment_methods": ["QRIS", "QRIS"],
            }
        ).json()["id"]
        initial_relation_count = self._query(
            "SELECT COUNT(*) FROM promotion_targets WHERE promo_id=:promo_id",
            {"promo_id": promo_id},
        )[0][0]
        self.assertEqual(initial_relation_count, 1)

        response = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers=self.headers,
            json={
                "target_option_ids": [self.second_target_id, self.second_target_id],
                "payment_methods": ["DANA", "BRIVA", "DANA"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = self._detail(promo_id)
        self.assertEqual(updated["target_option_ids"], [self.second_target_id])
        self.assertEqual(updated["targets"][0]["target_key"], self.second_target_sku)
        self.assertEqual(updated["payment_methods"], ["DANA", "BRIVA"])
        relation_count = self._query(
            "SELECT COUNT(*) FROM promotion_targets WHERE promo_id=:promo_id",
            {"promo_id": promo_id},
        )[0][0]
        self.assertEqual(relation_count, 1)

    def test_activate_disable_and_duplicate_keep_original_available(self):
        banner_id = self._create(self._banner("Lifecycle banner")).json()["id"]
        activated = self.client.post(
            f"/admin/api/promos/{banner_id}/actions/activate",
            headers=self.headers,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "active")
        self.assertTrue(self._detail(banner_id)["active"])

        disabled = self.client.post(
            f"/admin/api/promos/{banner_id}/actions/disable",
            headers=self.headers,
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(disabled.json()["status"], "disabled")
        self.assertFalse(self._detail(banner_id)["active"])

        voucher_id = self._create(
            {
                **self._discount("Voucher sumber", "fixed", 1_000),
                "promo_type": "voucher",
                "code": "SOURCE10",
                "target_option_ids": [self.first_target_id],
                "payment_methods": ["QRIS"],
            }
        ).json()["id"]
        duplicated = self.client.post(
            f"/admin/api/promos/{voucher_id}/duplicate",
            headers=self.headers,
        )
        self.assertEqual(duplicated.status_code, 200, duplicated.text)
        duplicate_id = duplicated.json()["id"]
        self.assertNotEqual(duplicate_id, voucher_id)
        original = self._detail(voucher_id)
        copy = self._detail(duplicate_id)
        self.assertEqual(original["code"], "SOURCE10")
        self.assertEqual(copy["code"], "")
        self.assertEqual(copy["promo_type"], "automatic")
        self.assertEqual(copy["lifecycle_status"], "draft")
        self.assertEqual(copy["target_option_ids"], [self.first_target_id])
        self.assertEqual(copy["payment_methods"], ["QRIS"])

    def test_create_rolls_back_when_relation_insert_fails(self):
        async def fail_relation_insert(promo_id, *, included_ids=None, connection=None, **kwargs):
            self.assertIsNotNone(connection, "Relation writes must share the create transaction")
            await self.database.db_execute(
                """
                INSERT INTO promotion_targets (promo_id, target_option_id, excluded)
                VALUES (:promo_id, :option_id, 0)
                """,
                {"promo_id": promo_id, "option_id": (included_ids or [self.first_target_id])[0]},
                connection=connection,
            )
            raise RuntimeError("forced relation insert failure")

        title = "Create harus rollback"
        eligibility_rules = self._eligibility(
            {
                "field": "successful_order_count",
                "operator": "equals",
                "value": 0,
            }
        )
        with patch.object(self.admin_routes, "_replace_promo_relations", new=fail_relation_insert):
            response = self.client.post(
                "/admin/api/promos",
                headers=self.headers,
                json={
                    **self._discount(title, "fixed", 1_000),
                    "target_option_ids": [self.first_target_id],
                    "eligibility_rules": eligibility_rules,
                },
            )
        self.assertEqual(response.status_code, 500, response.text)
        rows = self._query("SELECT id FROM promos WHERE title=:title", {"title": title})
        self.assertEqual(rows, [], "A failed target insert must roll back the promo row")

    def test_update_rolls_back_fields_and_relations_when_relation_insert_fails(self):
        original_rules = self._eligibility(
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
        promo_id = self._create(
            {
                **self._discount("Update sebelum gagal", "fixed", 1_000),
                "target_option_ids": [self.first_target_id],
                "eligibility_rules": original_rules,
            }
        ).json()["id"]

        async def fail_relation_insert(promo_id, *, included_ids=None, connection=None, **kwargs):
            self.assertIsNotNone(connection, "Relation writes must share the update transaction")
            await self.database.db_execute(
                "DELETE FROM promotion_targets WHERE promo_id=:promo_id",
                {"promo_id": promo_id},
                connection=connection,
            )
            await self.database.db_execute(
                """
                INSERT INTO promotion_targets (promo_id, target_option_id, excluded)
                VALUES (:promo_id, :option_id, 0)
                """,
                {"promo_id": promo_id, "option_id": (included_ids or [self.second_target_id])[0]},
                connection=connection,
            )
            raise RuntimeError("forced relation update failure")

        with patch.object(self.admin_routes, "_replace_promo_relations", new=fail_relation_insert):
            response = self.client.put(
                f"/admin/api/promos/{promo_id}",
                headers=self.headers,
                json={
                    "title": "Update setengah jadi",
                    "target_option_ids": [self.second_target_id],
                    "eligibility_rules": self._eligibility(
                        {
                            "field": "successful_order_count",
                            "operator": "greater_than_or_equal",
                            "value": 10,
                        }
                    ),
                },
            )
        self.assertEqual(response.status_code, 500, response.text)
        persisted = self._detail(promo_id)
        self.assertEqual(persisted["title"], "Update sebelum gagal")
        self.assertEqual(persisted["target_option_ids"], [self.first_target_id])
        self.assertEqual(persisted["eligibility_rules"], original_rules)
        raw_rules = self._query(
            "SELECT eligibility_rules FROM promos WHERE id=:promo_id",
            {"promo_id": promo_id},
        )[0][0]
        self.assertEqual(json.loads(raw_rules), original_rules)

    def test_validation_error_locations_distinguish_scalar_and_array_item(self):
        scalar = self._create(
            {**self._discount("Scalar loc", "percent", None), "discount_value": "not-a-number"},
            expected_status=422,
        )
        scalar_error = scalar.json()["detail"][0]
        self.assertEqual(scalar_error["loc"], ["body", "discount_value"])
        self.assertEqual(scalar_error["input"], "not-a-number")

        array = self._create(
            {
                **self._banner("Array loc"),
                "target_option_ids": [self.first_target_id, ""],
            },
            expected_status=422,
        )
        array_error = array.json()["detail"][0]
        self.assertEqual(array_error["loc"], ["body", "target_option_ids", 1])
        self.assertEqual(array_error["input"], "")

        active_day = self._create(
            {**self._banner("Active day loc"), "active_days": [1, ""]},
            expected_status=422,
        )
        active_day_error = active_day.json()["detail"][0]
        self.assertEqual(active_day_error["loc"], ["body", "active_days", 1])
        self.assertEqual(active_day_error["input"], "")


if __name__ == "__main__":
    unittest.main()
