from datetime import date, datetime

from fastapi.testclient import TestClient

from app.calendar_data import calendar_month, month_grid
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import RunMatch, SamsungRun, WhoopCycle, WhoopRecovery, WhoopWorkout

local = TestClient(app, client=("127.0.0.1", 50000))


def test_month_grid_starts_monday_ends_sunday():
    first, last = month_grid(2026, 11)  # Nov 1 2026 is a Sunday
    assert first == date(2026, 10, 26) and first.weekday() == 0
    assert last == date(2026, 12, 6) and last.weekday() == 6


def _seed():
    with SessionLocal() as s:
        s.add_all([
            # Sunday 8:24 AM MST run, and a Saturday 9:30 PM MST run (Sunday 04:30 UTC).
            SamsungRun(id="sun", exercise_type=1002, start=datetime(2026, 11, 15, 15, 24), duration_ms=4_817_000,
                       distance_m=12_874.75, timezone_offset="UTC-0700", imported_at=utcnow(), raw={}),
            SamsungRun(id="sat-night", exercise_type=1002, start=datetime(2026, 11, 15, 4, 30), duration_ms=1_800_000,
                       distance_m=4_828.0, timezone_offset="UTC-0700", imported_at=utcnow(), raw={}),
            WhoopWorkout(id="w-sun", sport_name="running", start=datetime(2026, 11, 15, 15, 25),
                         end=datetime(2026, 11, 15, 16, 50), timezone_offset="-07:00", raw={}),
            WhoopWorkout(id="pickle", sport_name="pickleball", start=datetime(2026, 11, 14, 1, 0),
                         end=datetime(2026, 11, 14, 2, 30), timezone_offset="-07:00", strain=11.0, raw={}),
            RunMatch(samsung_run_id="sun", whoop_workout_id="w-sun", overlap_seconds=1, overlap_fraction=1,
                     start_diff_seconds=60, whoop_sport_name="running", matched_at=utcnow()),
            WhoopCycle(id=7, start=datetime(2026, 11, 15, 5, 51), timezone_offset="-07:00", raw={}),
            WhoopRecovery(cycle_id=7, recovery_score=34, raw={}),
        ])
        s.commit()


def test_days_use_local_dates_and_hide_matched_whoop_runs():
    _seed()
    with SessionLocal() as s:
        cal = calendar_month(s, 2026, 11, today=date(2026, 11, 17))
    days = {d["date"]: d for w in cal["weeks"] for d in w["days"]}
    assert [r["id"] for r in days["2026-11-15"]["runs"]] == ["sun"]
    assert [r["id"] for r in days["2026-11-14"]["runs"]] == ["sat-night"]  # 9:30 PM local, not Sunday UTC
    assert days["2026-11-15"]["activities"] == []  # its WHOOP twin is the run itself
    assert [a["sport"] for a in days["2026-11-13"]["activities"]] == ["pickleball"]  # Fri 6 PM local
    assert days["2026-11-15"]["recovery"] == 34
    week = next(w for w in cal["weeks"] if w["start"] == "2026-11-09")
    assert week["runs"] == 2 and abs(week["run_miles"] - 11.0) < 0.01


def _day(cal, iso):
    return next(d for w in cal["weeks"] for d in w["days"] if d["date"] == iso)


WEEK = [  # Mon..Sun, the user's structure
    {"kind": "rest"},
    {"kind": "run", "length": "short", "effort": "tempo", "default_miles": 3},
    {"kind": "run", "length": "medium", "effort": "easy"},
    {"kind": "run", "length": "short", "effort": "easy", "default_miles": 3},
    {"kind": "rest"},
    {"kind": "unplanned"},
    {"kind": "run", "length": "long", "effort": "race_pace"},
]


def test_template_fills_weeks_from_the_phase_start():
    assert local.put("/api/plan/phases/2026-11-09", json={"name": "Build", "days": WEEK}).status_code == 200
    cal = local.get("/api/calendar?month=2026-11").json()
    assert _day(cal, "2026-11-08")["plan"] is None  # before the phase
    tue = _day(cal, "2026-11-17")["plan"]
    assert tue == {"kind": "run", "source": "template", "note": None, "length": "short", "effort": "tempo",
                   "distance_mi": 3}
    assert _day(cal, "2026-11-16")["plan"]["kind"] == "rest"
    assert _day(cal, "2026-11-21")["plan"] is None  # Saturday: unplanned
    assert _day(cal, "2026-11-22")["plan"]["distance_mi"] is None  # long run: no default miles


