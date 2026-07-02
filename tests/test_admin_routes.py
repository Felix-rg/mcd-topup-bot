import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class AdminRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.temp_db.name}"

        import importlib

        import core.database as core_database
        import database as legacy_database
        import routes.admin_routes as admin_routes

        importlib.reload(core_database)
        importlib.reload(legacy_database)
        importlib.reload(admin_routes)

        self.app = FastAPI()
        self.app.include_router(admin_routes.router)
        self.client = TestClient(self.app)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_login_with_default_admin_credentials(self):
        response = self.client.post(
            "/admin/login",
            json={"username": "admin", "password": "lixafa123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

    def test_protected_endpoint_requires_auth(self):
        response = self.client.get("/admin/api/products")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
