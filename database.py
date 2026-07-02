"""Shim module re-exporting from app.database for backward compatibility."""

from __future__ import annotations

from typing import Any

from app import database as _database


async def db_execute(query: str, params: dict[str, Any] | None = None) -> None:
    result = await _database.db_execute(query, params)
    globals()["engine"] = _database.engine
    return result


async def db_query(query: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    result = await _database.db_query(query, params)
    globals()["engine"] = _database.engine
    return result


async def init_db() -> None:
    await _database.init_db()
    globals()["engine"] = _database.engine


engine = _database.engine


def __getattr__(name: str) -> Any:
    return getattr(_database, name)


__all__ = ["db_execute", "db_query", "init_db", "engine"]