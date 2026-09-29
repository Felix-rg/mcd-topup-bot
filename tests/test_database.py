import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import (
    _ensure_product_columns,
    database_runtime_summary,
    get_database_url,
    initialize_database_runtime,
    log_database_runtime,
)
from app.core.settings import Settings


class DatabaseConfigTests(unittest.TestCase):
    def test_development_sqlite_must_be_explicit(self):
        configured = Settings(
            _env_file=None,
            app_env="development",
            database_url="sqlite+aiosqlite:///./explicit.db",
        )
        self.assertEqual(get_database_url(configured), "sqlite+aiosqlite:///./explicit.db")

    def test_development_postgresql_is_explicit(self):
        configured = Settings(
            _env_file=None,
            app_env="development",
            database_url="postgresql+asyncpg://user:password@db.internal/lixafa",
        )
        self.assertTrue(get_database_url(configured).startswith("postgresql+asyncpg://"))


def test_database_runtime_summary_never_contains_credentials(caplog) -> None:
    password = "do-not-log-this-password"
    configured = Settings(
        _env_file=None,
        app_env="development",
        database_url=f"postgresql+asyncpg://lixafa:{password}@database.internal/lixafa",
    )

    with caplog.at_level("INFO"):
        summary = log_database_runtime(app_settings=configured)

    assert summary == {
        "engine": "postgresql+asyncpg",
        "host": "database.***",
        "database": "lixafa",
        "environment": "development",
    }
    assert password not in caplog.text
    assert "lixafa:" not in caplog.text


def test_sqlite_runtime_summary_only_logs_filename() -> None:
    configured = Settings(
        _env_file=None,
        app_env="development",
        database_url="sqlite+aiosqlite:///C:/private/location/lixafa.db",
    )

    summary = database_runtime_summary(app_settings=configured)

    assert summary["host"] == "local"
    assert summary["database"] == "lixafa.db"


def test_legacy_product_schema_gets_category_additively(tmp_path) -> None:
    """Promotion catalog bootstrap must work with the historical products table."""

    async def scenario() -> tuple[set[str], str]:
        db_path = tmp_path / "legacy-products.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        CREATE TABLE products (
                            sku TEXT PRIMARY KEY,
                            name TEXT,
                            price NUMERIC
                        )
                        """
                    )
                )
                await connection.execute(
                    text("INSERT INTO products (sku, name, price) VALUES ('legacy-1', 'Legacy', 10000)")
                )

            await _ensure_product_columns(engine)

            async with engine.connect() as connection:
                columns = {
                    row[1]
                    for row in (await connection.execute(text("PRAGMA table_info(products)"))).fetchall()
                }
                name = (await connection.execute(text("SELECT name FROM products WHERE sku='legacy-1'"))).scalar_one()
            return columns, str(name)
        finally:
            await engine.dispose()

    columns, name = asyncio.run(scenario())
    assert "category" in columns
    assert name == "Legacy"


def test_production_runtime_checks_connection_without_running_bootstrap() -> None:
    production_settings = type(
        "ProductionSettings",
        (),
        {
            "is_production": True,
            "allows_automatic_schema_bootstrap": False,
            "app_env": "production",
            "database_url": "postgresql+asyncpg://user:password@db.internal/lixafa",
        },
    )()
    verify = AsyncMock()
    bootstrap = AsyncMock()
    with (
        patch("app.core.database.settings", production_settings),
        patch("app.core.database.log_database_runtime"),
        patch("app.core.database.verify_database_connection", new=verify),
        patch("app.core.database.init_db", new=bootstrap),
    ):
        asyncio.run(initialize_database_runtime())

    verify.assert_awaited_once()
    bootstrap.assert_not_awaited()


def test_staging_runtime_checks_connection_without_running_bootstrap() -> None:
    staging_settings = type(
        "StagingSettings",
        (),
        {
            "is_production": False,
            "allows_automatic_schema_bootstrap": False,
            "app_env": "staging",
            "database_url": "postgresql+asyncpg://user:password@db.internal/lixafa",
        },
    )()
    verify = AsyncMock()
    bootstrap = AsyncMock()
    with (
        patch("app.core.database.settings", staging_settings),
        patch("app.core.database.log_database_runtime"),
        patch("app.core.database.verify_database_connection", new=verify),
        patch("app.core.database.init_db", new=bootstrap),
    ):
        asyncio.run(initialize_database_runtime())

    verify.assert_awaited_once()
    bootstrap.assert_not_awaited()


def test_staging_request_path_checks_connection_without_running_bootstrap() -> None:
    from types import SimpleNamespace

    import app.database as database_wrapper

    staging_settings = type(
        "StagingSettings",
        (),
        {
            "allows_automatic_schema_bootstrap": False,
        },
    )()
    verify = AsyncMock()
    bootstrap = AsyncMock()
    previous_initialized = database_wrapper._initialized
    previous_initialized_url = database_wrapper._initialized_url
    previous_lock = database_wrapper._initialization_lock
    try:
        database_wrapper._initialized = False
        database_wrapper._initialized_url = None
        database_wrapper._initialization_lock = None
        with (
            patch("app.database.settings", staging_settings),
            patch("app.database._current_engine", return_value=SimpleNamespace(url="staging-db")),
            patch("app.database.verify_database_connection", new=verify),
            patch("app.database.init_core_db", new=bootstrap),
        ):
            asyncio.run(database_wrapper._ensure_db_ready())
    finally:
        database_wrapper._initialized = previous_initialized
        database_wrapper._initialized_url = previous_initialized_url
        database_wrapper._initialization_lock = previous_lock

    verify.assert_awaited_once()
    bootstrap.assert_not_awaited()


def test_production_connection_failure_is_not_hidden() -> None:
    async def fail_connection():
        raise RuntimeError("database unavailable")

    with patch("app.main.initialize_database_runtime", new=fail_connection):
        async def start_app():
            from app.main import lifespan

            async with lifespan(None):
                pass

        with pytest.raises(RuntimeError, match="database unavailable"):
            asyncio.run(start_app())


if __name__ == "__main__":
    unittest.main()
