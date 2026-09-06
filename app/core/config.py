import os
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration de l'application Coaching IA V2
    """
    # Environment & Server
    ENVIRONMENT: str = "development"
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    DEBUG: bool = True

    # Supabase (PostgreSQL)
    SUPABASE_URL: str = "https://your-project.supabase.co"
    SUPABASE_KEY: str = "your-anon-or-service-role-key"
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None

    # Google Gemini API
    GEMINI_API_KEY: Optional[str] = None

    # Telegram Bot & Webhook API
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = None
    WEBHOOK_SECRET: Optional[str] = None
    RENDER_EXTERNAL_URL: Optional[str] = None
    WEBHOOK_URL: Optional[str] = None
    COACH_TELEGRAM_ID: Optional[int] = None
    ADMIN_TELEGRAM_IDS: Optional[str] = None

    # Readiness Weights & Thresholds
    WEIGHT_SLEEP: float = 0.25
    WEIGHT_ENERGY: float = 0.25
    WEIGHT_FATIGUE: float = 0.20
    WEIGHT_STRESS: float = 0.15
    WEIGHT_SORENESS: float = 0.15

    THRESHOLD_GREEN: float = 3.8
    THRESHOLD_ORANGE: float = 2.5
    SORENESS_RED_THRESHOLD: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

