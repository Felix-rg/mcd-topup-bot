from app.core.settings import Settings, get_settings, settings

APP_NAME = settings.app_name
APP_ENV = settings.app_env
APP_BASE_URL = settings.app_base_url
SECRET_KEY = settings.secret_key
ALLOWED_ORIGINS = settings.allowed_origins
DATABASE_URL = settings.database_url
JWT_SECRET_KEY = settings.jwt_secret_key
JWT_ALGORITHM = settings.jwt_algorithm
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes
DIGIFLAZZ_USERNAME = settings.digiflazz_username
DIGIFLAZZ_API_KEY = settings.digiflazz_api_key
DIGIFLAZZ_KEY = settings.digiflazz_api_key
DIGIFLAZZ_SECRET = settings.digiflazz_webhook_secret
DIGIFLAZZ_WEBHOOK_SECRET = settings.digiflazz_webhook_secret
DIGIFLAZZ_BASE_URL = settings.digiflazz_base_url
TRIPAY_API_KEY = settings.tripay_api_key
TRIPAY_PRIVATE_KEY = settings.tripay_private_key
TRIPAY_MERCHANT_CODE = settings.tripay_merchant_code
TRIPAY_BASE_URL = settings.resolved_tripay_base_url
TRIPAY_CALLBACK_URL = f"{settings.app_base_url.rstrip('/')}/callback"
TRIPAY_RETURN_URL = f"{settings.app_base_url.rstrip('/')}/"
ENGINE_POLL_INTERVAL_SECONDS = settings.engine_poll_interval_seconds


def get_settings_alias() -> Settings:
    return get_settings()
