from datetime import datetime, timezone
import unittest

from app.promotions.engine import PromotionContext, quote_promotions
from app.promotions.service import code_failure_payload
from app.routes.topup_routes import _apply_best_price_promo, _promo_code_was_applied, _promo_usage_available


def test_auto_price_promo_stacks_with_code_promo() -> None:
    product = {
        "sku": "mlweek",
        "provider": "Mobile Legends",
        "name": "ML Weekly Pass",
        "price": 32742,
        "category": "Games",
    }
    promos = [
        {
            "id": 1,
            "title": "Promo Mingguan",
            "code": "",
            "badge": "Mingguan",
            "rule_type": "price",
            "target_scope": "provider",
            "target_value": "Mobile Legends",
            "discount_type": "fixed",
            "discount_value": 2000,
            "max_discount": 0,
            "active": True,
        },
        {
            "id": 2,
            "title": "Kode Tambahan",
            "code": "MLHEMAT",
            "badge": "Kode",
            "rule_type": "price",
            "target_scope": "sku",
            "target_value": "mlweek",
            "discount_type": "fixed",
            "discount_value": 2000,
            "max_discount": 0,
            "active": True,
        },
    ]

    resolved = _apply_best_price_promo(product, promos, datetime.now(timezone.utc), promo_code="MLHEMAT")

    assert resolved["original_price"] == 32742
    assert resolved["price"] == 28742
    assert _promo_code_was_applied(resolved, "MLHEMAT")
    assert [promo["price_after"] for promo in resolved["promos_applied"]] == [30742, 28742]


def test_auto_price_promo_still_applies_without_code() -> None:
    product = {
        "sku": "mlweek",
        "provider": "Mobile Legends",
        "name": "ML Weekly Pass",
        "price": 32742,
        "category": "Games",
    }
    promos = [
        {
            "id": 1,
            "title": "Promo Mingguan",
            "code": "",
            "badge": "Mingguan",
            "rule_type": "price",
            "target_scope": "provider",
            "target_value": "Mobile Legends",
            "discount_type": "fixed",
            "discount_value": 2000,
            "max_discount": 0,
            "active": True,
        }
    ]

    resolved = _apply_best_price_promo(product, promos, datetime.now(timezone.utc))

    assert resolved["original_price"] == 32742
    assert resolved["price"] == 30742


def test_promo_datetime_without_timezone_is_interpreted_as_wib() -> None:
    product = {
        "sku": "mlweek",
        "provider": "Mobile Legends",
        "name": "ML Weekly Pass",
        "price": 32742,
        "category": "Games",
    }
    promos = [
        {
            "id": 1,
            "title": "Promo Pagi WIB",
            "code": "",
            "badge": "WIB",
            "rule_type": "price",
            "target_scope": "provider",
            "target_value": "Mobile Legends",
            "discount_type": "fixed",
            "discount_value": 2000,
            "max_discount": 0,
            "starts_at": "2026-07-06T09:00:00",
            "ends_at": "2026-07-06T23:59:00",
            "active": True,
        }
    ]

    # 03:00 UTC is 10:00 WIB. If the naive starts_at were treated as UTC,
    # this promo would incorrectly remain inactive until 16:00 WIB.
    resolved = _apply_best_price_promo(product, promos, datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc))

    assert resolved["price"] == 30742


def test_payment_method_specific_promo_only_applies_to_allowed_method() -> None:
    product = {
        "sku": "ff-100",
        "provider": "Free Fire",
        "name": "Free Fire 100 Diamonds",
        "price": 10000,
        "category": "Games",
    }
    promos = [
        {
            "id": 1,
            "title": "DANA Free Fire",
            "code": "DANAFF",
            "badge": "DANA",
            "rule_type": "price",
            "target_scope": "provider",
            "target_value": "Free Fire",
            "discount_type": "percent",
            "discount_value": 50,
            "max_discount": 0,
            "payment_methods": ["DANA"],
            "active": True,
        }
    ]

    with_dana = _apply_best_price_promo(
        dict(product),
        promos,
        datetime.now(timezone.utc),
        promo_code="DANAFF",
        payment_method="DANA",
    )
    with_qris = _apply_best_price_promo(
        dict(product),
        promos,
        datetime.now(timezone.utc),
        promo_code="DANAFF",
        payment_method="QRIS",
    )

    assert with_dana["price"] == 5000
    assert _promo_code_was_applied(with_dana, "DANAFF")
    assert with_qris["price"] == 10000
    assert not _promo_code_was_applied(with_qris, "DANAFF")


