from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8000/auth/whoop/callback"

    database_url: str = "sqlite:///data/training.db"

    sync_interval_minutes: int = 20
    whoop_backfill_days: int = 0  # 0 = all available history

    @property
    def whoop_configured(self) -> bool:
        return bool(self.whoop_client_id and self.whoop_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
