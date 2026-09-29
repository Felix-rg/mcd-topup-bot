"""Compatibility shim re-exporting topup routes from app.routes.topup_routes."""

from app.routes.topup_routes import *  # noqa: F401,F403

__all__ = getattr(__import__("app.routes.topup_routes", fromlist=["*"]), "__all__", [])
