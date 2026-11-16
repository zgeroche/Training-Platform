from datetime import datetime
from urllib.parse import parse_qs, urlparse

from app.db import SessionLocal, init_db, parse_ts
from app.models import WhoopRecovery, WhoopSleep, WhoopWorkout
from app.whoop import auth
from app.whoop.sync import upsert_recovery, upsert_sleep, upsert_workout

init_db()

WORKOUT = {
    "id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
    "user_id": 9012,
    "created_at": "2026-11-01T11:25:44.774Z",
    "updated_at": "2026-11-01T14:25:44.774Z",
    "start": "2026-11-01T12:00:00.000Z",
    "end": "2026-11-01T13:05:12.000Z",
    "timezone_offset": "-05:00",
    "sport_name": "running",
    "score_state": "SCORED",
    "score": {
        "strain": 13.4,
        "average_heart_rate": 152,
        "max_heart_rate": 181,
        "kilojoule": 3500.5,
        "percent_recorded": 100,
        "distance_meter": 13100.2,
        "altitude_gain_meter": 42.1,
        "zone_durations": {
            "zone_zero_milli": 0,
            "zone_one_milli": 60000,
            "zone_two_milli": 900000,
            "zone_three_milli": 2100000,
            "zone_four_milli": 700000,
            "zone_five_milli": 52000,
        },
    },
}


def test_parse_ts_converts_to_naive_utc():
    assert parse_ts("2026-11-01T07:00:00-05:00") == datetime(2026, 11, 1, 12, 0, 0)
    assert parse_ts("2026-11-01T12:00:00.000Z") == datetime(2026, 11, 1, 12, 0, 0)
    assert parse_ts(None) is None


def test_upsert_workout_is_idempotent_and_updates():
    with SessionLocal() as s:
        upsert_workout(s, WORKOUT)
        s.commit()
        changed = {**WORKOUT, "score": {**WORKOUT["score"], "strain": 14.0}}
        upsert_workout(s, changed)
        s.commit()
        rows = s.query(WhoopWorkout).all()
    assert len(rows) == 1
    w = rows[0]
    assert w.strain == 14.0
    assert w.sport_name == "running"
    assert w.zone_three_milli == 2100000
    assert w.start == datetime(2026, 11, 1, 12, 0, 0)


def test_upsert_handles_unscored_records():
    with SessionLocal() as s:
        upsert_recovery(s, {"cycle_id": 77, "sleep_id": "abc", "score_state": "PENDING_SCORE"})
        upsert_sleep(s, {"id": "sleep-1", "start": "2026-11-01T03:00:00Z", "score_state": "UNSCORABLE"})
        s.commit()
        rec = s.get(WhoopRecovery, 77)
        sl = s.get(WhoopSleep, "sleep-1")
    assert rec.recovery_score is None
    assert sl.sleep_performance_percentage is None


def test_authorize_url_and_state():
    url = auth.build_authorize_url()
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["test-client"]
    assert "offline" in q["scope"][0].split()
    state = q["state"][0]
    assert len(state) >= 8
    assert auth.consume_state(state) is True
    assert auth.consume_state(state) is False  # single use
