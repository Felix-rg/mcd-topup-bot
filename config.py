"""Compatibility shim re-exporting configuration from app.config."""

from app.config import *  # noqa: F401,F403

__all__ = getattr(__import__("app.config", fromlist=["*"]), "__all__", [])
