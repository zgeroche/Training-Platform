from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

_settings = get_settings()

_url = make_url(_settings.database_url)
if _url.get_backend_name() == "sqlite" and _url.database and _url.database != ":memory:":
    Path(_url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _url.get_backend_name() == "sqlite" else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    models.Base.metadata.create_all(engine)


def utcnow() -> datetime:
    """Naive UTC timestamp. All datetimes in the database are stored as naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from an API into naive UTC."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
