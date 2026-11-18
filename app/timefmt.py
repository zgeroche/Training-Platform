"""Show stored times (naive UTC) in the user's own time zone (LOCAL_TIMEZONE, default Mountain Time)."""

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import utcnow


@lru_cache
def local_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().local_timezone)


def to_local(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(local_zone())


def fmt_local(dt_utc: datetime | None, date: bool = True, zone: bool = True) -> str:
    """e.g. 'Tue, Nov 17, 8:49 PM MST' (Windows strftime has no %-d / %-I, so build it by hand)."""
    if dt_utc is None:
        return "never"
    d = to_local(dt_utc)
    time = f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"
    out = f"{d:%a}, {d:%b} {d.day}, {time}" if date else time
    return f"{out} {d.tzname()}" if zone else out


def ago(dt_utc: datetime | None) -> str:
    if dt_utc is None:
        return ""
    secs = (utcnow() - dt_utc).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{round(secs / 60)} min ago"
    if secs < 2 * 86400:
        hours = round(secs / 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{round(secs / 86400)} days ago"
