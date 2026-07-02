"""Shim re-export for services.tripay_service to app.services.tripay_service"""

from app.services.tripay_service import *  # noqa: F401,F403

__all__ = getattr(__import__("app.services.tripay_service", fromlist=["*"]), "__all__", [])
