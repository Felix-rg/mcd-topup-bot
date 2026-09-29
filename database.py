"""Compatibility alias for legacy imports that use database."""

import sys

from app import database as _database

sys.modules[__name__] = _database
