"""Compatibility module re-exporting the shared security helpers."""

from app.core.security import (  # noqa: F401
    check_rate_limit,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
