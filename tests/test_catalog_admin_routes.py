"""Admin routing/permission regression for Phase 2 catalog controls."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class CatalogAdminRouteTests(unittest.TestCase):
    def setUp(self) -> None:
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

        from app.core import database as core_database
        from app.core import settings as core_settings
        from app import database as legacy_database
        from app.routes import admin_routes

        importlib.reload(core_settings)
        importlib.reload(core_database)
        importlib.reload(legacy_database)
        self.routes = importlib.reload(admin_routes)
        self.core_database = core_database
        self.app = FastAPI()
        self.app.include_router(self.routes.router)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        if self.core_database.engine is not None:
            import asyncio

            asyncio.run(self.core_database.engine.dispose())
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def _login(self, username: str = "admin", password: str = "lixafa123") -> str:
        response = self.client.post("/admin/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def test_catalog_endpoints_are_owner_visible_and_delegated_without_provider_calls(self) -> None:
        token = self._login()
        me = self.client.get("/admin/api/me", headers={"token": token})
        self.assertEqual(me.status_code, 200)
        self.assertTrue({"catalog:preview", "catalog:review", "catalog:apply"}.issubset(set(me.json()["permissions"])))

        status = {
            "provider_code": "digiflazz",
            "catalog_scope": "prepaid",
            "last_attempt_at": None,
            "next_allowed_at": None,
            "last_success_at": None,
            "last_successful_run_id": None,
            "last_failure_code": None,
            "safe_failure_summary": None,
            "last_preview": None,
        }
        preview_result = {"success": True, "run": {"run_id": "preview-1", "status": "PREVIEW_READY"}}
        runs_result = {"items": [], "limit": 50}
        detail_result = {"run": {"run_id": "preview-1"}, "items": [], "can_apply": True, "blocked_reasons": []}
        decision_result = {"success": True, "review": {"review_id": "review-1", "status": "APPROVED"}, "created_logical_product": None}
        apply_result = {"success": True, "already_applied": False, "run": {"run_id": "apply-1", "status": "APPLIED"}}
        candidates_result = {"logical_product": {"catalog_product_id": "logical-1"}, "items": []}

        with patch.object(self.routes, "catalog_sync_status", new=AsyncMock(return_value=status)) as status_call, patch.object(
            self.routes, "preview_catalog_sync", new=AsyncMock(return_value=preview_result)
        ) as preview_call, patch.object(self.routes, "list_catalog_sync_runs", new=AsyncMock(return_value=runs_result)), patch.object(
            self.routes, "get_catalog_sync_run_detail", new=AsyncMock(return_value=detail_result)
        ), patch.object(self.routes, "decide_catalog_mapping", new=AsyncMock(return_value=decision_result)), patch.object(
            self.routes, "apply_catalog_snapshot", new=AsyncMock(return_value=apply_result)
        ), patch.object(self.routes, "provider_candidates_for_logical_product", new=AsyncMock(return_value=candidates_result)), patch.object(
            self.routes, "list_catalog_logical_products", new=AsyncMock(return_value={"items": [], "limit": 100})
        ):
            self.assertEqual(self.client.get("/admin/api/catalog/status", headers={"token": token}).json()["provider_code"], "digiflazz")
            self.assertEqual(self.client.get("/admin/api/catalog/sync-runs", headers={"token": token}).status_code, 200)
            self.assertEqual(self.client.get("/admin/api/catalog/sync-runs/preview-1", headers={"token": token}).status_code, 200)
            self.assertEqual(self.client.get("/admin/api/catalog/logical-products", headers={"token": token}).status_code, 200)
            self.assertEqual(
                self.client.get("/admin/api/catalog/products/logical-1/provider-candidates", headers={"token": token}).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    "/admin/api/catalog/digiflazz/prepaid/preview",
                    headers={"token": token},
                    json={"provider": "digiflazz", "scope": "prepaid"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post(
                    "/admin/api/catalog/mapping-reviews/review-1",
                    headers={"token": token},
                    json={"decision": "reject"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.post("/admin/api/catalog/sync-runs/preview-1/apply", headers={"token": token}).status_code,
                200,
            )
        status_call.assert_awaited_once()
        preview_call.assert_awaited_once()
        self.assertNotIn("api_key", str(status))

    def test_catalog_apply_is_owner_only_while_catalog_role_can_preview_and_review(self) -> None:
        owner_token = self._login()
        create = self.client.post(
            "/admin/api/admins",
            headers={"token": owner_token},
            json={"username": "cataloguser", "password": "catalog123", "role": "catalog"},
        )
        self.assertEqual(create.status_code, 200)
        catalog_token = self._login("cataloguser", "catalog123")

        with patch.object(
            self.routes,
            "preview_catalog_sync",
            new=AsyncMock(return_value={"success": True, "run": {"run_id": "preview-1", "status": "PREVIEW_READY"}}),
        ) as preview_call, patch.object(
            self.routes,
            "decide_catalog_mapping",
            new=AsyncMock(return_value={"success": True, "review": {"review_id": "review-1", "status": "REJECTED"}}),
        ):
            preview = self.client.post(
                "/admin/api/catalog/digiflazz/prepaid/preview",
                headers={"token": catalog_token},
                json={},
            )
            self.assertEqual(preview.status_code, 200)
            review = self.client.post(
                "/admin/api/catalog/mapping-reviews/review-1",
                headers={"token": catalog_token},
                json={"decision": "reject"},
            )
            self.assertEqual(review.status_code, 200)
            preview_call.assert_awaited_once()

        apply = self.client.post("/admin/api/catalog/sync-runs/preview-1/apply", headers={"token": catalog_token})
        self.assertEqual(apply.status_code, 403)


if __name__ == "__main__":
    unittest.main()
