import logging
from typing import Any, Dict, Optional, cast

from sqlalchemy import text

from app.core.database import get_engine, init_db as init_core_db

logger = logging.getLogger(__name__)
engine = None
_initialized = False
_initialized_url: Optional[str] = None


def _current_engine():
    global engine
    engine = get_engine()
    return engine


async def _ensure_db_ready() -> None:
    global _initialized, _initialized_url
    current_engine = _current_engine()
    current_url = str(current_engine.url)

    if _initialized and _initialized_url == current_url:
        return

    try:
        await init_core_db()
        _initialized = True
        _initialized_url = current_url
    except Exception as exc:
        logger.warning("DB init warning: %s", exc)


async def db_execute(query: str, params: Optional[Dict[str, Any]] = None) -> None:
    await _ensure_db_ready()
    try:
        async with _current_engine().begin() as conn:
            await conn.execute(text(query), params or {})
    except Exception:
        logger.exception("DB Execute Error")
        raise


async def db_query(query: str, params: Optional[Dict[str, Any]] = None) -> list[tuple[Any, ...]]:
    await _ensure_db_ready()
    try:
        async with _current_engine().connect() as conn:
            result = await conn.execute(text(query), params or {})
            return [tuple(row) for row in result.fetchall()]
    except Exception:
        logger.exception("DB Query Error")
        return cast(list[tuple[Any, ...]], [])


async def init_db() -> None:
    await _ensure_db_ready()
    logger.info("Database siap digunakan")
