"""Analytics: runs over a time window, grouped by the effort they were planned at.

A run's effort comes from the plan for its local calendar day (Easy / Race pace / Tempo).
Runs on days without a planned run (before training started, rest days, unplanned days)
are "unlabeled": they still count in overall numbers and weekly mileage.

Distance, time and pace come from Samsung; heart rate from the matched WHOOP workout.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calendar_data import MILE_M, _samsung_local
from app.models import PlanPhase, RunMatch, SamsungRun, WhoopWorkout
from app.plans import EFFORTS, plans_between

WINDOWS = {"4w": 28, "3m": 91, "6m": 182}  # days back from today; plus "training" and "all"
UNLABELED = "unlabeled"


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

    runs = []
    for run, avg_hr in rows:
        local = _samsung_local(run)
        d = local.date()
        if d < start or d > today or not run.distance_m or not run.duration_ms:
            continue
        plan = plans.get(d)
        planned_run = plan is not None and plan["kind"] == "run"
        miles = run.distance_m / MILE_M
        runs.append({
            "id": run.id,
            "date": d.isoformat(),
            "start_local": local.isoformat(timespec="minutes"),
            "distance_mi": round(miles, 2),
            "duration_ms": run.duration_ms,
            "pace_sec_per_mile": round(run.duration_ms / 1000 / miles, 1),
            "effort": (plan["effort"] or UNLABELED) if planned_run else UNLABELED,
            "length": plan["length"] if planned_run else None,
            "planned_mi": plan["distance_mi"] if planned_run else None,
            "average_heart_rate": avg_hr,
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

    by_effort = {e: _summary([r for r in runs if r["effort"] == e]) for e in (*EFFORTS, UNLABELED)}
    return {
        "window": window,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "overall": _summary(runs),
        "by_effort": by_effort,
        "runs": runs,
        "weeks": weeks,
    }
