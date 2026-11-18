from datetime import date, datetime

from fastapi.testclient import TestClient

from app.days import cycle_day, cycle_on, day_for_moment
from app.db import SessionLocal
from app.main import app
from app.models import WhoopCycle, WhoopRecovery, WhoopSleep, WhoopWorkout

local = TestClient(app, client=("127.0.0.1", 50000))
HOUR = 3_600_000


def _seed():
    """Saturday's cycle (with evening pickleball) and Sunday's (bed at 10:51 PM Sat, run Sun 8:24 AM)."""
    with SessionLocal() as s:
        s.add_all([
            # Cycles start at bedtime: Fri 10:31 PM MST and Sat 10:51 PM MST (stored as UTC).
            WhoopCycle(id=1, start=datetime(2026, 11, 14, 5, 31), end=datetime(2026, 11, 15, 5, 51),
                       timezone_offset="-07:00", strain=8.5, raw={}),
            WhoopCycle(id=2, start=datetime(2026, 11, 15, 5, 51), end=None,
                       timezone_offset="-07:00", strain=16.8, raw={}),
            WhoopSleep(id="sleep-sat-night", cycle_id=2, start=datetime(2026, 11, 15, 5, 51),
                       end=datetime(2026, 11, 15, 14, 14), timezone_offset="-07:00", nap=False,
                       sleep_performance_percentage=95, total_in_bed_time_milli=int(8.4 * HOUR),
                       total_light_sleep_time_milli=4 * HOUR, total_slow_wave_sleep_time_milli=int(1.5 * HOUR),
                       total_rem_sleep_time_milli=2 * HOUR, total_awake_time_milli=int(0.9 * HOUR), raw={}),
            WhoopRecovery(cycle_id=2, sleep_id="sleep-sat-night", score_state="SCORED", recovery_score=34,
                          hrv_rmssd_milli=37.0, resting_heart_rate=66, raw={}),
            WhoopWorkout(id="pickleball", sport_name="pickleball", start=datetime(2026, 11, 15, 1, 0),
                         end=datetime(2026, 11, 15, 2, 30), timezone_offset="-07:00", strain=11.2, raw={}),
            WhoopWorkout(id="run", sport_name="running", start=datetime(2026, 11, 15, 15, 25),
                         end=datetime(2026, 11, 15, 17, 0), timezone_offset="-07:00", strain=16.5, raw={}),
        ])
        s.commit()


def test_cycle_is_labelled_with_the_day_you_woke_up():
    _seed()
    with SessionLocal() as s:
        assert cycle_day(s.get(WhoopCycle, 2)) == date(2026, 11, 15)  # began 10:51 PM Saturday
        assert cycle_day(s.get(WhoopCycle, 1)) == date(2026, 11, 14)
        assert cycle_on(s, date(2026, 11, 15)).id == 2


def test_run_gets_that_mornings_recovery_last_nights_sleep_and_the_day_before():
    _seed()
    with SessionLocal() as s:
        day = day_for_moment(s, datetime(2026, 11, 15, 15, 24))  # Sunday 8:24 AM MST
    assert day["date"] == "2026-11-15"
    assert day["recovery"]["score"] == 34 and day["recovery"]["hrv_ms"] == 37.0
    assert day["sleep"]["id"] == "sleep-sat-night"
    assert day["sleep"]["hours_asleep"] == 7.5  # light + deep + REM, not time in bed
    assert [a["sport"] for a in day["activities"]] == ["running"]
    assert [a["sport"] for a in day["previous_day"]["activities"]] == ["pickleball"]  # Saturday evening


def test_late_evening_still_belongs_to_that_day():
    _seed()
    with SessionLocal() as s:
        day = day_for_moment(s, datetime(2026, 11, 16, 5, 0))  # Sunday 10 PM MST, before bed
    assert day["date"] == "2026-11-15"


def test_day_api():
    _seed()
    r = local.get("/api/days/2026-11-15")
    assert r.status_code == 200 and r.json()["recovery"]["score"] == 34
    assert local.get("/api/days/2020-01-01").status_code == 404
