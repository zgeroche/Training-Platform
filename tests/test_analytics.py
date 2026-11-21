from datetime import date, datetime

from fastapi.testclient import TestClient

from app.analytics import analytics
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import RunMatch, SamsungRun, WhoopWorkout

local = TestClient(app, client=("127.0.0.1", 50000))
MILE = 1609.344
TODAY = date(2026, 11, 20)
WEEK = [{"kind": "rest"}, {"kind": "run", "length": "short", "effort": "tempo", "default_miles": 3},
        {"kind": "run", "length": "medium", "effort": "easy"}, {"kind": "run", "length": "short", "effort": "easy"},
        {"kind": "rest"}, {"kind": "unplanned"}, {"kind": "run", "length": "long", "effort": "race_pace"}]


def _run(rid, utc, miles, minutes):
    return SamsungRun(id=rid, exercise_type=1002, start=utc, distance_m=miles * MILE, duration_ms=minutes * 60_000,
                      timezone_offset="UTC-0700", imported_at=utcnow(), raw={})


def _seed():
    local.put("/api/plan/phases/2026-11-09", json={"days": WEEK})
    with SessionLocal() as s:
        s.add_all([
            _run("tue", datetime(2026, 11, 17, 19, 0), 3.0, 27),   # Tue: tempo
            _run("wed", datetime(2026, 11, 18, 19, 0), 5.0, 50),   # Wed: easy
            _run("thu", datetime(2026, 11, 19, 19, 0), 3.0, 30),   # Thu: easy
            _run("sat", datetime(2026, 11, 14, 17, 0), 2.0, 20),   # Sat: unplanned -> unlabeled
            _run("old", datetime(2026, 10, 1, 17, 0), 4.0, 40),    # before training: unlabeled
            WhoopWorkout(id="w-wed", sport_name="running", start=datetime(2026, 11, 18, 19, 1),
                         end=datetime(2026, 11, 18, 19, 55), average_heart_rate=150, raw={}),
            RunMatch(samsung_run_id="wed", whoop_workout_id="w-wed", overlap_seconds=1, overlap_fraction=1,
                     start_diff_seconds=60, whoop_sport_name="running", matched_at=utcnow()),
        ])
        s.commit()


def test_runs_get_their_days_planned_effort():
    _seed()
    with SessionLocal() as s:
        a = analytics(s, "3m", TODAY)
    efforts = {r["id"]: r["effort"] for r in a["runs"]}
    assert efforts == {"old": "unlabeled", "sat": "unlabeled", "tue": "tempo", "wed": "easy", "thu": "easy"}
    easy = a["by_effort"]["easy"]
    assert easy["runs"] == 2 and easy["miles"] == 8.0
    assert easy["avg_pace_sec_per_mile"] == 600.0       # 80 min over 8 mi: weighted by distance
    assert easy["avg_heart_rate"] == 150                 # only the run with a WHOOP match counts
    assert a["by_effort"]["race_pace"]["runs"] == 0
    assert a["overall"]["runs"] == 5


def test_windows():
    _seed()
    with SessionLocal() as s:
        four = analytics(s, "4w", TODAY)
        training = analytics(s, "training", TODAY)
        everything = analytics(s, "all", TODAY)
    assert four["start"] == "2026-10-24" and {r["id"] for r in four["runs"]} == {"sat", "tue", "wed", "thu"}
    assert training["start"] == "2026-11-09" and "old" not in {r["id"] for r in training["runs"]}
    assert everything["start"] == "2026-10-01"
    week = next(w for w in training["weeks"] if w["start"] == "2026-11-16")
    assert week["miles"] == 11.0 and week["planned_miles"] == 3.0   # only Tuesday has default miles in WEEK


def test_api():
    _seed()
    assert local.get("/api/analytics?window=training").status_code == 200
    assert local.get("/api/analytics?window=1y").status_code == 422
