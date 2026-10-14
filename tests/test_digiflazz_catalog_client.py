"""Unit safety coverage for the isolated Digiflazz price-list client.

Every HTTP client in this module is a local fake.  These tests prove the
catalog capability gate and request contract without contacting Digiflazz or
reading a real provider configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from app.services import digiflazz_catalog_client as catalog_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, calls: list[dict[str, Any]]) -> None:
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        self._calls.append({"url": url, "json": dict(json)})
        return self._response


class DigiflazzCatalogClientTests(unittest.TestCase):
    def test_disabled_capability_stops_before_config_or_http(self) -> None:
        with patch.object(
            catalog_client, "is_digiflazz_capability_enabled", return_value=False
        ), patch.object(catalog_client, "get_digiflazz_catalog_config", new=AsyncMock()) as get_config:
            with self.assertRaises(catalog_client.DigiflazzCatalogClientError) as raised:
                asyncio.run(catalog_client.DigiflazzCatalogClient().fetch_prepaid_price_list())

        self.assertEqual(raised.exception.code, "catalog_price_list_disabled")
        get_config.assert_not_awaited()

    def test_prepaid_request_is_minimal_and_never_sends_api_key(self) -> None:
        calls: list[dict[str, Any]] = []
        config = {
            "username": "catalog-test-user",
            "api_key": "catalog-test-secret",
            "base_url": "https://catalog.example.invalid/api",
        }
        response = _FakeResponse(
            200,
            {
                "data": [
                    {
                        "buyer_sku_code": "SKU-1",
                        "product_name": "Pulsa 10 ribu",
                        "unexpected_provider_field": "not persisted by normalizer",
                    }
                ]
            },
        )

        def fake_http_client(*, timeout: float) -> _FakeAsyncClient:
            self.assertEqual(timeout, catalog_client.CATALOG_HTTP_TIMEOUT_SECONDS)
            return _FakeAsyncClient(response, calls)

        with patch.object(catalog_client, "is_digiflazz_capability_enabled", return_value=True), patch.object(
            catalog_client, "get_digiflazz_catalog_config", new=AsyncMock(return_value=config)
        ), patch.object(catalog_client.httpx, "AsyncClient", new=fake_http_client):
            result = asyncio.run(catalog_client.DigiflazzCatalogClient().fetch_prepaid_price_list())

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "https://catalog.example.invalid/api/price-list")
        self.assertEqual(calls[0]["json"]["cmd"], "prepaid")
        self.assertEqual(calls[0]["json"]["username"], "catalog-test-user")
        self.assertEqual(
            calls[0]["json"]["sign"],
            hashlib.md5(b"catalog-test-usercatalog-test-secretpricelist").hexdigest(),
        )
        self.assertNotIn("api_key", calls[0]["json"])
        self.assertEqual(result.rows[0]["buyer_sku_code"], "SKU-1")

    def test_non_https_or_credential_bearing_endpoint_is_rejected_before_http(self) -> None:
        for base_url in ("http://catalog.example.invalid", "https://user:pass@catalog.example.invalid/v1"):
            with self.subTest(base_url=base_url), patch.object(
                catalog_client, "is_digiflazz_capability_enabled", return_value=True
            ), patch.object(
                catalog_client,
                "get_digiflazz_catalog_config",
                new=AsyncMock(
                    return_value={
                        "username": "catalog-test-user",
                        "api_key": "catalog-test-secret",
                        "base_url": base_url,
                    }
                ),
            ), patch.object(
                catalog_client.httpx,
                "AsyncClient",
                side_effect=AssertionError("unsafe endpoint must be rejected before HTTP"),
            ) as http_client:
                with self.assertRaises(catalog_client.DigiflazzCatalogClientError) as raised:
                    asyncio.run(catalog_client.DigiflazzCatalogClient().fetch_prepaid_price_list())

            self.assertEqual(raised.exception.code, "catalog_configuration_invalid")
            http_client.assert_not_called()

    def test_provider_body_and_secret_never_reach_safe_error(self) -> None:
        secret = "catalog-test-secret"
        body_secret = "provider-raw-body-secret"
        response = _FakeResponse(403, {"message": body_secret, "api_key": secret})
        calls: list[dict[str, Any]] = []

        with patch.object(catalog_client, "is_digiflazz_capability_enabled", return_value=True), patch.object(
            catalog_client,
            "get_digiflazz_catalog_config",
            new=AsyncMock(
                return_value={
                    "username": "catalog-test-user",
                    "api_key": secret,
                    "base_url": "https://catalog.example.invalid",
                }
            ),
        ), patch.object(
            catalog_client.httpx,
            "AsyncClient",
            new=lambda *, timeout: _FakeAsyncClient(response, calls),
        ):
            with self.assertRaises(catalog_client.DigiflazzCatalogClientError) as raised:
                asyncio.run(catalog_client.DigiflazzCatalogClient().fetch_prepaid_price_list())

        self.assertEqual(raised.exception.code, "catalog_provider_http_error")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(body_secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
