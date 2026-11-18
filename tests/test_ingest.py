from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import RunMatch, SamsungRoutePoint, SamsungRun, SamsungSample, SamsungSplit, WhoopWorkout
from app.samsung.ingest import splits_from_distance
from app.samsung.store import MILE_M

client = TestClient(app)
KEY = {"X-API-Key": "test-upload-key"}
T0 = 1_794_756_276_000  # 2026-11-15 15:24:36 UTC
SPEED = 3.0  # m/s, about 8:56/mi
PAUSE_AT_S, PAUSE_S = 600, 60  # a one-minute stop at the 10-minute mark


def _run_payload(run_id="run-1", miles=2.5, **overrides):
    """A steady run with one pause: samples and GPS every second while moving."""
    moving_s = int(miles * MILE_M / SPEED)
    samples, route = [], []
    for k in range(moving_s + 1):
        t = T0 + (k + (PAUSE_S if k >= PAUSE_AT_S else 0)) * 1000  # wall clock jumps over the pause
        samples.append({"t": t, "distance_m": k * SPEED, "speed_mps": SPEED, "cadence": 170,
                        "heart_rate": 150})  # HR must be ignored
        route.append({"t": t, "lat": 40.0 + k * 1e-5, "lon": -105.0, "alt": 1500.0, "acc": 4.0})
    payload = {
        "id": run_id, "exercise_type": 1002, "start": T0,
        "end": T0 + (moving_s + PAUSE_S) * 1000, "utc_offset_minutes": -420,
        "duration_ms": moving_s * 1000, "distance_m": moving_s * SPEED,
        "pauses": [{"start": T0 + PAUSE_AT_S * 1000, "end": T0 + (PAUSE_AT_S + PAUSE_S) * 1000}],
        "samples": samples, "route": route, "mean_heart_rate": 151,
    }
    payload.update(overrides)
    return payload


def test_upload_requires_the_key():
    assert client.post("/api/samsung/runs", json=[]).status_code == 401
    assert client.post("/api/samsung/runs", json=[], headers={"X-API-Key": "nope"}).status_code == 401


def test_upload_stores_run_with_splits_that_skip_the_pause():
    r = client.post("/api/samsung/runs", json=[_run_payload()], headers=KEY)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == "stored"

    with SessionLocal() as s:
        run = s.get(SamsungRun, "run-1")
        splits = s.query(SamsungSplit).filter_by(run_id="run-1").order_by(SamsungSplit.split_index).all()
        route = s.query(SamsungRoutePoint).filter_by(run_id="run-1").order_by(SamsungRoutePoint.seq).all()
        n_samples = s.query(SamsungSample).filter_by(run_id="run-1").count()

    assert run.timezone_offset == "UTC-0700"
    assert run.start == datetime(2026, 11, 15, 15, 24, 36)
    assert run.has_route and n_samples == len(route)
    assert "heart" not in str(run.raw)  # neither field name nor value kept

    mile_ms = MILE_M / SPEED * 1000  # ~536 s per mile at this speed
    assert [sp.is_partial for sp in splits] == [False, False, True]
    # Mile 2 contains the one-minute stop, but moving time excludes it.
    for sp in splits[:2]:
        assert abs(sp.duration_ms - mile_ms) < 1500
    assert splits[-1].end_elapsed_ms == run.duration_ms
    assert abs(splits[-1].distance_m - 0.5 * MILE_M) < 5

    # Route points carry moving time and the mile they're in.
    assert route[0].split_index == 1 and route[-1].split_index == 3
    after_pause = route[PAUSE_AT_S]
    assert after_pause.elapsed_ms == PAUSE_AT_S * 1000


def test_reupload_replaces_and_matches_whoop():
    with SessionLocal() as s:
        s.add(WhoopWorkout(id="w1", sport_name="running", raw={},
                           start=datetime(2026, 11, 15, 15, 25), end=datetime(2026, 11, 15, 16, 0)))
        s.commit()
    client.post("/api/samsung/runs", json=[_run_payload()], headers=KEY)
    r = client.post("/api/samsung/runs", json=[_run_payload()], headers=KEY)
    assert r.json()["runs_matched_to_whoop"] == 1
    with SessionLocal() as s:
        assert s.query(SamsungRun).count() == 1
        assert s.get(RunMatch, "run-1").whoop_workout_id == "w1"


def test_accidental_glitched_and_non_runs_are_skipped():
    payloads = [
        _run_payload("tiny", distance_m=5.0),
        _run_payload("glitch", duration_ms=60_000),  # 2.5 mi in one minute
        _run_payload("walk", exercise_type=1001),
    ]
    r = client.post("/api/samsung/runs", json=payloads, headers=KEY)
    reasons = {x["id"]: x.get("reason") for x in r.json()["results"]}
    assert reasons == {"tiny": "accidental", "glitch": "implausible", "walk": "not a run"}
    with SessionLocal() as s:
        assert s.query(SamsungRun).count() == 0


def test_latest_reports_newest_start():
    client.post("/api/samsung/runs", json=[_run_payload()], headers=KEY)
    assert client.get("/api/samsung/latest", headers=KEY).json() == {"latest_start": T0}


def test_other_devices_only_reach_the_upload_api():
    # TestClient connects as a non-local client, like the phone would.
    assert client.get("/").status_code == 403
    assert client.get("/api/runs").status_code == 403
    assert client.post("/auth/whoop/disconnect").status_code == 403
    assert client.get("/api/samsung/latest", headers=KEY).status_code == 200
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.get("/api/runs").status_code == 200


def _splits(run_id):
    with SessionLocal() as s:
        return s.query(SamsungSplit).filter_by(run_id=run_id).order_by(SamsungSplit.split_index).all()


def test_splits_follow_gps_when_speed_readings_lag():
    # Speed-derived distance runs 60 m behind the truth; the GPS route doesn't.
    payload = _run_payload("lag", pauses=[])
    for k, smp in enumerate(payload["samples"]):
        smp["t"] = T0 + k * 1000
        smp["distance_m"] = max(0.0, k * SPEED - 60)
    for k, pt in enumerate(payload["route"]):
        pt["t"] = T0 + k * 1000
    client.post("/api/samsung/runs", json=[payload], headers=KEY)
    mile_ms = MILE_M / SPEED * 1000
    assert abs(_splits("lag")[0].duration_ms - mile_ms) < 1500  # lagging speed would give ~+20 s


def test_splits_fall_back_to_speed_without_gps():
    client.post("/api/samsung/runs", json=[_run_payload("treadmill", route=[])], headers=KEY)
    splits = _splits("treadmill")
    assert [sp.is_partial for sp in splits] == [False, False, True]


def test_recompute_rebuilds_phone_run_splits():
    from app.samsung.ingest import recompute_phone_splits

    client.post("/api/samsung/runs", json=[_run_payload()], headers=KEY)
    with SessionLocal() as s:  # simulate splits stored by an older method
        s.query(SamsungSplit).filter_by(run_id="run-1").delete()
        s.commit()
        assert recompute_phone_splits(s) == 1
        s.commit()
    assert len(_splits("run-1")) == 3


def test_splits_interpolate_the_mile_crossing():
    # 0 m at 0 s, 2000 m at 1000 s: the mile is crossed at 1609.344/2000 of the way.
    splits = splits_from_distance([(0, 0.0), (1_000_000, 2000.0)], 1_000_000, 2000.0)
    assert splits[0]["end_elapsed_ms"] == round(1_000_000 * MILE_M / 2000)
    assert splits[1]["is_partial"] and abs(splits[1]["distance_m"] - (2000 - MILE_M)) < 1e-6
