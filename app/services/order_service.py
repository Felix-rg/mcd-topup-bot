"""Business helpers for order creation and payment calculation."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import uuid4


def normalize_payment_method(method: Optional[str]) -> str:
    """Normalisasi nama metode pembayaran mengikuti aturan bisnis yang ada."""
    return (method or "").strip().upper()


def calculate_admin_fee(price: float, method: Optional[str]) -> int:
    """Hitung biaya admin sesuai aturan lama: QRIS 0.7%, OVO/DANA 1.5%, sisanya 4500."""
    normalized_method = normalize_payment_method(method)
    price_value = Decimal(str(price))

    if normalized_method == "QRIS":
        return int(price_value * Decimal("0.007"))
    if normalized_method in {"OVO", "DANA"}:
        return int(price_value * Decimal("0.015"))
    return 4500


def calculate_total_amount(price: float, method: Optional[str]) -> int:
    """Return the total amount a customer must pay for an order."""
    price_value = Decimal(str(price))
    fee_value = Decimal(str(calculate_admin_fee(price, method)))
    return int(price_value + fee_value)


def create_order_id() -> str:
    """Generate a new order identifier while keeping the API contract unchanged."""
    return str(uuid4())
