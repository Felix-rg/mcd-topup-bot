from functools import lru_cache
from typing import List

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "LIXAFA PROJEK"
    app_env: str = "development"
    app_base_url: str = "http://127.0.0.1:8000"
    secret_key: str = Field(default="change-me-in-production")
    allowed_origins: str = "*"

    database_url: str = ""

    jwt_secret_key: str = Field(default="change-me-in-production")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480

    digiflazz_username: str = ""
    digiflazz_api_key: str = ""
    digiflazz_webhook_secret: str = ""
    digiflazz_base_url: str = "https://api.digiflazz.com/v1"

    default_admin_username: str = "admin"
    default_admin_password: str = ""

    tripay_api_key: str = ""
    tripay_private_key: str = ""
    tripay_merchant_code: str = ""
    tripay_base_url: str = ""

    engine_poll_interval_seconds: int = 15

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"development", "test", "staging", "production"}:
            raise ValueError("APP_ENV must be development, test, staging, or production")
        return normalized

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("secret_key", "jwt_secret_key")
    @classmethod
    def validate_security_values(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("app_env", "development").lower() in {"staging", "production"} and (
            not value or value in {"change-me-in-production", ""}
        ):
            raise ValueError("Staging and production require a non-default secret value")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, value: str, info: ValidationInfo) -> str:
        origins = [origin.strip() for origin in str(value or "").split(",") if origin.strip()]
        if info.data.get("app_env", "development").lower() in {"staging", "production"} and (not origins or "*" in origins):
            raise ValueError("Staging and production require explicit allowed_origins")
        return value

    @model_validator(mode="after")
    def validate_database_configuration(self) -> "Settings":
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL must be configured explicitly; use an explicit sqlite+aiosqlite URL for local development"
            )
        if self.is_production and self.database_url.lower().startswith("sqlite"):
            raise ValueError("Production requires PostgreSQL and cannot use SQLite")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def allows_automatic_schema_bootstrap(self) -> bool:
        """Only disposable local environments may bootstrap schema/data implicitly."""

        return self.app_env.lower() in {"development", "test"}

    @property
    def parsed_allowed_origins(self) -> List[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def resolved_tripay_base_url(self) -> str:
        if self.tripay_base_url.strip():
            return self.tripay_base_url.rstrip("/")

        if self.is_production:
            return "https://tripay.co.id/api"

        return "https://tripay.co.id/api-sandbox"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
