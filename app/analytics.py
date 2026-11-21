"""Analytics: runs over a time window, grouped by the effort they were planned at.

A run's effort comes from the plan for its local calendar day (Easy / Race pace / Tempo).
Runs on days without a planned run (before training started, rest days, unplanned days)
are "unlabeled": they still count in overall numbers and weekly mileage. For those, an
*estimated* effort is also given (clearly separate; see estimate_effort).

Each run also carries:
  - readiness factors: that morning's WHOOP recovery, the night's sleep, and the day before
    (strain and activities), using WHOOP cycles (sleep onset to sleep onset) as days;
  - heartbeats per mile: average heart rate x minutes per mile (lower = fitter).

Distance, time and pace come from Samsung; heart rate from the matched WHOOP workout.
"""

from bisect import bisect_right
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calendar_data import MILE_M, _samsung_local
from app.days import cycle_day
from app.models import (
    PlanPhase,
    RunMatch,
    SamsungRun,
    WhoopCycle,
    WhoopRecovery,
    WhoopSleep,
    WhoopWorkout,
)
from app.plans import EFFORTS, plans_between

WINDOWS = {"4w": 28, "3m": 91, "6m": 182}  # days back from today; plus "training" and "all"
UNLABELED = "unlabeled"
MS_PER_HOUR = 3_600_000

# Effort estimate for unlabeled runs, following the user's own week: the long run is the
# race-pace run, the pushed run has a high heart rate, everything else is easy.
LONG_RUN_MI = 7.0
TEMPO_HR_PERCENTILE = 0.75  # top quarter of the user's average run heart rates


def window_start(session: Session, window: str, today: date) -> date:
    if window in WINDOWS:
        return today - timedelta(days=WINDOWS[window] - 1)
    if window == "training":
        start = session.scalar(select(func.min(PlanPhase.starts_on)))
        return start or today
    if window == "all":
        first = session.scalar(select(func.min(SamsungRun.start)))
        return first.date() if first else today
    raise ValueError(f"Unknown window {window!r}")


def _summary(runs: list[dict]) -> dict:
    miles = sum(r["distance_mi"] for r in runs)
    secs = sum(r["duration_ms"] / 1000 for r in runs)
    hrs = [r["average_heart_rate"] for r in runs if r["average_heart_rate"] is not None]
    return {
        "runs": len(runs),
        "miles": round(miles, 2),
        "avg_distance_mi": round(miles / len(runs), 2) if runs else None,
        # Total time over total miles, so a long run counts more than a short one.
        "avg_pace_sec_per_mile": round(secs / miles, 1) if miles else None,
        "avg_heart_rate": round(sum(hrs) / len(hrs)) if hrs else None,
    }


# ---------- readiness factors ----------

class _WhoopDays:
    """WHOOP cycles, recoveries, sleeps and workouts around a time range, for fast per-run lookups."""

    def __init__(self, session: Session, lo: datetime, hi: datetime):
        pad = timedelta(days=3)
        self.cycles = session.scalars(
            select(WhoopCycle).where(WhoopCycle.start.between(lo - pad, hi + pad)).order_by(WhoopCycle.start)
        ).all()
        self.starts = [c.start for c in self.cycles]
        ids = [c.id for c in self.cycles]
        self.recovery = {r.cycle_id: r for r in session.scalars(select(WhoopRecovery).where(WhoopRecovery.cycle_id.in_(ids)))}
        sleep_ids = [r.sleep_id for r in self.recovery.values() if r.sleep_id]
        self.sleep = {s.id: s for s in session.scalars(select(WhoopSleep).where(WhoopSleep.id.in_(sleep_ids)))}
        self.workouts = session.scalars(
            select(WhoopWorkout).where(WhoopWorkout.start.between(lo - pad, hi + pad)).order_by(WhoopWorkout.start)
        ).all()
        self.workout_starts = [w.start for w in self.workouts]

    def _activities(self, start: datetime, end: datetime) -> list[str]:
        i = bisect_right(self.workout_starts, start - timedelta(microseconds=1))
        out = []
        while i < len(self.workouts) and self.workouts[i].start < end:
            out.append(self.workouts[i].sport_name or "activity")
            i += 1
        return out

    def factors(self, run_start: datetime) -> dict:
        i = bisect_right(self.starts, run_start) - 1
        if i < 0:
            return {}
        cycle = self.cycles[i]
        rec = self.recovery.get(cycle.id)
        sleep = self.sleep.get(rec.sleep_id) if rec and rec.sleep_id else None
        asleep_ms = sum(v or 0 for v in (
            sleep.total_light_sleep_time_milli, sleep.total_slow_wave_sleep_time_milli, sleep.total_rem_sleep_time_milli
        )) if sleep else 0
        prev = self.cycles[i - 1] if i > 0 else None
        return {
            "recovery": rec.recovery_score if rec else None,
            "hrv_ms": rec.hrv_rmssd_milli if rec else None,
            "resting_heart_rate": rec.resting_heart_rate if rec else None,
            "sleep_hours": round(asleep_ms / MS_PER_HOUR, 2) if asleep_ms else None,
            "sleep_performance": sleep.sleep_performance_percentage if sleep else None,
            "prev_day_strain": prev.strain if prev else None,
            "prev_day_activities": self._activities(prev.start, prev.end or cycle.start) if prev else None,
        }


