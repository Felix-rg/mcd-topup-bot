"""Shim re-export for services.digiflazz_service to app.services.digiflazz_service"""

from app.services.digiflazz_service import *  # noqa: F401,F403

__all__ = getattr(__import__("app.services.digiflazz_service", fromlist=["*"]), "__all__", [])
