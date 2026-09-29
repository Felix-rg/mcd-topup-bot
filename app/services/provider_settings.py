from typing import Any, Dict

from app import database as app_database
from app.core.settings import settings


PROVIDER_SETTING_PREFIX = "provider."

SECRET_PROVIDER_KEYS = {
    "tripay_api_key",
    "tripay_private_key",
    "tripay_merchant_code",
    "digiflazz_api_key",
    "digiflazz_webhook_secret",
}

PROVIDER_DEFAULTS = {
    "active_payment_provider": "tripay",
    "active_topup_provider": "digiflazz",
    "backup_topup_provider": "",
    "topup_failover_enabled": "0",
    "tripay_invoice_expiry_minutes": "30",
    "digiflazz_testing_enabled": "0",
    "digiflazz_max_price_margin_percent": "10",
    "digiflazz_low_balance_threshold": "50000",
    "tripay_wallet_sync_interval_seconds": "120",
    "digiflazz_product_auto_sync_enabled": "1",
    "digiflazz_product_sync_interval_minutes": "360",
    "tripay_base_url": settings.resolved_tripay_base_url,
    "tripay_api_key": settings.tripay_api_key,
    "tripay_private_key": settings.tripay_private_key,
    "tripay_merchant_code": settings.tripay_merchant_code,
    "digiflazz_base_url": settings.digiflazz_base_url,
    "digiflazz_username": settings.digiflazz_username,
    "digiflazz_api_key": settings.digiflazz_api_key,
    "digiflazz_webhook_secret": settings.digiflazz_webhook_secret,
}


def _storage_key(key: str) -> str:
    return f"{PROVIDER_SETTING_PREFIX}{key}"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def mask_secret(value: Any) -> str:
    # Even a prefix/suffix makes an environment credential unnecessarily
    # observable through the admin status response.  Presence is enough for
    # operations; the value itself never leaves process configuration.
    return "configured" if _clean(value) else ""


async def get_provider_overrides() -> Dict[str, str]:
    rows = await app_database.db_query(
        """
        SELECT key, value
        FROM site_settings
        WHERE key LIKE :prefix
        """,
        {"prefix": f"{PROVIDER_SETTING_PREFIX}%"},
    )
    result: Dict[str, str] = {}
    for key, value in rows:
        short_key = str(key).replace(PROVIDER_SETTING_PREFIX, "", 1)
        result[short_key] = value or ""
    return result


async def get_provider_runtime_settings() -> Dict[str, str]:
    overrides = await get_provider_overrides()
    runtime: Dict[str, str] = {}
    for key, default in PROVIDER_DEFAULTS.items():
        if key in SECRET_PROVIDER_KEYS:
            # Sensitive provider credentials are intentionally environment-only.
            # Existing database values are ignored rather than becoming a runtime
            # source of truth.
            runtime[key] = _clean(default)
            continue
        override = overrides.get(key)
        if override is None or override == "":
            runtime[key] = _clean(default)
            continue
        runtime[key] = _clean(override)
    return runtime


async def update_provider_runtime_settings(updates: Dict[str, Any], updated_by: str) -> None:
    for key in PROVIDER_DEFAULTS:
        if key not in updates:
            continue

        if key in SECRET_PROVIDER_KEYS:
            # Keep direct service callers from persisting credentials even if an
            # API validation layer is bypassed.
            continue

        raw_value = updates.get(key)
        if raw_value is None:
            continue

        value = _clean(raw_value)
        await app_database.db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, :updated_by)
            ON CONFLICT(key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            {"key": _storage_key(key), "value": value, "updated_by": updated_by},
        )


async def get_tripay_config() -> Dict[str, str]:
    runtime = await get_provider_runtime_settings()
    return {
        "api_key": runtime["tripay_api_key"],
        "private_key": runtime["tripay_private_key"],
        "merchant_code": runtime["tripay_merchant_code"],
        "base_url": runtime["tripay_base_url"] or settings.resolved_tripay_base_url,
    }


