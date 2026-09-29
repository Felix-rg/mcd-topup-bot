"""Compatibility shim re-exporting Tripay service from app.services."""

from app.services.tripay_service import *  # noqa: F401,F403

__all__ = getattr(__import__("app.services.tripay_service", fromlist=["*"]), "__all__", [])
