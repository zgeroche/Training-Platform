from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8000/auth/whoop/callback"

    database_url: str = "sqlite:///data/training.db"

    # Secret the phone app sends (X-API-Key header) to upload runs. Empty = uploads disabled.
    ingest_api_key: str = ""

    # Time zone for times shown in the app (IANA name). America/Denver = Mountain Time.
    local_timezone: str = "America/Denver"

    # Goal race. Plans stop after race day; the calendar marks it and counts down to it.
    race_day: date = date(2027, 5, 2)
    race_name: str = "Marathon"
    race_distance_mi: float = 26.2

    sync_interval_minutes: int = 20
    whoop_backfill_days: int = 0  # 0 = all available history

    @property
    def whoop_configured(self) -> bool:
        return bool(self.whoop_client_id and self.whoop_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
