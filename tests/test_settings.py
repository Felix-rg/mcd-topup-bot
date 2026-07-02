import pytest
from pydantic import ValidationError

from core.settings import Settings


def test_production_requires_real_security_values() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="change-me-in-production",
            jwt_secret_key="change-me-in-production",
            default_admin_password="",
        )