def test_later_phase_takes_over_and_day_changes_win():
    local.put("/api/plan/phases/2026-11-09", json={"days": WEEK})
    later = [dict(d) for d in WEEK]
    later[1] = {"kind": "run", "length": "short", "effort": "tempo", "default_miles": 4}
    local.put("/api/plan/phases/2026-11-23", json={"days": later})
    cal = local.get("/api/calendar?month=2026-11").json()
    assert _day(cal, "2026-11-17")["plan"]["distance_mi"] == 3
    assert _day(cal, "2026-11-24")["plan"]["distance_mi"] == 4

    # Change one Sunday: it stays as set; the next Sunday still follows the template.
    r = local.put("/api/plans/2026-11-22", json={"kind": "run", "length": "long", "effort": "race_pace",
                                                 "distance_mi": 9, "note": "first 9"})
    assert r.json()["plan"]["source"] == "day" and r.json()["plan"]["distance_mi"] == 9
    cal = local.get("/api/calendar?month=2026-11").json()
    assert _day(cal, "2026-11-22")["plan"]["note"] == "first 9"
    assert _day(cal, "2026-11-29")["plan"]["source"] == "template"

    # A rest day can be swapped for a run, and a planned day marked unplanned.
    local.put("/api/plans/2026-11-20", json={"kind": "run", "length": "short", "effort": "easy"})
    local.put("/api/plans/2026-11-19", json={"kind": "unplanned"})
    cal = local.get("/api/calendar?month=2026-11").json()
    assert _day(cal, "2026-11-20")["plan"]["kind"] == "run"
    assert _day(cal, "2026-11-19")["plan"] is None

    # Reset puts the day back on the template.
    assert local.delete("/api/plans/2026-11-22").json()["plan"]["source"] == "template"


def test_day_status():
    from app.calendar_data import day_status

    today = date(2026, 11, 17)
    run = {"kind": "run", "distance_mi": 6.0}
    assert day_status(run, 6.2, 1, date(2026, 11, 16), today) == "done"
    assert day_status(run, 5.8, 1, date(2026, 11, 16), today) == "done"   # 97%
    assert day_status(run, 5.0, 1, date(2026, 11, 16), today) == "short"  # 83%
    assert day_status(run, 0, 0, date(2026, 11, 16), today) == "missed"
    assert day_status(run, 0, 0, today, today) == "today"                 # not missed until it's over
    assert day_status(run, 0, 0, date(2026, 11, 18), today) == "upcoming"
    assert day_status({"kind": "run", "distance_mi": None}, 1.0, 1, date(2026, 11, 16), today) == "done"
    start = date(2026, 11, 9)
    assert day_status({"kind": "rest"}, 3.0, 1, date(2026, 11, 16), today, start) == "extra"
    assert day_status(None, 3.0, 1, date(2026, 11, 16), today, start) == "extra"
    assert day_status(None, 3.0, 1, date(2026, 11, 8), today, start) is None  # before training started
    assert day_status(None, 3.0, 1, date(2026, 11, 16), today, None) is None  # no plan at all yet
    assert day_status({"kind": "rest"}, 0, 0, date(2026, 11, 16), today, start) is None


def test_week_planned_vs_actual():
    _seed()  # Sat 3.0 mi (rest-free Saturday: unplanned) + Sun 8.0 mi
    local.put("/api/plan/phases/2026-11-09", json={"days": WEEK})
    local.put("/api/plans/2026-11-15", json={"kind": "run", "length": "long", "effort": "race_pace", "distance_mi": 8})
    with SessionLocal() as s:
        cal = calendar_month(s, 2026, 11, today=date(2026, 11, 17))
    week = next(w for w in cal["weeks"] if w["start"] == "2026-11-09")
    assert week["planned_runs"] == 4                 # Tue, Wed, Thu, Sun
    assert week["planned_miles"] == 14.0             # 3 + 3 + 8 (Wed has no default miles)
    assert week["planned_runs_without_miles"] == 1
    assert week["done"] == 1 and week["missed"] == 3 and week["extra"] == 1  # Sat run was unplanned
    assert _day(cal, "2026-11-15")["status"] == "done"
    assert _day(cal, "2026-11-14")["status"] == "extra"


def test_race_day_and_nothing_after():
    local.put("/api/plan/phases/2026-11-09", json={"days": WEEK})
    may = local.get("/api/calendar?month=2027-05").json()
    race = _day(may, "2027-05-02")["plan"]
    assert race["kind"] == "race" and race["distance_mi"] == 26.2
    assert _day(may, "2027-05-04")["plan"] is None   # a Tuesday after the race: plans have ended
    assert _day(may, "2027-04-27")["plan"]["kind"] == "run"
    assert may["race"]["date"] == "2027-05-02" and may["race"]["days_to_go"] > 0


def test_plan_validation_and_lan_block():
    assert local.put("/api/plans/2026-11-18", json={"kind": "run", "distance_mi": 0}).status_code == 422
    assert local.put("/api/plans/2026-11-18", json={"kind": "run", "effort": "sprint"}).status_code == 422
    assert local.put("/api/plans/not-a-date", json={"kind": "rest"}).status_code == 422
    assert local.put("/api/plan/phases/2026-11-09", json={"days": WEEK[:6]}).status_code == 422  # needs 7 days
    phone = TestClient(app)  # another device on the network
    assert phone.put("/api/plans/2026-11-18", json={"kind": "rest"}).status_code == 403
    assert phone.put("/api/plan/phases/2026-11-09", json={"days": WEEK}).status_code == 403


def test_calendar_api():
    _seed()
    r = local.get("/api/calendar?month=2026-11")
    assert r.status_code == 200 and len(r.json()["weeks"]) == 6
    assert local.get("/api/calendar?month=2026-13").status_code == 422
    assert local.get("/api/calendar").status_code == 200
