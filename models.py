"""Compatibility shim re-exporting Pydantic models from app.models."""

from app.models import *  # noqa: F401,F403

__all__ = getattr(__import__("app.models", fromlist=["*"]), "__all__", [])
