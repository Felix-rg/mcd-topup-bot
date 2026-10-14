"""Dedicated, opt-in client for the Digiflazz Buyer price-list endpoint.

Catalog discovery is intentionally isolated from the transaction client.  This
module has no dependency on order, wallet, payment, or provider retry code and
only ever calls the prepaid ``price-list`` endpoint after its environment-only
capability has been enabled.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from app.services.digiflazz_capabilities import (
    DIGIFLAZZ_CAPABILITY_CATALOG_PRICE_LIST,
    is_digiflazz_capability_enabled,
)
from app.services.provider_settings import get_digiflazz_catalog_config


logger = logging.getLogger(__name__)

CATALOG_HTTP_TIMEOUT_SECONDS = 20.0


class DigiflazzCatalogClientError(RuntimeError):
    """A safe, classified catalog-fetch failure.

    ``message`` is deliberately suitable for an admin response.  It must not
    include response bodies, request payloads, URLs, or credentials.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class DigiflazzCatalogResponse:
    """The only response shape exposed to catalog normalization."""

    rows: Sequence[Mapping[str, Any]]


def _price_list_signature(username: str, api_key: str) -> str:
    return hashlib.md5(f"{username}{api_key}pricelist".encode("utf-8")).hexdigest()


class DigiflazzCatalogClient:
    """Fetch prepaid price-list candidates without exposing provider secrets."""

    def __init__(self, *, timeout_seconds: float = CATALOG_HTTP_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = float(timeout_seconds)

    async def fetch_prepaid_price_list(self) -> DigiflazzCatalogResponse:
        """Fetch one prepaid price list.

        The capability is checked before configuration is read, which ensures
        a disabled catalog cannot accidentally disclose configuration usage or
        make an outbound request.
        """

        if not is_digiflazz_capability_enabled(DIGIFLAZZ_CAPABILITY_CATALOG_PRICE_LIST):
            raise DigiflazzCatalogClientError(
                "catalog_price_list_disabled",
                "Price-list Digiflazz belum diizinkan oleh environment.",
            )

        config = await get_digiflazz_catalog_config()
        username = str(config.get("username") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        if not username or not api_key or not base_url:
            raise DigiflazzCatalogClientError(
                "catalog_configuration_incomplete",
                "Konfigurasi katalog Digiflazz belum lengkap.",
            )
        parsed_url = urlsplit(base_url)
        if (
            parsed_url.scheme.casefold() != "https"
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            # Do not include a malformed configured value in the message or
            # log: it may itself carry a credential-bearing URL.
            raise DigiflazzCatalogClientError(
                "catalog_configuration_invalid",
                "Endpoint katalog Digiflazz harus berupa URL HTTPS yang aman.",
            )

        payload = {
            "cmd": "prepaid",
            "username": username,
            "sign": _price_list_signature(username, api_key),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(f"{base_url}/price-list", json=payload)
        except httpx.TimeoutException:
            logger.warning("Digiflazz catalog price-list timed out")
            raise DigiflazzCatalogClientError(
                "catalog_provider_timeout",
                "Price-list Digiflazz melewati batas waktu.",
            ) from None
        except httpx.HTTPError as exc:
            logger.warning("Digiflazz catalog transport failure error_type=%s", type(exc).__name__)
            raise DigiflazzCatalogClientError(
                "catalog_provider_transport_error",
                "Koneksi price-list Digiflazz gagal.",
            ) from None

        if response.status_code < 200 or response.status_code >= 300:
            logger.warning("Digiflazz catalog price-list rejected status=%s", response.status_code)
            raise DigiflazzCatalogClientError(
                "catalog_provider_http_error",
                "Price-list Digiflazz menolak permintaan katalog.",
            )

        try:
            payload_data = response.json()
        except ValueError:
            logger.warning("Digiflazz catalog price-list returned invalid JSON")
            raise DigiflazzCatalogClientError(
                "catalog_provider_invalid_response",
                "Price-list Digiflazz mengembalikan format tidak valid.",
            ) from None

        if not isinstance(payload_data, Mapping):
            raise DigiflazzCatalogClientError(
                "catalog_provider_invalid_response",
                "Price-list Digiflazz mengembalikan format tidak valid.",
            )
        rows = payload_data.get("data")
        if not isinstance(rows, list):
            # Provider errors can contain messages and account details.  Do not
            # retain or relay that body to application logs/API responses.
            logger.warning("Digiflazz catalog price-list returned no row list")
            raise DigiflazzCatalogClientError(
                "catalog_provider_rejected",
                "Price-list Digiflazz tidak menyediakan daftar kandidat.",
            )
        if not all(isinstance(row, Mapping) for row in rows):
            raise DigiflazzCatalogClientError(
                "catalog_provider_invalid_response",
                "Price-list Digiflazz memiliki baris kandidat tidak valid.",
            )
        return DigiflazzCatalogResponse(rows=tuple(rows))
