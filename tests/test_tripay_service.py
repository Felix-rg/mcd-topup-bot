import asyncio
import hashlib
import hmac

from app.config import TRIPAY_BASE_URL
from app.services.tripay_service import (
    TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE,
    TRIPAY_INVOICE_OUTCOME_NOT_SENT,
    TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN,
    TRIPAY_INVOICE_OUTCOME_SUCCESS,
    build_invoice_payload,
    classify_tripay_invoice_response,
    create_invoice,
    create_ewallet_signature,
    create_open_payment_signature,
    parse_tripay_fee,
)


def test_invoice_outcome_classifier_fails_closed_for_ambiguous_responses() -> None:
    assert classify_tripay_invoice_response(None) == TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN
    assert classify_tripay_invoice_response({"error": "unclassified failure"}) == TRIPAY_INVOICE_OUTCOME_SENT_UNKNOWN
    assert classify_tripay_invoice_response({"_invoice_outcome": "DEFINITIVE_FAILURE"}) == TRIPAY_INVOICE_OUTCOME_DEFINITIVE_FAILURE
    assert classify_tripay_invoice_response({"_invoice_outcome": "NOT_SENT"}) == TRIPAY_INVOICE_OUTCOME_NOT_SENT
    assert classify_tripay_invoice_response({"checkout_url": "https://payment.invalid/checkout"}) == TRIPAY_INVOICE_OUTCOME_SUCCESS


def test_build_invoice_payload_normalizes_method_and_urls() -> None:
    payload = build_invoice_payload(
        order_id="order-1",
        amount=15000,
        method="qris",
        customer_name="Customer",
        customer_email="customer@example.com",
        customer_phone="08123456789",
        callback_url="https://example.ngrok-free.app/callback",
        return_url="https://example.ngrok-free.app/",
    )

    assert payload["method"] == "QRIS"
    assert payload["callback_url"] == "https://example.ngrok-free.app/callback"
    assert payload["return_url"] == "https://example.ngrok-free.app/"
    assert payload["merchant_ref"] == "order-1"
    assert payload["amount"] == 15000


def test_default_tripay_base_url_uses_sandbox_in_development() -> None:
    assert TRIPAY_BASE_URL == "https://tripay.co.id/api-sandbox"


def test_build_invoice_payload_supports_expiry_and_custom_items() -> None:
    payload = build_invoice_payload(
        order_id="order-2",
        amount=22000,
        method="BRIVA",
        customer_name="Customer",
        customer_email="customer@example.com",
        customer_phone="08123456789",
        expired_time=1893456000,
        order_items=[{"name": "Tagihan PLN", "price": 22000, "quantity": 1}],
    )

    assert payload["expired_time"] == 1893456000
    assert payload["order_items"][0]["name"] == "Tagihan PLN"


def test_open_payment_and_ewallet_signatures_follow_tripay_contract() -> None:
    private_key = "private-test"
    merchant_code = "T1234"

    open_expected = hmac.new(
        private_key.encode(),
        f"{merchant_code}QRISWALLET-1".encode(),
        hashlib.sha256,
    ).hexdigest()
    ewallet_expected = hmac.new(
        private_key.encode(),
        f"{merchant_code}DANA08123456789".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert create_open_payment_signature("QRIS", "WALLET-1", private_key=private_key, merchant_code=merchant_code) == open_expected
    assert create_ewallet_signature("DANA", "08123456789", private_key=private_key, merchant_code=merchant_code) == ewallet_expected


def test_nested_total_fee_uses_customer_fee() -> None:
    fee = parse_tripay_fee(
        {
            "success": True,
            "data": [{"total_fee": {"customer": 1000, "merchant": 0}}],
        },
        amount=7416,
    )

    assert fee.valid is True
    assert fee.customer_fee == 1000
    assert fee.merchant_fee == 0
    assert fee.total_fee == 1000


def test_customer_and_merchant_fee_remain_distinct() -> None:
    fee = parse_tripay_fee(
        {
            "fee_customer": 1200,
            "fee_merchant": 350,
            "total_fee": 1550,
        },
        amount=20000,
    )

    assert fee.customer_fee == 1200
    assert fee.merchant_fee == 350
    assert fee.total_fee == 1550


def test_flat_percent_and_dana_minimum_fee_use_integer_rupiah() -> None:
    fee = parse_tripay_fee(
        {
            "fee_customer": {"flat": 0, "percent": "3.00"},
            "minimum_fee": 1000,
        },
        amount=7416,
    )

    assert fee.valid is True
    assert fee.flat_fee == 0
    assert fee.percent_fee == 223
    assert fee.customer_fee == 1000
    assert isinstance(fee.customer_fee, int)


def test_flat_and_percent_fee_round_up_without_float() -> None:
    fee = parse_tripay_fee(
        {"fee": {"flat": 500, "percent": "0.7"}},
        amount=10001,
    )

    assert fee.flat_fee == 500
    assert fee.percent_fee == 71
    assert fee.customer_fee == 571


def test_legacy_scalar_total_fee_is_supported() -> None:
    fee = parse_tripay_fee({"total_fee": 785}, amount=10000)

    assert fee.valid is True
    assert fee.customer_fee == 785
    assert fee.total_fee == 785


def test_legacy_total_fee_with_merchant_fee_derives_customer_component() -> None:
    fee = parse_tripay_fee(
        {"total_fee": 1350, "fee_merchant": 350},
        amount=10000,
    )

    assert fee.valid is True
    assert fee.customer_fee == 1000
    assert fee.merchant_fee == 350
    assert fee.total_fee == 1350


def test_malformed_and_missing_fee_fail_closed() -> None:
    malformed = parse_tripay_fee({"total_fee": {"customer": "invalid"}}, amount=10000)
    missing = parse_tripay_fee({"success": True, "data": [{}]}, amount=10000)

    assert malformed.valid is False
    assert missing.valid is False
    assert malformed.total_fee == 0
    assert missing.total_fee == 0


def test_transaction_create_normalizes_nested_fee_without_live_request(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "success": True,
                "data": {
                    "reference": "TRIPAY-TEST-1",
                    "merchant_ref": "ORDER-1",
                    "checkout_url": "https://tripay.test/checkout",
                    "amount": 7416,
                    "total_fee": {"customer": 1000, "merchant": 250},
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    async def fake_runtime_config():
        return {
            "api_key": "api-test",
            "private_key": "private-test",
            "merchant_code": "T1234",
            "base_url": "https://tripay.test/api-sandbox",
        }

    monkeypatch.setattr("app.services.tripay_service._runtime_config", fake_runtime_config)
    monkeypatch.setattr("app.services.tripay_service.httpx.AsyncClient", lambda *args, **kwargs: FakeClient())

    result = asyncio.run(
        create_invoice(
            order_id="ORDER-1",
            amount=7416,
            method="DANA",
            customer_name="Customer",
            customer_email="customer@example.com",
            customer_phone="08123456789",
        )
    )

    assert result["fee_customer"] == 1000
    assert result["fee_merchant"] == 250
    assert result["total_fee"] == 1250
    assert result["checkout_url"] == "https://tripay.test/checkout"
