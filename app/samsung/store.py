"""Saving Samsung runs, shared by the export importer and the phone upload endpoint."""

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.models import SamsungRoutePoint, SamsungRun, SamsungSample, SamsungSplit

MILE_M = 1609.344
PARTIAL_THRESHOLD = 0.98  # a split shorter than 98% of a mile is the partial remainder
# Accidental Start presses: in the real export these were all <= 17 m, and the shortest
# real run was over 400 m, so 100 m separates them cleanly.
MIN_RUN_DISTANCE_M = 100.0
# Recording glitches: an average pace faster than 4:00/mi (about world-record mile pace)
# can't be a real run. The one glitch in the real export averaged 0:57/mi; the fastest
# real run averaged 7:35/mi.
MIN_PACE_SEC_PER_MILE = 240.0

CHILD_TABLES = (SamsungSplit, SamsungRoutePoint, SamsungSample)


def rejection_reason(distance_m: float | None, duration_ms: int | None) -> str | None:
    """'accidental' or 'implausible' if this shouldn't be kept as a run, else None."""
    distance_m = distance_m or 0.0
    if distance_m < MIN_RUN_DISTANCE_M:
        return "accidental"
    if duration_ms and (duration_ms / 1000) / (distance_m / MILE_M) < MIN_PACE_SEC_PER_MILE:
        return "implausible"
    return None


def remove_run(session: Session, run_id: str) -> None:
    for model in CHILD_TABLES:
        session.execute(delete(model).where(model.run_id == run_id))
    session.execute(delete(SamsungRun).where(SamsungRun.id == run_id))


def save_run(
    session: Session, run: SamsungRun, splits: list[dict], route: list[dict], samples: list[dict]
) -> None:
    """Insert or replace a run and everything recorded with it. Caller commits."""
    run.has_route = bool(route)
    run.route_point_count = len(route)
    run.split_count = len(splits)
    session.merge(run)
    session.flush()
    for model in CHILD_TABLES:
        session.execute(delete(model).where(model.run_id == run.id))
    for model, rows in ((SamsungSplit, splits), (SamsungRoutePoint, route), (SamsungSample, samples)):
        if rows:
            session.execute(insert(model), [{"run_id": run.id, **r} for r in rows])