async def get_digiflazz_config() -> Dict[str, str]:
    runtime = await get_provider_runtime_settings()
    return {
        "username": runtime["digiflazz_username"],
        "api_key": runtime["digiflazz_api_key"],
        "webhook_secret": runtime["digiflazz_webhook_secret"],
        "base_url": runtime["digiflazz_base_url"] or settings.digiflazz_base_url,
    }


def build_provider_admin_payload(runtime: Dict[str, str]) -> Dict[str, Any]:
    tripay_configured = all(
        [
            runtime.get("tripay_api_key"),
            runtime.get("tripay_private_key"),
            runtime.get("tripay_merchant_code"),
        ]
    )
    digiflazz_configured = all(
        [
            runtime.get("digiflazz_username"),
            runtime.get("digiflazz_api_key"),
        ]
    )

    return {
        "active_payment_provider": runtime.get("active_payment_provider") or "tripay",
        "active_topup_provider": runtime.get("active_topup_provider") or "digiflazz",
        "backup_topup_provider": runtime.get("backup_topup_provider") or "",
        "topup_failover_enabled": _truthy(runtime.get("topup_failover_enabled")),
        "tripay_invoice_expiry_minutes": int(runtime.get("tripay_invoice_expiry_minutes") or 30),
        "digiflazz_testing_enabled": _truthy(runtime.get("digiflazz_testing_enabled")),
        "digiflazz_max_price_margin_percent": float(runtime.get("digiflazz_max_price_margin_percent") or 10),
        "digiflazz_low_balance_threshold": float(runtime.get("digiflazz_low_balance_threshold") or 50000),
        "tripay_wallet_sync_interval_seconds": int(runtime.get("tripay_wallet_sync_interval_seconds") or 120),
        "digiflazz_product_auto_sync_enabled": _truthy(runtime.get("digiflazz_product_auto_sync_enabled")),
        "digiflazz_product_sync_interval_minutes": int(runtime.get("digiflazz_product_sync_interval_minutes") or 360),
        "supported_payment_providers": ["tripay"],
        "supported_topup_providers": ["digiflazz"],
        "automations": {
            "tripay_wallet_reconcile": {
                "enabled": True,
                "interval_seconds": int(runtime.get("tripay_wallet_sync_interval_seconds") or 120),
            },
            "digiflazz_product_sync": {
                "enabled": _truthy(runtime.get("digiflazz_product_auto_sync_enabled")),
                "interval_minutes": int(runtime.get("digiflazz_product_sync_interval_minutes") or 360),
            },
        },
        "providers": {
            "tripay": {
                "name": "Tripay",
                "configured": tripay_configured,
                "base_url": runtime.get("tripay_base_url") or settings.resolved_tripay_base_url,
                "credentials": [
                    {
                        "key": "tripay_api_key",
                        "label": "API Key",
                        "configured": bool(runtime.get("tripay_api_key")),
                        "masked": mask_secret(runtime.get("tripay_api_key")),
                    },
                    {
                        "key": "tripay_private_key",
                        "label": "Private Key",
                        "configured": bool(runtime.get("tripay_private_key")),
                        "masked": mask_secret(runtime.get("tripay_private_key")),
                    },
                    {
                        "key": "tripay_merchant_code",
                        "label": "Merchant Code",
                        "configured": bool(runtime.get("tripay_merchant_code")),
                        "masked": mask_secret(runtime.get("tripay_merchant_code")),
                    },
                ],
            },
            "digiflazz": {
                "name": "Digiflazz",
                "configured": digiflazz_configured,
                "base_url": runtime.get("digiflazz_base_url") or settings.digiflazz_base_url,
                "username": runtime.get("digiflazz_username") or "",
                "credentials": [
                    {
                        "key": "digiflazz_api_key",
                        "label": "API Key",
                        "configured": bool(runtime.get("digiflazz_api_key")),
                        "masked": mask_secret(runtime.get("digiflazz_api_key")),
                    },
                    {
                        "key": "digiflazz_webhook_secret",
                        "label": "Webhook Secret",
                        "configured": bool(runtime.get("digiflazz_webhook_secret")),
                        "masked": mask_secret(runtime.get("digiflazz_webhook_secret")),
                    },
                ],
            },
        },
    }
