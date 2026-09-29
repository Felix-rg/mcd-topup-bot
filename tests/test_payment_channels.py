import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.routes import topup_routes


def _provider_response(*channels):
    return {"success": True, "data": list(channels)}


def _channel(
    code="DANA",
    *,
    active=True,
    maintenance=False,
    minimum_amount=1000,
    maximum_amount=10_000_000,
    fee_customer=None,
    minimum_fee=0,
):
    return {
        "code": code,
        "name": code,
        "group": "E-Wallet",
        "active": active,
        "maintenance": maintenance,
        "minimum_amount": minimum_amount,
        "maximum_amount": maximum_amount,
        "fee_customer": fee_customer or {"flat": 0, "percent": 3},
        "minimum_fee": minimum_fee,
    }


def test_active_inactive_and_maintenance_channels_are_explicit() -> None:
    response = _provider_response(
        _channel("DANA", active=True),
        _channel("OVO", active=False),
        _channel("QRIS", active=True, maintenance=True),
    )
    with patch.object(topup_routes, "get_payment_channels", new=AsyncMock(return_value=response)):
        result = asyncio.run(topup_routes.payment_channels())

    assert result["success"] is True
    by_code = {channel["code"]: channel for channel in result["channels"]}
    assert by_code["DANA"]["active"] is True
    assert by_code["OVO"]["active"] is False
    assert by_code["QRIS"]["maintenance"] is True
    assert by_code["QRIS"]["active"] is False
    assert by_code["WALLET"]["active"] is True
    assert result["source"] == "tripay"


def test_provider_failure_returns_error_without_static_channels() -> None:
    response = {"success": False, "message": "provider unavailable", "data": []}
    with patch.object(topup_routes, "get_payment_channels", new=AsyncMock(return_value=response)):
        result = asyncio.run(topup_routes.payment_channels())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    payload = json.loads(result.body)
    assert payload["success"] is False
    assert payload["channels"] == []
    assert payload["reason_code"] == "PAYMENT_CHANNELS_UNAVAILABLE"
    assert "provider unavailable" not in str(payload)


@pytest.mark.parametrize(
    ("channel", "reason_code"),
    [
        (_channel(active=False), "PAYMENT_CHANNEL_INACTIVE"),
        (_channel(active=True, maintenance=True), "PAYMENT_CHANNEL_MAINTENANCE"),
    ],
)
def test_inactive_or_maintenance_channel_cannot_be_quoted(channel, reason_code) -> None:
    with patch.object(
        topup_routes,
        "get_payment_channels",
        new=AsyncMock(return_value=_provider_response(channel)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(topup_routes._quote_payment_fee(7416, "DANA"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["reason_code"] == reason_code


@pytest.mark.parametrize(
    ("amount", "reason_code"),
    [
        (999, "PAYMENT_AMOUNT_BELOW_MINIMUM"),
        (5001, "PAYMENT_AMOUNT_ABOVE_MAXIMUM"),
    ],
)
def test_minimum_and_maximum_amount_are_validated_by_backend(amount, reason_code) -> None:
    channel = _channel(minimum_amount=1000, maximum_amount=5000)
    calculate = AsyncMock()
    with (
        patch.object(
            topup_routes,
            "get_payment_channels",
            new=AsyncMock(return_value=_provider_response(channel)),
        ),
        patch.object(topup_routes, "calculate_fee", new=calculate),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(topup_routes._quote_payment_fee(amount, "DANA"))

    assert exc_info.value.detail["reason_code"] == reason_code
    calculate.assert_not_awaited()


def test_non_promo_quote_uses_nested_backend_customer_fee() -> None:
    channel = _channel(minimum_fee=1000)
    fee_response = {
        "success": True,
        "data": [
            {
                "code": "DANA",
                "fee": {"flat": 0, "percent": "3.00"},
                "total_fee": {"merchant": 0, "customer": 1000},
            }
        ],
    }
    with (
        patch.object(
            topup_routes,
            "get_payment_channels",
            new=AsyncMock(return_value=_provider_response(channel)),
        ),
        patch.object(topup_routes, "calculate_fee", new=AsyncMock(return_value=fee_response)),
    ):
        fee, source = asyncio.run(topup_routes._quote_payment_fee(7416, "DANA"))

    assert fee == 1000
    assert source == "tripay"


def test_channel_fee_rule_is_backend_fallback_when_calculator_is_missing() -> None:
    channel = _channel(minimum_fee=1000)
    with (
        patch.object(
            topup_routes,
            "get_payment_channels",
            new=AsyncMock(return_value=_provider_response(channel)),
        ),
        patch.object(
            topup_routes,
            "calculate_fee",
            new=AsyncMock(return_value={"success": True, "data": [{}]}),
        ),
    ):
        fee, source = asyncio.run(topup_routes._quote_payment_fee(7416, "DANA"))

    assert fee == 1000
    assert source == "tripay_channel"


def test_free_promo_fee_is_zero_without_tripay_calls() -> None:
    get_channels = AsyncMock()
    calculate = AsyncMock()
    with (
        patch.object(topup_routes, "get_payment_channels", new=get_channels),
        patch.object(topup_routes, "calculate_fee", new=calculate),
    ):
        fee, source = asyncio.run(topup_routes._quote_payment_fee(0, "DANA"))

    assert fee == 0
    assert source == "free_checkout"
    get_channels.assert_not_awaited()
    calculate.assert_not_awaited()
