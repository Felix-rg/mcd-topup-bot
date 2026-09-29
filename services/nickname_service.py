"""Compatibility shim re-exporting nickname service from app.services."""

from app.services.nickname_service import *  # noqa: F401,F403

__all__ = getattr(__import__("app.services.nickname_service", fromlist=["*"]), "__all__", [])
