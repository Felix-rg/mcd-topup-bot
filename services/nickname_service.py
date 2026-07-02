"""Shim re-export for services.nickname_service to app.services.nickname_service"""

from app.services.nickname_service import *  # noqa: F401,F403

__all__ = getattr(__import__("app.services.nickname_service", fromlist=["*"]), "__all__", [])
