from app.config import TRIPAY_BASE_URL
from app.services.tripay_service import build_invoice_payload


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
