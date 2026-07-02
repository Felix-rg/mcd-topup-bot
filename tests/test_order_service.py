from app.services.order_service import calculate_admin_fee


def test_calculate_admin_fee_for_qris() -> None:
    assert calculate_admin_fee(10000, "qris") == 70


def test_calculate_admin_fee_for_ovo() -> None:
    assert calculate_admin_fee(10000, "ovo") == 150


def test_calculate_admin_fee_for_default_method() -> None:
    assert calculate_admin_fee(10000, "bank") == 4500
