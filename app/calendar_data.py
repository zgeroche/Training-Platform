"""Calendar data: what happened on each local calendar day of a month grid.

Weeks start on Monday. Each day lists its runs (Samsung, the source of truth for distance and
time), other WHOOP activities that weren't matched to a run (pickleball, lifting, ...), and that
day's WHOOP recovery. Days are local calendar days, using each record's own time-zone offset.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.days import _offset as whoop_offset
from app.days import cycle_day
from app.models import PlanPhase, RunMatch, SamsungRun, WhoopCycle, WhoopRecovery, WhoopWorkout
from app.plans import plans_between
from app.timefmt import to_local

MILE_M = 1609.344
DONE_RATIO = 0.95  # ran at least 95% of the planned miles = done


def day_status(plan: dict | None, run_miles: float, runs: int, day: date, today: date,
               training_start: date | None = None) -> str | None:
    """How a day went against its plan.

    done    planned run (or race), and you ran: at least 95% of the planned miles, if miles were planned
    short   planned run with miles, and you ran less than 95% of them
    missed  planned run on a past day with no run (today isn't missed until it's over)
    extra   a run on a rest or unplanned day, once training has started (the first phase's start)
    upcoming / today  planned, not run yet
    None    nothing planned and nothing run (or a rest day kept, or a run before training started)
    """
    planned_run = plan is not None and plan["kind"] in ("run", "race")
    if not planned_run:
        in_training = training_start is not None and day >= training_start
        return "extra" if runs and in_training else None
    if runs:
        target = plan.get("distance_mi")
        return "done" if not target or run_miles >= target * DONE_RATIO else "short"
    if day < today:
        return "missed"
    return "today" if day == today else "upcoming"


def month_grid(year: int, month: int) -> tuple[date, date]:
    """First Monday on or before the 1st, and last Sunday on or after the month's last day."""
    first = date(year, month, 1)
    last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
    return first - timedelta(days=first.weekday()), last + timedelta(days=6 - last.weekday())


def _samsung_local(run: SamsungRun) -> datetime:
    """Run start in its own local time ('UTC-0700'); falls back to the app's time zone."""
    tz = run.timezone_offset
    if tz and tz.startswith("UTC") and len(tz) == 8:
        sign = -1 if tz[3] == "-" else 1
        return run.start + sign * timedelta(hours=int(tz[4:6]), minutes=int(tz[6:8]))
    return to_local(run.start).replace(tzinfo=None)


def calendar_days(session: Session, first: date, last: date, today: date) -> list[dict]:
    # Query a day wider on each side: local time can shift a record across midnight UTC.
    lo = datetime.combine(first - timedelta(days=1), datetime.min.time())
    hi = datetime.combine(last + timedelta(days=2), datetime.min.time())
    days: dict[date, dict] = {}
    d = first
    while d <= last:
        days[d] = {"date": d.isoformat(), "runs": [], "activities": [], "recovery": None,
                   "run_miles": 0.0}
        d += timedelta(days=1)

    run_strain = dict(session.execute(
        select(RunMatch.samsung_run_id, WhoopWorkout.strain)
        .join(WhoopWorkout, WhoopWorkout.id == RunMatch.whoop_workout_id)
        .join(SamsungRun, SamsungRun.id == RunMatch.samsung_run_id)
        .where(SamsungRun.start.between(lo, hi))
    ).all())
    for run in session.scalars(select(SamsungRun).where(SamsungRun.start.between(lo, hi)).order_by(SamsungRun.start)):
        local = _samsung_local(run)
        day = days.get(local.date())
        if day is None:
            continue
        miles = (run.distance_m or 0) / MILE_M
        pace = run.duration_ms / 1000 / miles if run.duration_ms and miles else None
        day["runs"].append({"id": run.id, "start_local": local.isoformat(timespec="minutes"),
                            "distance_mi": round(miles, 2), "duration_ms": run.duration_ms,
                            "pace_sec_per_mile": pace, "strain": run_strain.get(run.id)})
        day["run_miles"] += miles

    matched = set(session.scalars(select(RunMatch.whoop_workout_id)))
    for w in session.scalars(select(WhoopWorkout).where(WhoopWorkout.start.between(lo, hi)).order_by(WhoopWorkout.start)):
        if w.id in matched:
            continue  # already shown as its Samsung run
        local = w.start + whoop_offset(w.timezone_offset)
        day = days.get(local.date())
        if day is None:
            continue
        day["activities"].append({"whoop_workout_id": w.id, "sport": w.sport_name,
                                  "start_local": local.isoformat(timespec="minutes"),
                                  "minutes": round((w.end - w.start).total_seconds() / 60) if w.end else None,
                                  "strain": w.strain})

    cycles = session.execute(
        select(WhoopCycle, WhoopRecovery)
        .outerjoin(WhoopRecovery, WhoopRecovery.cycle_id == WhoopCycle.id)
        .where(WhoopCycle.start.between(lo - timedelta(days=1), hi))
    ).all()
    for cycle, rec in cycles:
        day = days.get(cycle_day(cycle))
        if day is not None and rec is not None and rec.recovery_score is not None:
            day["recovery"] = rec.recovery_score

    for d, plan in plans_between(session, first, last).items():
        days[d]["plan"] = plan

    training_start = session.scalar(select(func.min(PlanPhase.starts_on)))
    for d, day in days.items():
        day["run_miles"] = round(day["run_miles"], 2)
        day["status"] = day_status(day["plan"], day["run_miles"], len(day["runs"]), d, today, training_start)
    return list(days.values())


def week_summary(days: list[dict]) -> dict:
    """Planned vs actual for a Monday-Sunday week."""
    planned = [d for d in days if d["plan"] and d["plan"]["kind"] in ("run", "race")]
    known = [d["plan"]["distance_mi"] for d in planned if d["plan"].get("distance_mi")]
    return {
        "start": days[0]["date"],
        "run_miles": round(sum(d["run_miles"] for d in days), 2),
        "runs": sum(len(d["runs"]) for d in days),
        "planned_runs": len(planned),
        "planned_miles": round(sum(known), 2) if known else None,
        "planned_runs_without_miles": len(planned) - len(known),
        "done": sum(1 for d in days if d["status"] == "done"),
        "short": sum(1 for d in days if d["status"] == "short"),
        "missed": sum(1 for d in days if d["status"] == "missed"),
        "extra": sum(1 for d in days if d["status"] == "extra"),
        "days": days,
    }


def calendar_month(session: Session, year: int, month: int, today: date) -> dict:
    first, last = month_grid(year, month)
    days = calendar_days(session, first, last, today)
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    settings = get_settings()
    return {
        "year": year,
        "month": month,
        "today": today.isoformat(),
        "race": {"date": settings.race_day.isoformat(), "name": settings.race_name,
                 "distance_mi": settings.race_distance_mi, "days_to_go": (settings.race_day - today).days},
        "weeks": [week_summary(w) for w in weeks],
    }
