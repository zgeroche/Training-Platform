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


def test_readiness_factors_and_beats_per_mile():
    from app.models import WhoopCycle, WhoopRecovery, WhoopSleep

    _seed()
    H = 3_600_000
    with SessionLocal() as s:
        s.add_all([
            # Tue cycle (bed Mon 10 PM MST) with recovery + sleep; Mon cycle before it with evening pickleball.
            WhoopCycle(id=1, start=datetime(2026, 11, 16, 5, 0), end=datetime(2026, 11, 17, 5, 0), strain=9.5,
                       timezone_offset="-07:00", raw={}),
            WhoopCycle(id=2, start=datetime(2026, 11, 17, 5, 0), end=datetime(2026, 11, 18, 5, 0), strain=12.0,
                       timezone_offset="-07:00", raw={}),
            WhoopSleep(id="s2", cycle_id=2, start=datetime(2026, 11, 17, 5, 0), end=datetime(2026, 11, 17, 13, 0),
                       total_light_sleep_time_milli=4 * H, total_slow_wave_sleep_time_milli=H,
                       total_rem_sleep_time_milli=2 * H, sleep_performance_percentage=88, raw={}),
            WhoopRecovery(cycle_id=2, sleep_id="s2", recovery_score=71, hrv_rmssd_milli=45.0,
                          resting_heart_rate=60, raw={}),
            WhoopWorkout(id="pb", sport_name="pickleball", start=datetime(2026, 11, 17, 1, 0),
                         end=datetime(2026, 11, 17, 2, 0), raw={}),
        ])
        s.commit()
        a = analytics(s, "training", TODAY)
    tue = next(r for r in a["runs"] if r["id"] == "tue")
    assert tue["factors"]["recovery"] == 71 and tue["factors"]["sleep_hours"] == 7.0
    assert tue["factors"]["prev_day_strain"] == 9.5
    assert tue["factors"]["prev_day_activities"] == ["pickleball"]
    wed = next(r for r in a["runs"] if r["id"] == "wed")
    assert wed["beats_per_mile"] == 1500  # 150 bpm x 10:00/mi
    assert tue["beats_per_mile"] is None   # no WHOOP match, no heart rate


def test_days_have_sleep_recovery_and_the_day_before():
    from app.models import WhoopCycle, WhoopRecovery, WhoopSleep

    _seed()
    H = 3_600_000
    with SessionLocal() as s:
        s.add_all([
            # Cycles labelled Tue Nov 17 and Wed Nov 18 (bedtimes Mon and Tue 10 PM MST).
            WhoopCycle(id=2, start=datetime(2026, 11, 17, 5, 0), end=datetime(2026, 11, 18, 5, 0), strain=12.0,
                       timezone_offset="-07:00", raw={}),
            WhoopCycle(id=3, start=datetime(2026, 11, 18, 5, 0), end=None, strain=6.0, timezone_offset="-07:00", raw={}),
            WhoopSleep(id="s3", cycle_id=3, start=datetime(2026, 11, 18, 5, 0), end=datetime(2026, 11, 18, 12, 0),
                       total_light_sleep_time_milli=3 * H, total_slow_wave_sleep_time_milli=H,
                       total_rem_sleep_time_milli=2 * H, raw={}),
            WhoopRecovery(cycle_id=3, sleep_id="s3", recovery_score=40, hrv_rmssd_milli=33.0, resting_heart_rate=64, raw={}),
            WhoopWorkout(id="pb", sport_name="pickleball", start=datetime(2026, 11, 18, 2, 0),
                         end=datetime(2026, 11, 18, 3, 0), raw={}),  # Tue 7 PM MST, in cycle 2
        ])
        s.commit()
        a = analytics(s, "training", TODAY)
    wed = next(d for d in a["days"] if d["date"] == "2026-11-18")
    assert wed["recovery"] == 40 and wed["sleep_hours"] == 6.0 and wed["day_strain"] == 6.0
    assert wed["prev_day_strain"] == 12.0
    assert wed["prev_day_runs"] == [{"distance_mi": 3.0, "effort": "tempo", "estimated_effort": None}]  # Tuesday's run
    assert wed["prev_day_other_activities"] == ["pickleball"]


def test_effort_estimate_rule():
    from app.analytics import estimate_effort

    assert estimate_effort(8.0, None, 163) == "race_pace"   # long run, even without heart rate
    assert estimate_effort(3.0, 170, 163) == "tempo"
    assert estimate_effort(3.0, 155, 163) == "easy"
    assert estimate_effort(3.0, None, 163) is None          # nothing to go on
    _seed()
    with SessionLocal() as s:
        a = analytics(s, "all", TODAY)
    labeled = {r["id"]: r["estimated_effort"] for r in a["runs"] if r["effort"] != "unlabeled"}
    assert set(labeled.values()) == {None}  # planned runs never get an estimate
    assert next(r for r in a["runs"] if r["id"] == "old")["estimated_effort"] is None  # no HR, not long


def test_api():
    _seed()
    assert local.get("/api/analytics?window=training").status_code == 200
    assert local.get("/api/analytics?window=1y").status_code == 422
