"""Environment-only capability gates for outbound Digiflazz operations.

The catalog and transaction paths share credentials, so configuration presence
alone must never authorize an outbound request.  These gates deliberately
read the process settings rather than provider runtime settings stored in the
database.
"""

from typing import Dict

from app.core import settings as settings_module


DIGIFLAZZ_CAPABILITY_CATALOG_PRICE_LIST = "catalog_price_list"
DIGIFLAZZ_CAPABILITY_TRANSACTION_SEND = "transaction_send"
DIGIFLAZZ_CAPABILITY_TRANSACTION_STATUS = "transaction_status"
DIGIFLAZZ_CAPABILITY_BALANCE_WATCH = "balance_watch"
DIGIFLAZZ_CAPABILITY_DEPOSIT = "deposit"


_CAPABILITY_SETTING_FIELDS = {
    DIGIFLAZZ_CAPABILITY_CATALOG_PRICE_LIST: "digiflazz_catalog_price_list_enabled",
    DIGIFLAZZ_CAPABILITY_TRANSACTION_SEND: "digiflazz_transaction_send_enabled",
    DIGIFLAZZ_CAPABILITY_TRANSACTION_STATUS: "digiflazz_transaction_status_enabled",
    DIGIFLAZZ_CAPABILITY_BALANCE_WATCH: "digiflazz_balance_watch_enabled",
    DIGIFLAZZ_CAPABILITY_DEPOSIT: "digiflazz_deposit_enabled",
}


def is_digiflazz_capability_enabled(capability: str) -> bool:
    """Return whether one named outbound operation is explicitly enabled.

    A typo is a programming/configuration error, not a permissive fallback.
    """

    try:
        setting_name = _CAPABILITY_SETTING_FIELDS[capability]
    except KeyError as exc:
        raise ValueError(f"Unknown Digiflazz capability: {capability}") from exc
    return bool(getattr(settings_module.settings, setting_name, False))


def digiflazz_capability_snapshot() -> Dict[str, bool]:
    """Safe admin-facing state; values disclose permission, never credentials."""

    return {
        capability: is_digiflazz_capability_enabled(capability)
        for capability in _CAPABILITY_SETTING_FIELDS
    }
