"""Compatibility alias for legacy imports that use core.database."""

import sys

from app.core import database as _database

sys.modules[__name__] = _database