def test_usage_limited_promo_stops_after_quota_is_reached() -> None:
    product = {
        "sku": "ml-100",
        "provider": "Mobile Legends",
        "name": "ML 100 Diamonds",
        "price": 10000,
        "category": "Games",
    }
    promos = [
        {
            "id": 1,
            "title": "Kuota Habis",
            "code": "HABIS",
            "badge": "Limit",
            "rule_type": "price",
            "target_scope": "sku",
            "target_value": "ml-100",
            "discount_type": "fixed",
            "discount_value": 5000,
            "usage_limit": 10,
            "usage_count": 10,
            "active": True,
        }
    ]

    resolved = _apply_best_price_promo(
        product,
        promos,
        datetime.now(timezone.utc),
        promo_code="HABIS",
        payment_method="QRIS",
    )

    assert resolved["price"] == 10000
    assert not _promo_code_was_applied(resolved, "HABIS")


class PromoAdvancedRulesTests(unittest.TestCase):
    def test_exclusive_automatic_promo_reports_requested_voucher_conflict(self) -> None:
        quote = quote_promotions(
            [
                {
                    "id": 1,
                    "title": "Automatic Exclusive",
                    "code": "",
                    "promo_type": "automatic",
                    "target_scope": "all",
                    "discount_type": "fixed",
                    "discount_value": 5000,
                    "priority": 100,
                    "exclusive": True,
                    "active": True,
                },
                {
                    "id": 2,
                    "title": "Requested Voucher",
                    "code": "SAVE",
                    "promo_type": "voucher",
                    "target_scope": "all",
                    "discount_type": "fixed",
                    "discount_value": 1000,
                    "priority": 1,
                    "stackable": True,
                    "active": True,
                },
            ],
            PromotionContext(
                base_price=10000,
                sku="ff50",
                payment_method="DANA",
                promo_code="SAVE",
            ),
        )

        self.assertFalse(quote.code_valid)
        self.assertEqual([item.id for item in quote.applied], [1])
        self.assertIn(
            {"promo_id": 0, "code": "SAVE", "reason": "promo_conflict"},
            quote.rejections,
        )
        self.assertEqual(
            code_failure_payload(quote),
            {
                "valid": False,
                "reason_code": "PROMO_CONFLICT",
                "message": "Promo tidak dapat digabungkan dengan promo lain",
            },
        )

    def test_non_stackable_code_promo_skips_automatic_discount(self) -> None:
        product = {
            "sku": "ff-50",
            "provider": "Free Fire",
            "name": "Free Fire 50 Diamonds",
            "price": 10000,
            "category": "Games",
        }
        promos = [
            {
                "id": 1,
                "title": "Auto FF",
                "code": "",
                "rule_type": "price",
                "target_scope": "provider",
                "target_value": "Free Fire",
                "discount_type": "fixed",
                "discount_value": 1000,
                "active": True,
                "stackable": True,
            },
            {
                "id": 2,
                "title": "Voucher Solo",
                "code": "SOLO",
                "rule_type": "price",
                "target_scope": "sku",
                "target_value": "ff-50",
                "discount_type": "fixed",
                "discount_value": 4000,
                "active": True,
                "stackable": False,
            },
        ]

        resolved = _apply_best_price_promo(
            product,
            promos,
            datetime.now(timezone.utc),
            promo_code="SOLO",
            payment_method="QRIS",
        )

        self.assertEqual(resolved["price"], 6000)
        self.assertEqual(len(resolved["promos_applied"]), 1)
        self.assertTrue(_promo_code_was_applied(resolved, "SOLO"))

    def test_budget_and_context_limits_block_promo(self) -> None:
        self.assertFalse(
            _promo_usage_available(
                {
                    "usage_limit": 0,
                    "budget_limit": 10000,
                    "discount_spent": 10000,
                }
            )
        )
        self.assertFalse(
            _promo_usage_available(
                {
                    "max_per_phone": 1,
                    "phone_usage_count": 1,
                    "budget_limit": 0,
                }
            )
        )
