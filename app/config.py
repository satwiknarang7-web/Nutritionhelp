from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