# ---------- effort estimate ----------

def tempo_hr_threshold(session: Session) -> float | None:
    """Average heart rate at the user's 75th percentile across all matched non-long runs."""
    rows = session.execute(
        select(WhoopWorkout.average_heart_rate, SamsungRun.distance_m)
        .join(RunMatch, RunMatch.whoop_workout_id == WhoopWorkout.id)
        .join(SamsungRun, SamsungRun.id == RunMatch.samsung_run_id)
        .where(WhoopWorkout.average_heart_rate.is_not(None))
    ).all()
    hrs = sorted(hr for hr, dist in rows if dist and dist / MILE_M < LONG_RUN_MI)
    if len(hrs) < 8:
        return None
    return float(hrs[min(len(hrs) - 1, round(TEMPO_HR_PERCENTILE * (len(hrs) - 1)))])


def estimate_effort(distance_mi: float, avg_hr: float | None, tempo_hr: float | None) -> str | None:
    """A rough guess at effort for a run with no plan. None when there's no heart rate to go on."""
    if distance_mi >= LONG_RUN_MI:
        return "race_pace"
    if avg_hr is None or tempo_hr is None:
        return None
    return "tempo" if avg_hr >= tempo_hr else "easy"


# ---------- main ----------

def analytics(session: Session, window: str, today: date) -> dict:
    start = window_start(session, window, today)
    lo = start - timedelta(days=1)  # local dates can sit a day off UTC
    rows = session.execute(
        select(SamsungRun, WhoopWorkout.average_heart_rate)
        .outerjoin(RunMatch, RunMatch.samsung_run_id == SamsungRun.id)
        .outerjoin(WhoopWorkout, WhoopWorkout.id == RunMatch.whoop_workout_id)
        .where(SamsungRun.start >= lo)
        .order_by(SamsungRun.start)
    ).all()
    plans = plans_between(session, start, today)
    whoop = _WhoopDays(session, datetime.combine(lo, datetime.min.time()),
                       datetime.combine(today + timedelta(days=2), datetime.min.time()))
    tempo_hr = tempo_hr_threshold(session)

    runs = []
    day_before_runs: list[dict] = []  # runs on the day before the window, for its first day's "day before"
    for run, avg_hr in rows:
        local = _samsung_local(run)
        d = local.date()
        if d > today or not run.distance_m or not run.duration_ms:
            continue
        if d < start:
            if d == start - timedelta(days=1):
                prev_plan = plans_between(session, d, d)[d]
                miles = run.distance_m / MILE_M
                effort = prev_plan["effort"] if prev_plan and prev_plan["kind"] == "run" and prev_plan["effort"] else UNLABELED
                day_before_runs.append({"date": d.isoformat(), "distance_mi": round(miles, 2), "effort": effort,
                                        "estimated_effort": estimate_effort(miles, avg_hr, tempo_hr) if effort == UNLABELED else None})
            continue
        plan = plans.get(d)
        planned_run = plan is not None and plan["kind"] == "run"
        miles = run.distance_m / MILE_M
        pace = run.duration_ms / 1000 / miles
        effort = (plan["effort"] or UNLABELED) if planned_run else UNLABELED
        runs.append({
            "id": run.id,
            "date": d.isoformat(),
            "start_local": local.isoformat(timespec="minutes"),
            "distance_mi": round(miles, 2),
            "duration_ms": run.duration_ms,
            "pace_sec_per_mile": round(pace, 1),
            "effort": effort,
            "estimated_effort": estimate_effort(miles, avg_hr, tempo_hr) if effort == UNLABELED else None,
            "length": plan["length"] if planned_run else None,
            "planned_mi": plan["distance_mi"] if planned_run else None,
            "average_heart_rate": avg_hr,
            "beats_per_mile": round(avg_hr * pace / 60) if avg_hr else None,
            "factors": whoop.factors(run.start),
        })

    # Weekly mileage, Monday-first weeks covering the window.
    weeks = []
    week_start = start - timedelta(days=start.weekday())
    while week_start <= today:
        week_end = week_start + timedelta(days=6)
        in_week = [r for r in runs if week_start.isoformat() <= r["date"] <= week_end.isoformat()]
        planned = [p for dd, p in plans.items() if week_start <= dd <= week_end and p and p["kind"] in ("run", "race")]
        known = [p["distance_mi"] for p in planned if p.get("distance_mi")]
        weeks.append({
            "start": week_start.isoformat(),
            "miles": round(sum(r["distance_mi"] for r in in_week), 2),
            "runs": len(in_week),
            "planned_miles": round(sum(known), 2) if known else None,
        })
        week_start += timedelta(days=7)

    # One entry per WHOOP day (cycle) in the window, runs or not: sleep, recovery, strain, and the day before.
    runs_on: dict[str, list[dict]] = {}
    for r in day_before_runs + runs:
        runs_on.setdefault(r["date"], []).append(
            {"distance_mi": r["distance_mi"], "effort": r["effort"], "estimated_effort": r["estimated_effort"]})
    days = []
    for i, cycle in enumerate(whoop.cycles):
        d = cycle_day(cycle)
        if d < start or d > today:
            continue
        rec = whoop.recovery.get(cycle.id)
        sleep = whoop.sleep.get(rec.sleep_id) if rec and rec.sleep_id else None
        asleep_ms = sum(v or 0 for v in (
            sleep.total_light_sleep_time_milli, sleep.total_slow_wave_sleep_time_milli, sleep.total_rem_sleep_time_milli
        )) if sleep else 0
        prev = whoop.cycles[i - 1] if i > 0 else None
        prev_date = (d - timedelta(days=1)).isoformat()
        prev_acts = whoop._activities(prev.start, prev.end or cycle.start) if prev else []
        days.append({
            "date": d.isoformat(),
            "recovery": rec.recovery_score if rec else None,
            "hrv_ms": rec.hrv_rmssd_milli if rec else None,
            "resting_heart_rate": rec.resting_heart_rate if rec else None,
            "sleep_hours": round(asleep_ms / MS_PER_HOUR, 2) if asleep_ms else None,
            "sleep_performance": sleep.sleep_performance_percentage if sleep else None,
            "day_strain": cycle.strain,
            "prev_day_strain": prev.strain if prev else None,
            "prev_day_runs": runs_on.get(prev_date, []),
            "prev_day_other_activities": [a for a in prev_acts if a not in ("running", "stroller_jogging")],
        })

    by_effort = {e: _summary([r for r in runs if r["effort"] == e]) for e in (*EFFORTS, UNLABELED)}
    return {
        "window": window,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "overall": _summary(runs),
        "by_effort": by_effort,
        "estimate_rule": {"long_run_mi": LONG_RUN_MI, "tempo_hr": tempo_hr},
        "runs": runs,
        "weeks": weeks,
        "days": days,
    }
