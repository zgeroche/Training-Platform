from datetime import datetime, timedelta

from sqlalchemy import delete

from app.db import SessionLocal, init_db, utcnow
from app.matching import rebuild_all
from app.models import RunMatch, SamsungRun, WhoopWorkout

init_db()

DAY = datetime(2026, 11, 15)


def _run(rid, start_h, active_min, end_after_min=None):
    start = DAY + timedelta(hours=start_h)
    return SamsungRun(
        id=rid, exercise_type=1002, start=start,
        end=start + timedelta(minutes=end_after_min if end_after_min is not None else active_min),
        duration_ms=active_min * 60_000, distance_m=5000.0, imported_at=utcnow(), raw={},
    )


def _workout(wid, sport, start_h, minutes):
    start = DAY + timedelta(hours=start_h)
    return WhoopWorkout(id=wid, sport_name=sport, start=start, end=start + timedelta(minutes=minutes), raw={})


def _reset(objs):
    with SessionLocal() as s:
        for model in (RunMatch, SamsungRun, WhoopWorkout):
            s.execute(delete(model))
        s.add_all(objs)
        s.commit()


def _matches():
    with SessionLocal() as s:
        return {m.samsung_run_id: m for m in s.query(RunMatch).all()}


def test_matches_late_whoop_and_prefers_running_sport():
    _reset([
        _run("a", 7, 30, end_after_min=35),
        _workout("w-run", "running", 7 + 4 / 60, 50),  # WHOOP starts 4 min late, ends long after
        _workout("w-act", "activity", 6.9, 60),  # covers the run fully, but isn't a run
        _run("lonely", 12, 30),  # no WHOOP workout at all
    ])
    result = rebuild_all()
    m = _matches()
    assert m["a"].whoop_workout_id == "w-run"
    assert m["a"].start_diff_seconds == 240
    assert "lonely" not in m
    assert result.unmatched == 1


def test_forgotten_stop_does_not_swallow_later_workouts():
    _reset([
        _run("forgot", 14, 30, end_after_min=6 * 60),  # Stop pressed 6 h later
        _workout("pickleball", "pickleball", 18, 90),
        _workout("w-run", "running", 14 + 2 / 60, 35),
    ])
    rebuild_all()
    m = _matches()
    assert m["forgot"].whoop_workout_id == "w-run"
    assert all(x.whoop_workout_id != "pickleball" for x in m.values())


def test_one_whoop_workout_per_run():
    # Two short back-to-back Samsung runs inside one long WHOOP workout: only one gets it.
    _reset([
        _run("first", 9, 20),
        _run("second", 9 + 25 / 60, 20),
        _workout("long", "running", 9, 50),
    ])
    rebuild_all()
    assert len(_matches()) == 1
