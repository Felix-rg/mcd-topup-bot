import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.promotions.engine import (
    EligibilityProfile,
    PromotionContext,
    canonicalize_eligibility_rules,
    eligibility_customer_ids,
    evaluate_eligibility_rules,
    evaluate_promotion_eligibility,
    legacy_eligibility_rules,
)


class PromotionEligibilityRuleTests(unittest.TestCase):
    @staticmethod
    def _rules(*conditions, operator="all"):
        return {
            "version": 1,
            "operator": operator,
            "conditions": list(conditions),
        }

    @staticmethod
    def _condition(field, operator, value=None):
        return {"field": field, "operator": operator, "value": value}

    @staticmethod
    def _profile(**overrides):
        values = {
            "authentication_status": "guest",
            "has_customer_account": False,
            "account_status": None,
            "account_created_at": None,
            "account_age_days": None,
            "successful_order_count": 0,
            "successful_order_total": 0,
            "last_successful_order_at": None,
            "days_since_last_successful_order": None,
            "has_successful_order": False,
            "customer_id": None,
            "normalized_phone": "6281277700001",
            "target_id": "player-1",
        }
        values.update(overrides)
        return EligibilityProfile(**values)

    @classmethod
    def _member_profile(cls, **overrides):
        values = {
            "authentication_status": "member",
            "has_customer_account": True,
            "account_status": "active",
            "account_created_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
            "account_age_days": 3,
            "customer_id": 71,
            "normalized_phone": "6281277700071",
        }
        values.update(overrides)
        return cls._profile(**values)

    @staticmethod
    def _context(profile):
        return PromotionContext(
            base_price=10_000,
            sku="ff50",
            payment_method="DANA",
            promo_code="ELIGIBLE",
            eligibility_profile=profile,
            now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def _promo(*, segment="all", eligibility_rules=None, customer_targets=()):
        return {
            "id": 901,
            "code": "ELIGIBLE",
            "customer_segment": segment,
            "customer_targets": list(customer_targets),
            "eligibility_rules": eligibility_rules,
        }

    def test_legacy_all_resolves_to_empty_rules_and_remains_eligible(self):
        rules = legacy_eligibility_rules("all")
        self.assertEqual(rules, self._rules())
        evaluation = evaluate_promotion_eligibility(
            self._promo(segment="all"),
            self._context(self._profile()),
        )
        self.assertTrue(evaluation.eligible)
        self.assertEqual(evaluation.source, "legacy_adapter")

    def test_legacy_new_keeps_first_purchase_semantics_for_guest(self):
        rules = legacy_eligibility_rules("new")
        self.assertEqual(
            rules,
            self._rules(
                self._condition("successful_order_count", "equals", 0)
            ),
        )
        first = evaluate_promotion_eligibility(
            self._promo(segment="new"),
            self._context(self._profile(successful_order_count=0)),
        )
        returning = evaluate_promotion_eligibility(
            self._promo(segment="new"),
            self._context(
                self._profile(
                    successful_order_count=1,
                    successful_order_total=10_000,
                    has_successful_order=True,
                )
            ),
        )
        self.assertTrue(first.eligible)
        self.assertFalse(returning.eligible)
        self.assertEqual(returning.reason_code, "FIRST_PURCHASE_ONLY")

    def test_legacy_new_keeps_first_purchase_semantics_for_member(self):
        first = evaluate_promotion_eligibility(
            self._promo(segment="new"),
            self._context(self._member_profile(successful_order_count=0)),
        )
        returning = evaluate_promotion_eligibility(
            self._promo(segment="new"),
            self._context(
                self._member_profile(
                    successful_order_count=2,
                    successful_order_total=20_000,
                    has_successful_order=True,
                )
            ),
        )
        self.assertTrue(first.eligible)
        self.assertFalse(returning.eligible)
        self.assertEqual(returning.reason_code, "FIRST_PURCHASE_ONLY")

    def test_member_new_accepts_active_member_with_three_day_old_account(self):
        rules = self._rules(
            self._condition("authentication_status", "equals", "member"),
            self._condition("account_status", "equals", "active"),
            self._condition("account_age_days", "less_than_or_equal", 7),
        )
        evaluation = evaluate_eligibility_rules(
            rules,
            self._member_profile(account_age_days=3),
        )
        self.assertTrue(evaluation.eligible)
        self.assertEqual(len(evaluation.conditions), 3)

    def test_member_new_rejects_ten_day_old_account_at_seven_day_limit(self):
        evaluation = evaluate_eligibility_rules(
            self._rules(
                self._condition("authentication_status", "equals", "member"),
                self._condition("account_status", "equals", "active"),
                self._condition("account_age_days", "less_than_or_equal", 7),
            ),
            self._member_profile(account_age_days=10),
        )
        self.assertFalse(evaluation.eligible)
        self.assertEqual(evaluation.reason_code, "ACCOUNT_TOO_OLD")

    def test_member_new_rejects_guest_and_inactive_member(self):
        rules = self._rules(
            self._condition("authentication_status", "equals", "member"),
            self._condition("account_status", "equals", "active"),
            self._condition("account_age_days", "less_than_or_equal", 7),
        )
        guest = evaluate_eligibility_rules(rules, self._profile(account_age_days=1))
        inactive = evaluate_eligibility_rules(
            rules,
            self._member_profile(account_status="inactive"),
        )
        self.assertFalse(guest.eligible)
        self.assertEqual(guest.reason_code, "MEMBER_LOGIN_REQUIRED")
        self.assertFalse(inactive.eligible)
        self.assertEqual(inactive.reason_code, "ACCOUNT_INACTIVE")

    def test_member_new_optional_zero_success_condition_is_enforced(self):
        rules = self._rules(
            self._condition("authentication_status", "equals", "member"),
            self._condition("account_status", "equals", "active"),
            self._condition("account_age_days", "less_than_or_equal", 7),
            self._condition("successful_order_count", "equals", 0),
        )
        first = evaluate_eligibility_rules(
            rules,
            self._member_profile(successful_order_count=0),
        )
        returning = evaluate_eligibility_rules(
            rules,
            self._member_profile(
                successful_order_count=1,
                successful_order_total=50_000,
                has_successful_order=True,
            ),
        )
        self.assertTrue(first.eligible)
        self.assertFalse(returning.eligible)
        self.assertEqual(returning.reason_code, "FIRST_PURCHASE_ONLY")

    def test_existing_and_minimum_successful_order_count_are_enforced(self):
        existing = self._rules(
            self._condition(
                "successful_order_count",
                "greater_than_or_equal",
                1,
            )
        )
        loyal = self._rules(
            self._condition(
                "successful_order_count",
                "greater_than_or_equal",
                10,
            )
        )
        self.assertTrue(
            evaluate_eligibility_rules(
                existing,
                self._profile(successful_order_count=1, has_successful_order=True),
            ).eligible
        )
        below = evaluate_eligibility_rules(
            loyal,
            self._profile(successful_order_count=9, has_successful_order=True),
        )
        self.assertFalse(below.eligible)
        self.assertEqual(below.reason_code, "MIN_SUCCESSFUL_ORDERS_NOT_MET")

    def test_minimum_successful_spend_is_enforced(self):
        rules = self._rules(
            self._condition(
                "successful_order_total",
                "greater_than_or_equal",
                500_000,
            )
        )
        self.assertTrue(
            evaluate_eligibility_rules(
                rules,
                self._profile(successful_order_total=500_000),
            ).eligible
        )
        below = evaluate_eligibility_rules(
            rules,
            self._profile(successful_order_total=499_999),
        )
        self.assertFalse(below.eligible)
        self.assertEqual(below.reason_code, "MIN_SUCCESSFUL_SPEND_NOT_MET")

    def test_days_since_last_successful_order_supports_winback(self):
        rules = self._rules(
            self._condition("has_successful_order", "is_true"),
            self._condition(
                "days_since_last_successful_order",
                "greater_than",
                30,
            ),
        )
        winback = self._profile(
            successful_order_count=2,
            successful_order_total=100_000,
            has_successful_order=True,
            last_successful_order_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            days_since_last_successful_order=49,
        )
        recent = self._profile(
            successful_order_count=2,
            successful_order_total=100_000,
            has_successful_order=True,
            last_successful_order_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            days_since_last_successful_order=10,
        )
        self.assertTrue(evaluate_eligibility_rules(rules, winback).eligible)
        self.assertFalse(evaluate_eligibility_rules(rules, recent).eligible)

    def test_all_operator_requires_every_condition(self):
        rules = self._rules(
            self._condition("authentication_status", "equals", "member"),
            self._condition(
                "successful_order_count",
                "greater_than_or_equal",
                10,
            ),
            operator="all",
        )
        self.assertTrue(
            evaluate_eligibility_rules(
                rules,
                self._member_profile(successful_order_count=10),
            ).eligible
        )
        self.assertFalse(
            evaluate_eligibility_rules(
                rules,
                self._member_profile(successful_order_count=9),
            ).eligible
        )

    def test_any_operator_accepts_one_matching_condition(self):
        rules = self._rules(
            self._condition(
                "successful_order_count",
                "greater_than_or_equal",
                20,
            ),
            self._condition(
                "successful_order_total",
                "greater_than_or_equal",
                1_000_000,
            ),
            operator="any",
        )
        large_buyer = self._profile(
            successful_order_count=3,
            successful_order_total=1_000_000,
            has_successful_order=True,
        )
        small_buyer = self._profile(
            successful_order_count=3,
            successful_order_total=50_000,
            has_successful_order=True,
        )
        self.assertTrue(evaluate_eligibility_rules(rules, large_buyer).eligible)
        self.assertFalse(evaluate_eligibility_rules(rules, small_buyer).eligible)

    def test_supported_boolean_membership_between_and_datetime_operators(self):
        rules = self._rules(
            self._condition("authentication_status", "in", ["guest", "member"]),
            self._condition("has_customer_account", "is_true"),
            self._condition("customer_id", "not_in", [10, 11]),
            self._condition("successful_order_count", "between", [5, 10]),
            self._condition(
                "account_created_at",
                "between",
                ["2026-07-01T00:00:00+00:00", "2026-07-20T00:00:00+00:00"],
            ),
        )
        evaluation = evaluate_eligibility_rules(
            canonicalize_eligibility_rules(json.dumps(rules)),
            self._member_profile(
                customer_id=71,
                successful_order_count=7,
                account_created_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            ),
        )
        self.assertTrue(evaluation.eligible)

    def test_invalid_operator_is_rejected(self):
        with self.assertRaises(ValueError):
            canonicalize_eligibility_rules(
                self._rules(
                    self._condition(
                        "successful_order_count",
                        "contains",
                        1,
                    )
                )
            )

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            canonicalize_eligibility_rules(
                self._rules(
                    self._condition("topup.password", "equals", "anything")
                )
            )

    def test_wrong_value_types_and_incompatible_field_operator_are_rejected(self):
        invalid_conditions = (
            self._condition("account_age_days", "less_than_or_equal", "seven"),
            self._condition("authentication_status", "greater_than", "member"),
            self._condition("successful_order_total", "between", [1]),
            self._condition("has_successful_order", "is_true", True),
            self._condition("customer_id", "in", [1, "2"]),
        )
        for condition in invalid_conditions:
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                canonicalize_eligibility_rules(self._rules(condition))

    def test_sql_python_and_javascript_strings_are_only_literal_data(self):
        malicious = "__import__('os').system('touch-owned'); DROP TABLE promos; alert(1)"
        rules = self._rules(
            self._condition("target_id", "equals", malicious)
        )
        with (
            patch("builtins.eval") as eval_mock,
            patch("builtins.exec") as exec_mock,
            patch.object(os, "system") as system_mock,
        ):
            canonical = canonicalize_eligibility_rules(rules)
            evaluation = evaluate_eligibility_rules(
                canonical,
                self._profile(target_id="safe-target"),
            )
        self.assertEqual(canonical["conditions"][0]["value"], malicious.casefold())
        self.assertFalse(evaluation.eligible)
        eval_mock.assert_not_called()
        exec_mock.assert_not_called()
        system_mock.assert_not_called()

    def test_nested_too_many_and_oversized_rules_are_rejected(self):
        nested = {
            "version": 1,
            "operator": "all",
            "conditions": [
                {
                    "operator": "any",
                    "conditions": [
                        self._condition("successful_order_count", "equals", 0)
                    ],
                }
            ],
        }
        too_many = self._rules(
            *[
                self._condition("successful_order_count", "equals", index)
                for index in range(21)
            ]
        )
        oversized = self._rules(
            self._condition("target_id", "equals", "x" * 70_000)
        )
        for label, value in (
            ("nested", nested),
            ("too_many", too_many),
            ("oversized", oversized),
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                canonicalize_eligibility_rules(value)

    def test_invalid_explicit_rule_in_storage_fails_closed(self):
        evaluation = evaluate_promotion_eligibility(
            self._promo(
                segment="all",
                eligibility_rules='{"version":1,"operator":"all","conditions":[{"field":"unknown","operator":"equals","value":1}]}',
            ),
            self._context(self._profile()),
        )
        self.assertFalse(evaluation.eligible)
        self.assertEqual(evaluation.reason_code, "INVALID_ELIGIBILITY_RULE")
        self.assertEqual(evaluation.source, "explicit")

    def test_legacy_specific_uses_authoritative_customer_targets(self):
        rules = legacy_eligibility_rules("specific", customer_targets=[7, 3, 7])
        self.assertEqual(eligibility_customer_ids(rules), [3, 7])
        selected = evaluate_promotion_eligibility(
            self._promo(segment="specific", customer_targets=[3, 7]),
            self._context(self._member_profile(customer_id=7)),
        )
        other = evaluate_promotion_eligibility(
            self._promo(segment="specific", customer_targets=[3, 7]),
            self._context(self._member_profile(customer_id=8)),
        )
        self.assertTrue(selected.eligible)
        self.assertFalse(other.eligible)
        self.assertEqual(other.reason_code, "CUSTOMER_NOT_TARGETED")

    def test_missing_authoritative_fact_fails_closed_instead_of_becoming_zero(self):
        rules = self._rules(
            self._condition("successful_order_count", "equals", 0)
        )
        evaluation = evaluate_eligibility_rules(
            rules,
            self._profile(
                normalized_phone="",
                successful_order_count=None,
                successful_order_total=None,
                has_successful_order=None,
            ),
        )
        self.assertFalse(evaluation.eligible)
        self.assertEqual(evaluation.reason_code, "CUSTOMER_IDENTITY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
