"""Compatibility shim re-exporting admin routes from app.routes.admin_routes."""

from app.routes.admin_routes import *  # noqa: F401,F403

__all__ = getattr(__import__("app.routes.admin_routes", fromlist=["*"]), "__all__", [])
