"""Promotion calculation and persistence services.

The calculation module is deliberately database-free so the admin simulator,
public quote endpoint, and checkout can share exactly the same rules.
"""

from app.promotions.engine import (
    AppliedPromotion,
    EligibilityEvaluation,
    EligibilityProfile,
    PromotionContext,
    PromotionQuote,
    canonicalize_eligibility_rules,
    eligibility_customer_ids,
    eligibility_options_metadata,
    eligibility_rules_summary,
    evaluate_eligibility_rules,
    evaluate_promotion_eligibility,
    legacy_eligibility_rules,
    quote_promotions,
    serialize_eligibility_rules,
)

__all__ = [
    "AppliedPromotion",
    "EligibilityEvaluation",
    "EligibilityProfile",
    "PromotionContext",
    "PromotionQuote",
    "canonicalize_eligibility_rules",
    "eligibility_customer_ids",
    "eligibility_options_metadata",
    "eligibility_rules_summary",
    "evaluate_eligibility_rules",
    "evaluate_promotion_eligibility",
    "legacy_eligibility_rules",
    "quote_promotions",
    "serialize_eligibility_rules",
]
