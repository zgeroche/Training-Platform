"""Database tables.

Each WHOOP table keeps the most useful fields as columns (for querying and charts)
plus the full API record in `raw`, so nothing is lost if we need a field later.
All datetimes are naive UTC.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, datetime: DateTime}


class OAuthToken(Base):
    __tablename__ = "oauth_token"

    provider: Mapped[str] = mapped_column(String, primary_key=True)  # "whoop"
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime]
    scope: Mapped[str | None]
    updated_at: Mapped[datetime]


class SyncState(Base):
    __tablename__ = "sync_state"

    resource: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "whoop.workout"
    last_success_at: Mapped[datetime | None]
    last_attempt_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    records_last_run: Mapped[int] = mapped_column(Integer, default=0)


class WhoopUser(Base):
    __tablename__ = "whoop_user"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str | None]
    last_name: Mapped[str | None]
    email: Mapped[str | None]
    height_meter: Mapped[float | None]
    weight_kilogram: Mapped[float | None]
    max_heart_rate: Mapped[int | None]
    raw_profile: Mapped[dict[str, Any] | None]
    raw_body: Mapped[dict[str, Any] | None]
    updated_at: Mapped[datetime]


class WhoopCycle(Base):
    """A WHOOP physiological day (roughly wake to wake)."""

    __tablename__ = "whoop_cycle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None]
    start: Mapped[datetime] = mapped_column(index=True)
    end: Mapped[datetime | None]  # None while the cycle is still in progress
    timezone_offset: Mapped[str | None]
    score_state: Mapped[str | None]
    strain: Mapped[float | None]
    kilojoule: Mapped[float | None]
    average_heart_rate: Mapped[int | None]
    max_heart_rate: Mapped[int | None]
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any]]


class WhoopRecovery(Base):
    __tablename__ = "whoop_recovery"

    cycle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sleep_id: Mapped[str | None]
    user_id: Mapped[int | None]
    score_state: Mapped[str | None]
    user_calibrating: Mapped[bool | None] = mapped_column(Boolean)
    recovery_score: Mapped[float | None]
    resting_heart_rate: Mapped[float | None]
    hrv_rmssd_milli: Mapped[float | None]
    spo2_percentage: Mapped[float | None]
    skin_temp_celsius: Mapped[float | None]
    created_at: Mapped[datetime | None] = mapped_column(index=True)
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any]]


class WhoopSleep(Base):
    __tablename__ = "whoop_sleep"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID in API v2
    cycle_id: Mapped[int | None]
    user_id: Mapped[int | None]
    start: Mapped[datetime] = mapped_column(index=True)
    end: Mapped[datetime | None]
    timezone_offset: Mapped[str | None]
    nap: Mapped[bool | None] = mapped_column(Boolean)
    score_state: Mapped[str | None]
    sleep_performance_percentage: Mapped[float | None]
    sleep_efficiency_percentage: Mapped[float | None]
    sleep_consistency_percentage: Mapped[float | None]
    respiratory_rate: Mapped[float | None]
    total_in_bed_time_milli: Mapped[int | None]
    total_awake_time_milli: Mapped[int | None]
    total_light_sleep_time_milli: Mapped[int | None]
    total_slow_wave_sleep_time_milli: Mapped[int | None]
    total_rem_sleep_time_milli: Mapped[int | None]
    disturbance_count: Mapped[int | None]
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any]]


class WhoopWorkout(Base):
    __tablename__ = "whoop_workout"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID in API v2
    user_id: Mapped[int | None]
    start: Mapped[datetime] = mapped_column(index=True)
    end: Mapped[datetime | None]
    timezone_offset: Mapped[str | None]
    sport_id: Mapped[int | None]
    sport_name: Mapped[str | None]
    score_state: Mapped[str | None]
    strain: Mapped[float | None] = mapped_column(Float)
    average_heart_rate: Mapped[int | None]
    max_heart_rate: Mapped[int | None]
    kilojoule: Mapped[float | None]
    percent_recorded: Mapped[float | None]
    distance_meter: Mapped[float | None]
    altitude_gain_meter: Mapped[float | None]
    zone_zero_milli: Mapped[int | None]
    zone_one_milli: Mapped[int | None]
    zone_two_milli: Mapped[int | None]
    zone_three_milli: Mapped[int | None]
    zone_four_milli: Mapped[int | None]
    zone_five_milli: Mapped[int | None]
    updated_at: Mapped[datetime | None]
    raw: Mapped[dict[str, Any]]
