"""The day as the unit: a WHOOP cycle with its recovery, sleep and activities.

A WHOOP cycle runs from falling asleep to falling asleep. So the cycle a run falls in carries
that morning's recovery and the night of sleep before it, and the previous cycle is "the day
before" (including e.g. an evening pickleball game). Everything here is computed from the stored
tables on request; nothing is copied, so it can't go stale.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RunMatch, WhoopCycle, WhoopRecovery, WhoopSleep, WhoopWorkout

MS_PER_HOUR = 3_600_000


def _offset(tz: str | None) -> timedelta:
    """WHOOP's '-07:00' -> timedelta(hours=-7)."""
    if not tz:
        return timedelta(0)
    sign = -1 if tz.startswith("-") else 1
    hours, _, minutes = tz.lstrip("+-").partition(":")
    return sign * timedelta(hours=int(hours), minutes=int(minutes or 0))


def cycle_day(cycle: WhoopCycle) -> date:
    """The calendar day a cycle stands for: the day you woke up in it.

    Cycles start at bedtime (often the evening before), so take the local date 12 hours after
    the start: 10:51 PM Saturday -> Sunday; 12:30 AM Sunday -> Sunday.
    """
    return (cycle.start + _offset(cycle.timezone_offset) + timedelta(hours=12)).date()


def cycle_at(session: Session, ts: datetime) -> WhoopCycle | None:
    """The cycle containing a (naive UTC) moment; for rare gaps, the latest cycle that began before it."""
    return session.scalars(
        select(WhoopCycle).where(WhoopCycle.start <= ts).order_by(WhoopCycle.start.desc()).limit(1)
    ).first()


def cycle_on(session: Session, day: date) -> WhoopCycle | None:
    """The cycle for a calendar day (see cycle_day)."""
    # Cycles for `day` start between about noon the day before and noon that day (local time).
    lo = datetime.combine(day, datetime.min.time()) - timedelta(days=2)
    hi = datetime.combine(day, datetime.min.time()) + timedelta(days=2)
    for c in session.scalars(select(WhoopCycle).where(WhoopCycle.start.between(lo, hi)).order_by(WhoopCycle.start)):
        if cycle_day(c) == day:
            return c
    return None


def _previous_cycle(session: Session, cycle: WhoopCycle) -> WhoopCycle | None:
    return session.scalars(
        select(WhoopCycle).where(WhoopCycle.start < cycle.start).order_by(WhoopCycle.start.desc()).limit(1)
    ).first()


def _iso_local(ts: datetime | None, tz: str | None) -> str | None:
    if ts is None:
        return None
    return (ts.replace(tzinfo=timezone.utc) + _offset(tz)).replace(tzinfo=None).isoformat(timespec="minutes")


def _activities(session: Session, cycle: WhoopCycle) -> list[dict]:
    """Every WHOOP workout in the cycle (all sports), with the Samsung run it matched, if any."""
    end = cycle.end or datetime.max
    rows = session.execute(
        select(WhoopWorkout, RunMatch.samsung_run_id)
        .outerjoin(RunMatch, RunMatch.whoop_workout_id == WhoopWorkout.id)
        .where(WhoopWorkout.start >= cycle.start, WhoopWorkout.start < end)
        .order_by(WhoopWorkout.start)
    ).all()
    return [
        {
            "whoop_workout_id": w.id,
            "sport": w.sport_name,
            "start": w.start,
            "start_local": _iso_local(w.start, w.timezone_offset),
            "minutes": round((w.end - w.start).total_seconds() / 60) if w.end else None,
            "strain": w.strain,
            "average_heart_rate": w.average_heart_rate,
            "samsung_run_id": run_id,
        }
        for w, run_id in rows
    ]


def _sleep(s: WhoopSleep | None) -> dict | None:
    if s is None:
        return None
    asleep = sum(v or 0 for v in (s.total_light_sleep_time_milli, s.total_slow_wave_sleep_time_milli,
                                   s.total_rem_sleep_time_milli))
    return {
        "id": s.id,
        "start": s.start,
        "end": s.end,
        "start_local": _iso_local(s.start, s.timezone_offset),
        "end_local": _iso_local(s.end, s.timezone_offset),
        "score_state": s.score_state,
        "hours_asleep": round(asleep / MS_PER_HOUR, 2) if asleep else None,
        "hours_in_bed": round(s.total_in_bed_time_milli / MS_PER_HOUR, 2) if s.total_in_bed_time_milli else None,
        "performance_pct": s.sleep_performance_percentage,
        "efficiency_pct": s.sleep_efficiency_percentage,
        "consistency_pct": s.sleep_consistency_percentage,
        "respiratory_rate": s.respiratory_rate,
        "light_hours": round((s.total_light_sleep_time_milli or 0) / MS_PER_HOUR, 2),
        "deep_hours": round((s.total_slow_wave_sleep_time_milli or 0) / MS_PER_HOUR, 2),
        "rem_hours": round((s.total_rem_sleep_time_milli or 0) / MS_PER_HOUR, 2),
        "awake_hours": round((s.total_awake_time_milli or 0) / MS_PER_HOUR, 2),
        "disturbances": s.disturbance_count,
    }


def day_context(session: Session, cycle: WhoopCycle, with_previous: bool = True) -> dict:
    recovery = session.get(WhoopRecovery, cycle.id)
    sleep = session.get(WhoopSleep, recovery.sleep_id) if recovery and recovery.sleep_id else None
    if sleep is None:  # no recovery yet (e.g. still calibrating): fall back to the cycle's main sleep
        sleep = session.scalars(
            select(WhoopSleep).where(WhoopSleep.cycle_id == cycle.id, WhoopSleep.nap.is_not(True))
            .order_by(WhoopSleep.start).limit(1)
        ).first()
    out = {
        "date": cycle_day(cycle).isoformat(),
        "cycle_id": cycle.id,
        "cycle_start": cycle.start,
        "cycle_end": cycle.end,
        "in_progress": cycle.end is None,
        "day_strain": cycle.strain,
        "day_kilojoule": cycle.kilojoule,
        "recovery": None if recovery is None else {
            "score_state": recovery.score_state,
            "score": recovery.recovery_score,
            "hrv_ms": recovery.hrv_rmssd_milli,
            "resting_heart_rate": recovery.resting_heart_rate,
            "spo2_pct": recovery.spo2_percentage,
            "skin_temp_c": recovery.skin_temp_celsius,
            "calibrating": recovery.user_calibrating,
        },
        "sleep": _sleep(sleep),
        "activities": _activities(session, cycle),
    }
    if with_previous:
        prev = _previous_cycle(session, cycle)
        out["previous_day"] = None if prev is None else {
            "date": cycle_day(prev).isoformat(),
            "cycle_id": prev.id,
            "day_strain": prev.strain,
            "activities": _activities(session, prev),
        }
    return out


def day_for_moment(session: Session, ts: datetime) -> dict | None:
    cycle = cycle_at(session, ts)
    return day_context(session, cycle) if cycle else None
