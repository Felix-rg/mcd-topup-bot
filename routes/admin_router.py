"""Compatibility shim re-exporting admin health router from app.routes.admin_router."""

from app.routes.admin_router import *  # noqa: F401,F403

__all__ = getattr(__import__("app.routes.admin_router", fromlist=["*"]), "__all__", [])
