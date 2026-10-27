import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Neon pooled connection string, e.g.
    # postgresql://user:pass@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/nutritrack?sslmode=require
    database_url: str = ""
    db_pool_min: int | None = None  # default: 1 on servers, 0 on Vercel (no idle connections)
    db_pool_max: int = 10
    # Apply pending migrations on startup. Default: on when running on Vercel.
    auto_migrate: bool | None = None

    # Accounts
    app_env: str = "production"  # "development" logs email links instead of failing silently
    app_base_url: str = ""  # used in emails; defaults to the request's own URL
    session_days: int = 30
    require_email_verification: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    app_encryption_key: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"

    # External food databases (see app/external_foods.py)
    enable_openfoodfacts: bool = True
    enable_usda: bool = True
    usda_api_key: str = "DEMO_KEY"
    off_country: str = "india"
    off_user_agent: str = "NutriTrack/1.0 (https://github.com/satwiknarang7-web/nutritionhelp)"
    external_timeout: float = 8.0

    redis_url: str = ""
    cookie_secure: bool = True
    default_timezone: str = "Asia/Kolkata"

    # Cache lifetimes (seconds)
    user_cache_ttl: int = 60
    stats_cache_ttl: int = 600
    food_search_cache_ttl: int = 3600
    ai_cache_ttl: int = 7 * 24 * 3600

    max_upload_mb: int = 10

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL"))

    @property
    def should_auto_migrate(self) -> bool:
        return self.auto_migrate if self.auto_migrate is not None else self.on_vercel

    @property
    def pool_min(self) -> int:
        if self.db_pool_min is not None:
            return self.db_pool_min
        return 0 if self.on_vercel else 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
