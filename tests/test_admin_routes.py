import asyncio
import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AdminRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.previous_env = {
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "DEFAULT_ADMIN_USERNAME": os.environ.get("DEFAULT_ADMIN_USERNAME"),
            "DEFAULT_ADMIN_PASSWORD": os.environ.get("DEFAULT_ADMIN_PASSWORD"),
        }
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"
        os.environ["DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "lixafa123"

        import importlib

        from app.core import settings as core_settings
        from app.core import database as core_database
        from app import database as legacy_database
        from app.routes import admin_routes as admin_routes

        importlib.reload(core_settings)
        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(admin_routes)

        self.app = FastAPI()
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

    def login(self, username="admin", password="lixafa123"):
        response = self.client.post(
            "/admin/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"], response.json()

    def test_login_with_default_admin_credentials(self):
        response = self.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())
        self.assertEqual(response.json()["role"], "owner")
        self.assertIn("admins:manage", response.json()["permissions"])
        self.assertIn("api:monitor", response.json()["permissions"])

    def test_protected_endpoint_requires_auth(self):
        response = self.client.get("/admin/api/products")
        self.assertEqual(response.status_code, 401)

    def test_product_cost_alias_matches_dashboard_contract(self):
        token, _ = self.login()

        create_response = self.client.post(
            "/admin/api/products",
            headers={"token": token},
            json={
                "provider": "Test Provider",
                "name": "Test Product",
                "sku": "test-product",
                "cost": 8000,
                "price": 10000,
                "category": "Test Category",
                "description": "Produk test metadata",
                "promo_badge": "Hot",
                "display_order": 3,
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/admin/api/products", headers={"token": token})
        self.assertEqual(list_response.status_code, 200)

        product = list_response.json()["Test Category"]["Test Provider"][0]
        self.assertEqual(product["cost"], 8000)
        self.assertEqual(product["cost_price"], 8000)
        self.assertEqual(product["description"], "Produk test metadata")
        self.assertEqual(product["promo_badge"], "Hot")
        self.assertEqual(product["display_order"], 3)

        update_response = self.client.put(
            "/admin/api/products/test-product",
            headers={"token": token},
            json={
                "provider": "Test Provider",
                "name": "Test Product Update",
                "cost": 9000,
                "price": 12000,
                "category": "Test Category",
                "promo_text": "Promo metadata update",
            },
        )
        self.assertEqual(update_response.status_code, 200)

        updated_list = self.client.get("/admin/api/products", headers={"token": token})
        updated_product = updated_list.json()["Test Category"]["Test Provider"][0]
        self.assertEqual(updated_product["name"], "Test Product Update")
        self.assertEqual(updated_product["cost"], 9000)
        self.assertEqual(updated_product["price"], 12000)
        self.assertEqual(updated_product["promo_text"], "Promo metadata update")

    def test_promo_crud_available_from_admin_dashboard(self):
        token, _ = self.login()

        create_response = self.client.post(
            "/admin/api/promos",
            headers={"token": token},
            json={
                "title": "Diskon Test",
                "code": "TEST10",
                "description": "Promo dari test admin",
                "badge": "Test",
                "rule_type": "price",
                "target_scope": "sku",
                "target_value": "test-product",
                "discount_type": "percent",
                "discount_value": 10,
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/admin/api/promos", headers={"token": token})
        self.assertEqual(list_response.status_code, 200)
        promos = list_response.json()
        self.assertEqual(len(promos), 1)
        self.assertEqual(promos[0]["code"], "TEST10")

        promo_id = promos[0]["id"]
        update_response = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers={"token": token},
            json={"active": 0},
        )
        self.assertEqual(update_response.status_code, 200)

        delete_response = self.client.delete(f"/admin/api/promos/{promo_id}", headers={"token": token})
        self.assertEqual(delete_response.status_code, 200)

        empty_response = self.client.get("/admin/api/promos", headers={"token": token})
        self.assertEqual(empty_response.json(), [])

    def test_promo_empty_numeric_strings_are_normalized(self):
        token, _ = self.login()

        payload = {
            "title": "Banner tanpa diskon",
            "promo_type": "banner",
            "rule_type": "content",
            "target_scope": "all",
            "discount_type": "",
            "discount_value": "",
            "max_discount": "",
            "minimum_transaction": "",
            "special_price": "",
            "usage_limit": "",
            "quota_daily": "",
            "budget_limit": "",
            "max_per_customer": "",
            "max_per_customer_daily": "",
            "max_per_phone": "",
            "max_per_target": "",
            "max_promotions_per_order": "",
            "priority": "",
            "display_order": "",
            "show_on_website": 1,
            "active": 0,
        }
        create_response = self.client.post("/admin/api/promos", headers={"token": token}, json=payload)
        self.assertEqual(create_response.status_code, 200)

        promo_id = create_response.json()["id"]
        edit_response = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers={"token": token},
            json={**payload, "title": "Banner tanpa diskon edit"},
        )
        self.assertEqual(edit_response.status_code, 200)

    def test_promo_active_days_legacy_names_are_normalized(self):
        token, _ = self.login()

        payload = {
            "title": "Banner hari legacy",
            "promo_type": "banner",
            "rule_type": "content",
            "target_scope": "all",
            "active_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            "daily_start_time": "08:00",
            "daily_end_time": "22:00",
            "show_on_website": 1,
            "active": 0,
        }
        create_response = self.client.post("/admin/api/promos", headers={"token": token}, json=payload)
        self.assertEqual(create_response.status_code, 200)

        promo_id = create_response.json()["id"]
        promos = self.client.get("/admin/api/promos", headers={"token": token}).json()
        self.assertCountEqual(promos[0]["active_days"], [1, 2, 3, 4, 5, 6, 0])

        edit_response = self.client.put(
            f"/admin/api/promos/{promo_id}",
            headers={"token": token},
            json={**payload, "title": "Banner hari legacy edit", "active_days": ["sen", "sel", "rab"]},
        )
        self.assertEqual(edit_response.status_code, 200)

        updated = self.client.get("/admin/api/promos", headers={"token": token}).json()
        self.assertCountEqual(updated[0]["active_days"], [1, 2, 3])

    def test_promo_active_days_invalid_value_reports_field_loc(self):
        token, _ = self.login()

        response = self.client.post(
            "/admin/api/promos",
            headers={"token": token},
            json={
                "title": "Banner hari invalid",
                "promo_type": "banner",
                "rule_type": "content",
                "target_scope": "all",
                "active_days": ["not-a-day"],
                "show_on_website": 1,
                "active": 0,
            },
        )
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail[0]["loc"], ["body", "active_days", 0])
        self.assertEqual(detail[0]["input"], "not-a-day")
        self.assertIn("not-a-day", str(detail[0].get("input", "")))

    def test_promo_rupiah_strings_and_invalid_numeric_values(self):
        token, _ = self.login()

        valid_response = self.client.post(
            "/admin/api/promos",
            headers={"token": token},
            json={
                "title": "Diskon Rupiah",
                "promo_type": "automatic",
                "rule_type": "price",
                "target_scope": "all",
                "discount_type": "fixed",
                "discount_value": "Rp10.000",
                "minimum_transaction": "10.000",
                "usage_limit": "0",
                "max_per_customer": "0",
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(valid_response.status_code, 200)

        invalid_response = self.client.post(
            "/admin/api/promos",
            headers={"token": token},
            json={
                "title": "Diskon Invalid",
                "promo_type": "automatic",
                "rule_type": "price",
                "target_scope": "all",
                "discount_type": "fixed",
                "discount_value": "abc",
                "show_on_website": 1,
                "active": 1,
            },
        )
        self.assertEqual(invalid_response.status_code, 422)
        error = invalid_response.json()["detail"][0]
        self.assertEqual(error["loc"], ["body", "discount_value"])
        self.assertEqual(error["input"], "abc")

    def test_expired_promo_is_flagged_and_not_counted_as_live(self):
        token, _ = self.login()

        create_response = self.client.post(
            "/admin/api/promos",
            headers={"token": token},
            json={
                "title": "Promo Expired",
                "code": "OLD100",
                "description": "Promo lama",
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

        promos_response = self.client.get("/admin/api/promos", headers={"token": token})
        self.assertEqual(promos_response.status_code, 200)
        promos = promos_response.json()
        self.assertEqual(promos[0]["runtime_status"], "expired")
        self.assertEqual(promos[0]["runtime_label"], "Expired")
        self.assertFalse(promos[0]["visible_on_website_now"])

        summary = self.client.get("/admin/api/dashboard-summary", headers={"token": token})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["operational"]["active_promos"], 0)

    def test_owner_can_create_restricted_support_admin(self):
        owner_token, _ = self.login()

        create_response = self.client.post(
            "/admin/api/admins",
            headers={"token": owner_token},
            json={"username": "support", "password": "support123", "role": "support"},
        )
        self.assertEqual(create_response.status_code, 200)

        support_token, support_login = self.login("support", "support123")
        self.assertEqual(support_login["role"], "support")
        self.assertEqual(support_login["permissions"], ["orders:view"])

        orders_response = self.client.get("/admin/api/orders", headers={"token": support_token})
        self.assertEqual(orders_response.status_code, 200)

        products_response = self.client.get("/admin/api/products", headers={"token": support_token})
        self.assertEqual(products_response.status_code, 403)

        admins_response = self.client.get("/admin/api/admins", headers={"token": support_token})
        self.assertEqual(admins_response.status_code, 403)

    def test_owner_can_manage_site_settings_and_pages(self):
        token, _ = self.login()

        settings_response = self.client.put(
            "/admin/api/site-settings",
            headers={"token": token},
            json={"site_name": "LIXAFA Test", "logo_url": "/web/logo.png", "footer_text": "Footer test"},
        )
        self.assertEqual(settings_response.status_code, 200)

        settings_read = self.client.get("/admin/api/site-settings", headers={"token": token})
        self.assertEqual(settings_read.status_code, 200)
        self.assertEqual(settings_read.json()["site_name"], "LIXAFA Test")
        self.assertEqual(settings_read.json()["logo_url"], "/web/logo.png")

        create_page = self.client.post(
            "/admin/api/pages",
            headers={"token": token},
            json={
                "slug": "info-test",
                "title": "Info Test",
                "excerpt": "Ringkasan",
                "content": "Isi konten",
                "active": 1,
                "show_on_website": 1,
            },
        )
        self.assertEqual(create_page.status_code, 200)

        pages = self.client.get("/admin/api/pages", headers={"token": token})
        self.assertEqual(pages.status_code, 200)
        self.assertEqual(pages.json()[0]["slug"], "info-test")
        page_id = pages.json()[0]["id"]

        update_page = self.client.put(
            f"/admin/api/pages/{page_id}",
            headers={"token": token},
            json={"title": "Info Test Update", "active": 0},
        )
        self.assertEqual(update_page.status_code, 200)

        delete_page = self.client.delete(f"/admin/api/pages/{page_id}", headers={"token": token})
        self.assertEqual(delete_page.status_code, 200)

    def test_upload_image_rejects_svg(self):
        token, _ = self.login()

        response = self.client.post(
            "/admin/upload-image",
            headers={"token": token},
            data={"folder": "logos"},
            files={"file": ("logo.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Format file", response.json()["detail"])

    def test_owner_can_view_provider_status_but_support_cannot(self):
        owner_token, _ = self.login()

        response = self.client.get("/admin/api/provider-status", headers={"token": owner_token})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("tripay", payload["providers"])
        self.assertIn("digiflazz", payload["providers"])
        self.assertIn("metrics", payload)
        self.assertIn("recent_orders", payload)
        self.assertIn("configured", payload["providers"]["tripay"])

        rejected_secret_response = self.client.put(
            "/admin/api/provider-settings",
            headers={"token": owner_token},
            json={
                "tripay_api_key": "not-allowed-in-database",
            },
        )
        self.assertEqual(rejected_secret_response.status_code, 400)

        save_response = self.client.put(
            "/admin/api/provider-settings",
            headers={"token": owner_token},
            json={
                "active_payment_provider": "tripay",
                "active_topup_provider": "digiflazz",
                "backup_topup_provider": "",
                "topup_failover_enabled": 0,
                "tripay_base_url": "https://tripay.example/api",
                "digiflazz_base_url": "https://digiflazz.example/v1",
                "digiflazz_username": "df-user",
            },
        )
        self.assertEqual(save_response.status_code, 200)

        updated_response = self.client.get("/admin/api/provider-status", headers={"token": owner_token})
        self.assertEqual(updated_response.status_code, 200)
        updated = updated_response.json()
        self.assertEqual(updated["providers"]["tripay"]["base_url"], "https://tripay.example/api")
        self.assertEqual(updated["providers"]["digiflazz"]["base_url"], "https://digiflazz.example/v1")
        self.assertEqual(updated["providers"]["digiflazz"]["username"], "df-user")
        self.assertNotIn("not-allowed-in-database", str(updated))

        create_response = self.client.post(
            "/admin/api/admins",
            headers={"token": owner_token},
            json={"username": "support2", "password": "support123", "role": "support"},
        )
        self.assertEqual(create_response.status_code, 200)

        support_token, _ = self.login("support2", "support123")
        support_response = self.client.get("/admin/api/provider-status", headers={"token": support_token})
        self.assertEqual(support_response.status_code, 403)

    def test_owner_can_operate_orders_reports_customers_and_audit(self):
        owner_token, _ = self.login()

        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nickname, nominal, price, product_cost,
                    amount, payment_method, payment_fee, payment_status, topup_status,
                    sn, note, status_updated_at
                )
                VALUES (
                    :id, :phone, :target_id, :nickname, :nominal, :price, :product_cost,
                    :amount, :payment_method, :payment_fee, :payment_status, :topup_status,
                    :sn, :note, CURRENT_TIMESTAMP
                )
                """,
                {
                    "id": "order-admin-test",
                    "phone": "081234567890",
                    "target_id": "12345678",
                    "nickname": "Tester",
                    "nominal": "ml_10k",
                    "price": 10000,
                    "product_cost": 8000,
                    "amount": 12000,
                    "payment_method": "QRIS",
                    "payment_fee": 2000,
                    "payment_status": "PAID",
                    "topup_status": "SUCCESS",
                    "sn": "SN123",
                    "note": "ok",
                },
            )
        )

        summary = self.client.get("/admin/api/dashboard-summary", headers={"token": owner_token})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["total"]["revenue"], 12000)
        self.assertEqual(summary.json()["total"]["realized_profit"], 4000)

        detail = self.client.get("/admin/api/orders/order-admin-test", headers={"token": owner_token})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["product_cost"], 8000)

        status_update = self.client.put(
            "/admin/api/orders/order-admin-test/status",
            headers={"token": owner_token},
            json={"topup_status": "FAILED", "note": "manual check"},
        )
        self.assertEqual(status_update.status_code, 409)

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nickname, nominal, price, product_cost,
                    amount, payment_method, payment_fee, payment_status, topup_status,
                    sn, note, status_updated_at
                )
                VALUES (
                    'order-admin-retry', '081234567891', '12345679', 'Retry Tester', 'ml_10k', 10000, 8000,
                    12000, 'QRIS', 2000, 'PAID', 'FAILED',
                    '', 'provider failed', CURRENT_TIMESTAMP
                )
                """
            )
        )
        retry = self.client.post(
            "/admin/api/orders/order-admin-retry/retry",
            headers={"token": owner_token},
            json={"reason": "retry failed provider"},
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["order"]["topup_status"], "PROCESSING")

        refund = self.client.post(
            "/admin/api/orders/order-admin-test/refund",
            headers={"token": owner_token},
            json={"note": "refund test"},
        )
        self.assertEqual(refund.status_code, 200)
        self.assertEqual(refund.json()["order"]["payment_status"], "REFUNDED")

        report = self.client.get("/admin/api/reports/finance", headers={"token": owner_token})
        self.assertEqual(report.status_code, 200)
        self.assertIn("summary", report.json())

        export = self.client.get("/admin/api/reports/export", headers={"token": owner_token})
        self.assertEqual(export.status_code, 200)
        self.assertIn("order-admin-test", export.text)

        block = self.client.post(
            "/admin/api/customers/block",
            headers={"token": owner_token},
            json={"phone": "081234567890", "reason": "test block"},
        )
        self.assertEqual(block.status_code, 200)

        customers = self.client.get("/admin/api/customers", headers={"token": owner_token})
        self.assertEqual(customers.status_code, 200)
        blocked_customer = next(item for item in customers.json() if item["phone"] == "081234567890")
        self.assertTrue(blocked_customer["blocked"])

        audit_logs = self.client.get("/admin/api/audit-logs", headers={"token": owner_token})
        self.assertEqual(audit_logs.status_code, 200)
        self.assertGreaterEqual(len(audit_logs.json()), 1)

    def test_success_order_cannot_be_retried(self):
        owner_token, _ = self.login()

        from app import database as legacy_database

        asyncio.run(
            legacy_database.db_execute(
                """
                INSERT INTO topup (
                    id, phone, target_id, nominal, amount, payment_status, topup_status, sn
                )
                VALUES (
                    'order-success-retry-blocked', '081234567890', '12345678',
                    'ml_10k', 10000, 'PAID', 'SUCCESS', 'SN-SUCCESS'
                )
                """
            )
        )

        retry = self.client.post(
            "/admin/api/orders/order-success-retry-blocked/retry",
            headers={"token": owner_token},
            json={"reason": "test retry success"},
        )
        self.assertEqual(retry.status_code, 409)
        self.assertIn("SUCCESS", retry.json()["detail"])

        detail = self.client.get(
            "/admin/api/orders/order-success-retry-blocked",
            headers={"token": owner_token},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["topup_status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
