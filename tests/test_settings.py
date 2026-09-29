import pytest
from pydantic import ValidationError

from app.core.settings import Settings


SQLITE_URL = "sqlite+aiosqlite:///./lixafa-test.db"
POSTGRES_URL = "postgresql+asyncpg://lixafa:password@db.internal/lixafa"


def test_production_requires_real_security_values() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
            default_admin_password="",
            database_url=POSTGRES_URL,
        )


def test_production_rejects_wildcard_allowed_origins() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="prod-secret-key",
            jwt_secret_key="prod-jwt-secret",
            allowed_origins="*",
            database_url=POSTGRES_URL,
        )


def test_staging_requires_real_security_values() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="staging",
            secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
            allowed_origins="https://staging.lixafa.example",
            database_url=POSTGRES_URL,
        )


def test_staging_rejects_wildcard_allowed_origins() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="staging",
            secret_key="staging-secret-key",
            jwt_secret_key="staging-jwt-secret",
            allowed_origins="*",
            database_url=POSTGRES_URL,
        )


def test_production_accepts_explicit_allowed_origins() -> None:
    settings = Settings(
        app_env="production",
        secret_key="prod-secret-key",
        jwt_secret_key="prod-jwt-secret",
        allowed_origins="https://lixafa.example, https://admin.lixafa.example",
        database_url=POSTGRES_URL,
    )

    assert settings.parsed_allowed_origins == ["https://lixafa.example", "https://admin.lixafa.example"]


def test_development_accepts_explicit_sqlite() -> None:
    settings = Settings(_env_file=None, app_env="development", database_url=SQLITE_URL)

    assert settings.database_url == SQLITE_URL


def test_development_accepts_explicit_postgresql() -> None:
    settings = Settings(_env_file=None, app_env="development", database_url=POSTGRES_URL)

    assert settings.database_url == POSTGRES_URL


@pytest.mark.parametrize(
    ("app_env", "expected"),
    [
        ("development", True),
        ("test", True),
        ("staging", False),
        ("production", False),
    ],
)
def test_automatic_schema_bootstrap_is_limited_to_disposable_environments(
    app_env: str,
    expected: bool,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env=app_env,
        database_url=POSTGRES_URL if app_env in {"staging", "production"} else SQLITE_URL,
        secret_key="non-default-secret",
        jwt_secret_key="non-default-jwt-secret",
        allowed_origins="https://staging.lixafa.example",
    )

    assert settings.allows_automatic_schema_bootstrap is expected


def test_production_without_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            secret_key="prod-secret-key",
            jwt_secret_key="prod-jwt-secret",
            allowed_origins="https://lixafa.example",
            database_url="",
        )


def test_production_cannot_fallback_to_sqlite() -> None:
    with pytest.raises(ValidationError, match="cannot use SQLite"):
        Settings(
            _env_file=None,
            app_env="production",
            secret_key="prod-secret-key",
            jwt_secret_key="prod-jwt-secret",
            allowed_origins="https://lixafa.example",
            database_url=SQLITE_URL,
        )


def test_explicit_env_file_is_loaded(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\nDATABASE_URL=sqlite+aiosqlite:///./from-env.db\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "development"
    assert settings.database_url == "sqlite+aiosqlite:///./from-env.db"
