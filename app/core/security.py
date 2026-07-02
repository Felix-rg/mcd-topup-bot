"""Shared security helpers used across the application."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.settings import settings

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def hash_password(password: str) -> str:
    """Hash a plaintext password using the configured password hashing scheme."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token with an expiration timestamp."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token, returning the payload or None."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None


def check_rate_limit(identifier: str, limit: int = 5, window: int = 60) -> bool:
    """Enforce a simple in-memory rate limit for a single worker process."""
    now = time.time()
    _rate_limit_store[identifier] = [
        timestamp for timestamp in _rate_limit_store[identifier] if now - timestamp < window
    ]

    if len(_rate_limit_store[identifier]) >= limit:
        return False

    _rate_limit_store[identifier].append(now)
    return True
