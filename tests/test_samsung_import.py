import json
from pathlib import Path

from app.db import SessionLocal, init_db
from app.models import SamsungRoutePoint, SamsungRun, SamsungSample, SamsungSplit
from app.samsung.export_import import MILE_M, build_splits, import_export

init_db()

P = "com.samsung.health.exercise."
RUN_ID = "11111111-2222-3333-4444-555555555555"
WALK_ID = "99999999-2222-3333-4444-555555555555"
TINY_ID = "00000000-2222-3333-4444-555555555555"
GLITCH_ID = "77777777-2222-3333-4444-555555555555"
T0 = 1794756276000  # epoch ms


def _write_export(tmp: Path) -> Path:
    """A tiny export: one 1.5-mile run with GPS and one walk (which must be ignored)."""
    root = tmp / "samsunghealth_test_20261115000000"
    jdir = root / "jsons" / "com.samsung.shealth.exercise" / "1"
    jdir.mkdir(parents=True)

    # 1 sample per second, 4 m each; interval 1 for the first mile, 2 afterwards.
    n = int(1.5 * MILE_M / 4)
    live, live_int, loc, loc_int = [], [], [], []
    for k in range(n):
        ts = T0 + k * 1000
        interval = 1 if (k + 1) * 4 <= MILE_M else 2
        live.append({"start_time": ts, "distance": 4.0, "speed": 4.0})
        live.append({"start_time": ts + 500, "heart_rate": 150.0})  # HR-only sample, no meta
        live_int.append({"start_time": ts, "elapsed_time": k * 1000, "interval": interval, "segment": 1})
        loc.append({"start_time": ts, "latitude": 40.0 + k * 1e-5, "longitude": -105.0, "altitude": 1500.0,
                    "accuracy": 5.0})
        loc_int.append({"start_time": ts, "elapsed_time": k * 1000, "interval": interval, "segment": 1})
    (jdir / f"{RUN_ID}.{P}live_data.json").write_text(json.dumps(live))
    (jdir / f"{RUN_ID}.live_data_internal.json").write_text(json.dumps(live_int))
    (jdir / f"{RUN_ID}.{P}location_data.json").write_text(json.dumps(loc))
    (jdir / f"{RUN_ID}.location_data_internal.json").write_text(json.dumps(loc_int))

    cols = ["live_data_internal", "location_data_internal", P + "datauuid", P + "exercise_type",
            P + "start_time", P + "end_time", P + "time_offset", P + "duration", P + "distance",
            P + "mean_speed", P + "mean_heart_rate", P + "max_heart_rate", P + "live_data", P + "location_data"]
    run = [f"{RUN_ID}.live_data_internal.json", f"{RUN_ID}.location_data_internal.json", RUN_ID, "1002",
           "2026-11-15 15:24:36.000", "2026-11-15 15:35:00.000", "UTC-0700", str(n * 1000), str(n * 4.0),
           "4.0", "150.0", "170.0", f"{RUN_ID}.{P}live_data.json", f"{RUN_ID}.{P}location_data.json"]
    walk = ["", "", WALK_ID, "1001", "2026-11-14 15:00:00.000", "2026-11-14 15:30:00.000", "UTC-0700",
            "1800000", "2000", "1.1", "", "", "", ""]
    tiny = ["", "", TINY_ID, "1002", "2026-11-13 15:00:00.000", "2026-11-13 15:00:10.000", "UTC-0700",
            "6000", "0.0", "0", "", "", "", ""]  # accidental Start press: no distance at all
    glitch = ["", "", GLITCH_ID, "1002", "2026-11-12 15:00:00.000", "2026-11-12 15:01:00.000", "UTC-0700",
              "60000", "2000", "33", "", "", "", ""]  # 1.24 mi in 1 minute: impossible
    csv_text = "com.samsung.shealth.exercise,7013005,17\n" + "\n".join(
        ",".join(r) + "," for r in (cols, run, walk, tiny, glitch)  # real exports end rows with ","
    )  # real exports end rows with ","
    (root / "com.samsung.shealth.exercise.20261115000000.csv").write_text(csv_text + "\n", encoding="utf-8")
    return tmp


def test_import_run_route_and_splits(tmp_path):
    export = _write_export(tmp_path)
    result = import_export(export)  # parent folder: importer must find the samsunghealth_* folder
    assert result.runs == 1
    assert result.with_route == 1

    with SessionLocal() as s:
        run = s.get(SamsungRun, RUN_ID)
        assert s.get(SamsungRun, WALK_ID) is None  # walks are not runs
        assert run.timezone_offset == "UTC-0700"
        assert run.has_route and run.route_point_count == result.route_points
        assert not any("heart_rate" in k for k in run.raw)  # Samsung HR is never stored

        splits = s.query(SamsungSplit).filter_by(run_id=RUN_ID).order_by(SamsungSplit.split_index).all()
        assert [sp.split_index for sp in splits] == [1, 2]
        assert abs(splits[0].distance_m - MILE_M) < 5 and not splits[0].is_partial
        assert splits[1].is_partial
        assert splits[0].end_elapsed_ms == splits[1].start_elapsed_ms
        assert splits[1].end_elapsed_ms == run.duration_ms


def test_reimport_replaces_instead_of_duplicating(tmp_path):
    export = _write_export(tmp_path)
    first = import_export(export)
    import_export(export)
    with SessionLocal() as s:
        assert s.query(SamsungRun).filter_by(id=RUN_ID).count() == 1
        assert s.query(SamsungRoutePoint).filter_by(run_id=RUN_ID).count() == first.route_points
        assert s.query(SamsungSplit).filter_by(run_id=RUN_ID).count() == 2


def test_accidental_runs_are_left_out_and_removed(tmp_path):
    from app.db import utcnow

    with SessionLocal() as s:  # as if an older import had stored it
        s.add(SamsungRun(id=TINY_ID, exercise_type=1002, start=utcnow(), distance_m=5.0,
                         imported_at=utcnow(), raw={}))
        s.commit()
    result = import_export(_write_export(tmp_path))
    assert result.accidental == 1
    assert result.implausible == 1
    assert result.runs == 1
    with SessionLocal() as s:
        assert s.get(SamsungRun, TINY_ID) is None
        assert s.get(SamsungRun, GLITCH_ID) is None


def test_speed_samples_are_stored_without_heart_rate(tmp_path):
    import_export(_write_export(tmp_path))
    with SessionLocal() as s:
        samples = s.query(SamsungSample).filter_by(run_id=RUN_ID).order_by(SamsungSample.seq).all()
    n = int(1.5 * MILE_M / 4)
    assert len(samples) == n  # the HR-only rows are dropped
    assert samples[0].distance_m == 4.0 and abs(samples[-1].distance_m - n * 4.0) < 1e-6  # cumulative
    assert all(sm.speed_mps == 4.0 for sm in samples)
    assert not any("heart" in c.name for c in SamsungSample.__table__.columns)


def test_build_splits_without_data():
    assert build_splits([], [], 1000) == []
