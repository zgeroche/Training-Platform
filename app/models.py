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


class SamsungRun(Base):
    """A run recorded on the Galaxy Watch. Source of truth for time, duration, distance, route and splits.

    Samsung heart-rate data is deliberately not stored; WHOOP is the HR source.
    """

    __tablename__ = "samsung_run"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Samsung datauuid
    exercise_type: Mapped[int]
    start: Mapped[datetime] = mapped_column(index=True)  # when Start was pressed
    end: Mapped[datetime | None]  # when Stop was pressed
    timezone_offset: Mapped[str | None]  # e.g. "UTC-0700"
    duration_ms: Mapped[int | None]  # active time, pauses excluded: THE run duration everywhere in the app
    distance_m: Mapped[float | None]
    mean_speed_mps: Mapped[float | None]
    max_speed_mps: Mapped[float | None]
    mean_cadence: Mapped[float | None]
    max_cadence: Mapped[float | None]
    altitude_gain_m: Mapped[float | None]
    altitude_loss_m: Mapped[float | None]
    min_altitude_m: Mapped[float | None]
    max_altitude_m: Mapped[float | None]
    vo2_max: Mapped[float | None]
    has_route: Mapped[bool] = mapped_column(Boolean, default=False)
    route_point_count: Mapped[int] = mapped_column(Integer, default=0)
    split_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime]
    raw: Mapped[dict[str, Any]]  # summary CSV row, heart-rate columns removed


class SamsungRoutePoint(Base):
    __tablename__ = "samsung_route_point"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime]
    elapsed_ms: Mapped[int | None]  # active time since start (pauses excluded)
    split_index: Mapped[int | None]  # 1-based mile this point falls in
    latitude: Mapped[float]
    longitude: Mapped[float]
    altitude_m: Mapped[float | None]
    accuracy_m: Mapped[float | None]


class SamsungSample(Base):
    """Samsung's ~1 per second readings during a run (speed, distance, cadence; never heart rate)."""

    __tablename__ = "samsung_sample"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    elapsed_ms: Mapped[int]  # active time since start (pauses excluded)
    distance_m: Mapped[float]  # cumulative distance at this sample
    speed_mps: Mapped[float | None]
    cadence: Mapped[float | None]


class SamsungSplit(Base):
    """One auto-lap from the watch: a full mile, or the partial remainder at the end."""

    __tablename__ = "samsung_split"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    split_index: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1-based
    distance_m: Mapped[float]
    duration_ms: Mapped[int]  # active time for this split
    start_elapsed_ms: Mapped[int]
    end_elapsed_ms: Mapped[int]
    is_partial: Mapped[bool] = mapped_column(Boolean)


class RunMatch(Base):
    """Links a Samsung run to the WHOOP workout recorded for it. Rebuilt by app.matching."""

    __tablename__ = "run_match"

    samsung_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    whoop_workout_id: Mapped[str] = mapped_column(String, unique=True)
    overlap_seconds: Mapped[float]
    overlap_fraction: Mapped[float]  # share of the Samsung run covered by the WHOOP workout
    start_diff_seconds: Mapped[float]  # WHOOP start minus Samsung start (positive = WHOOP started late)
    whoop_sport_name: Mapped[str | None]
    matched_at: Mapped[datetime]


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
