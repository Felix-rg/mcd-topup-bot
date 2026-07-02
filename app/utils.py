"""Compatibility module re-exporting shared application helpers."""

from app.security import check_rate_limit, hash_password, verify_password

__all__ = ["hash_password", "verify_password", "check_rate_limit"]
