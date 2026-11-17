"""Match Samsung runs to WHOOP workouts by time overlap.

Samsung's Start/Stop presses are exact; WHOOP auto-detects, so its workout often starts a
little late and ends late (until heart rate settles). We therefore measure how much of the
Samsung run is covered by each WHOOP workout and pick the best one, one-to-one.

The whole table is rebuilt each time; it's a few hundred rows, so this takes milliseconds.
Run on its own with:  python -m app.matching
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db, utcnow
from app.models import RunMatch, SamsungRun, WhoopWorkout

log = logging.getLogger(__name__)

RUN_SPORTS = {"running", "stroller_jogging"}
MIN_OVERLAP_FRACTION = 0.3
# A forgotten Stop press can leave the Samsung end time hours late. Cap how far past the
# active duration the window may extend, so such runs don't swallow unrelated workouts.
MAX_PAUSED = timedelta(minutes=30)


@dataclass
class MatchResult:
    runs: int
    matched: int
    matched_other_sport: int
    unmatched: int


def samsung_window(run: SamsungRun) -> tuple[datetime, datetime]:
    active_end = run.start + timedelta(milliseconds=run.duration_ms or 0)
    end = run.end or active_end
    return run.start, min(max(end, active_end), active_end + MAX_PAUSED)


def _overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> float:
    return max(0.0, (min(a[1], b[1]) - max(a[0], b[0])).total_seconds())


def rebuild_matches(session: Session) -> MatchResult:
    runs = session.scalars(select(SamsungRun)).all()
    if not runs:
        session.execute(delete(RunMatch))
        session.commit()
        return MatchResult(0, 0, 0, 0)

    lo = min(r.start for r in runs) - timedelta(hours=1)
    hi = max(samsung_window(r)[1] for r in runs) + timedelta(hours=1)
    workouts = session.scalars(
        select(WhoopWorkout).where(WhoopWorkout.start >= lo, WhoopWorkout.start <= hi)
    ).all()

    candidates = []
    for run in runs:
        sw = samsung_window(run)
        length = (sw[1] - sw[0]).total_seconds()
        if length <= 0:
            continue
        for w in workouts:
            ww = (w.start, w.end or w.start)
            ov = _overlap(sw, ww)
            if ov / length < MIN_OVERLAP_FRACTION:
                continue
            candidates.append((w.sport_name in RUN_SPORTS, ov, run, w, ov / length))

    # Best first: running-type WHOOP workouts win, then the larger overlap.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    used_runs: set[str] = set()
    used_workouts: set[str] = set()
    rows = []
    now = utcnow()
    for is_run_sport, ov, run, w, frac in candidates:
        if run.id in used_runs or w.id in used_workouts:
            continue
        used_runs.add(run.id)
        used_workouts.add(w.id)
        rows.append(
            {
                "samsung_run_id": run.id,
                "whoop_workout_id": w.id,
                "overlap_seconds": ov,
                "overlap_fraction": frac,
                "start_diff_seconds": (w.start - run.start).total_seconds(),
                "whoop_sport_name": w.sport_name,
                "matched_at": now,
            }
        )

    session.execute(delete(RunMatch))
    if rows:
        session.execute(insert(RunMatch), rows)
    session.commit()

    other = sum(1 for r in rows if r["whoop_sport_name"] not in RUN_SPORTS)
    result = MatchResult(len(runs), len(rows), other, len(runs) - len(rows))
    log.info("Run matching: %s", result)
    return result


def rebuild_all() -> MatchResult:
    with SessionLocal() as session:
        return rebuild_matches(session)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    print(rebuild_all())
