"""Import runs from a Samsung Health "Download personal data" export.

Usage:  python -m app.samsung.export_import "C:\\Dev\\samsung-export\\extracted"

Pass the extracted export folder (the samsunghealth_* folder or any folder above it).
Re-running on the same or a newer export is safe: each run is replaced, not duplicated.

What is imported per run: start/stop time, active duration, distance, speed, cadence,
elevation, VO2 max, the GPS route, and the watch's automatic per-mile splits.
Samsung heart-rate data is intentionally skipped (WHOOP is the HR source).
"""

import csv
import json
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db, utcnow
from app.matching import rebuild_matches
from app.models import SamsungRoutePoint, SamsungRun, SamsungSample, SamsungSplit

log = logging.getLogger(__name__)

P = "com.samsung.health.exercise."
RUN_TYPES = {1002}  # Samsung exercise_type for running
SUMMARY_CSV_GLOB = "com.samsung.shealth.exercise.[0-9]*.csv"
JSON_DIR = Path("jsons") / "com.samsung.shealth.exercise"
MILE_M = 1609.344
PARTIAL_THRESHOLD = 0.98  # a split shorter than 98% of a mile is the partial remainder
# Accidental Start presses: in the real export these were all <= 17 m, and the shortest
# real run was over 400 m, so 100 m separates them cleanly.
MIN_RUN_DISTANCE_M = 100.0
# Recording glitches: an average pace faster than 4:00/mi (about world-record mile pace)
# can't be a real run. The one glitch in the real export averaged 0:57/mi; the fastest
# real run averaged 7:35/mi.
MIN_PACE_SEC_PER_MILE = 240.0


@dataclass
class ImportResult:
    runs: int = 0
    with_route: int = 0
    route_points: int = 0
    splits: int = 0
    matched: int = 0  # runs linked to a WHOOP workout after the import
    accidental: int = 0  # near-zero-distance runs left out (accidental Start presses)
    implausible: int = 0  # runs with an impossible average pace (recording glitches) left out
    skipped: list[str] = field(default_factory=list)


# ---------- reading the export ----------

def find_export_root(path: Path) -> Path:
    """Locate the folder that holds the exercise summary CSV."""
    path = Path(path)
    if any(path.glob(SUMMARY_CSV_GLOB)):
        return path
    candidates = sorted(p.parent for p in path.glob(f"*/{SUMMARY_CSV_GLOB}"))
    candidates += sorted(p.parent for p in path.glob(f"*/*/{SUMMARY_CSV_GLOB}"))
    if not candidates:
        raise FileNotFoundError(f"No Samsung exercise CSV found under {path}")
    return candidates[-1]  # newest export if several sit side by side


def read_summary_rows(root: Path) -> list[dict[str, str]]:
    csv_path = sorted(root.glob(SUMMARY_CSV_GLOB))[-1]
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        f.readline()  # Samsung puts a metadata line above the header
        # Rows end with a trailing comma; DictReader files that empty extra value under None.
        return [{k: v for k, v in row.items() if k is not None} for row in csv.DictReader(f)]


def index_json_files(root: Path) -> dict[str, Path]:
    return {p.name: p for p in (root / JSON_DIR).rglob("*.json")}


def _load_json(index: dict[str, Path], name: str | None):
    if not name or name not in index:
        return None
    return json.loads(index[name].read_text(encoding="utf-8"))


# ---------- value helpers ----------

def _f(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _i(value: str | None) -> int | None:
    v = _f(value)
    return int(v) if v is not None else None


def _csv_time(value: str | None) -> datetime | None:
    """Samsung CSV times are UTC, e.g. '2026-11-15 15:24:35.247'."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")


def _ms_time(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def _strip_heart_rate(row: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in row.items() if "heart_rate" not in k}


# ---------- derived data ----------

def build_splits(live: list[dict], internal: list[dict], duration_ms: int | None) -> list[dict]:
    """Per-mile splits from the watch's auto-lap 'interval' numbers.

    `live` carries per-sample distance deltas; `internal` carries the interval number and
    active elapsed time for the same timestamps. A split ends where the next one starts.
    """
    meta = {m["start_time"]: m for m in internal}
    per: dict[int, dict] = {}
    for sample in live:
        m = meta.get(sample["start_time"])
        if m is None:
            continue
        acc = per.setdefault(m["interval"], {"distance": 0.0, "first_elapsed": m["elapsed_time"]})
        acc["distance"] += sample.get("distance") or 0.0

    if not per:
        return []
    order = sorted(per)
    total_ms = duration_ms or max(m["elapsed_time"] for m in internal)
    splits = []
    prev_end = 0
    for n, interval in enumerate(order, start=1):
        end = per[order[n]]["first_elapsed"] if n < len(order) else total_ms
        end = max(end, prev_end)
        distance = per[interval]["distance"]
        splits.append(
            {
                "split_index": n,
                "distance_m": distance,
                "duration_ms": end - prev_end,
                "start_elapsed_ms": prev_end,
                "end_elapsed_ms": end,
                "is_partial": distance < MILE_M * PARTIAL_THRESHOLD,
            }
        )
        prev_end = end
    return splits


def build_samples(live: list[dict], internal: list[dict]) -> list[dict]:
    """Per-second speed/distance/cadence, keyed by active elapsed time. Heart rate is dropped."""
    meta = {m["start_time"]: m for m in internal}
    samples = []
    total = 0.0
    for sample in sorted(live, key=lambda x: x["start_time"]):
        m = meta.get(sample["start_time"])
        if m is None or ("speed" not in sample and "distance" not in sample):
            continue  # HR-only rows and rows without timing are skipped
        total += sample.get("distance") or 0.0
        samples.append(
            {
                "seq": len(samples),
                "elapsed_ms": m["elapsed_time"],
                "distance_m": total,
                "speed_mps": sample.get("speed"),
                "cadence": sample.get("cadence"),
            }
        )
    return samples


def build_route(location: list[dict], location_internal: list[dict] | None) -> list[dict]:
    meta = {m["start_time"]: m for m in (location_internal or [])}
    points = []
    for seq, p in enumerate(sorted(location, key=lambda x: x["start_time"])):
        if p.get("latitude") is None or p.get("longitude") is None:
            continue
        m = meta.get(p["start_time"], {})
        points.append(
            {
                "seq": seq,
                "ts": _ms_time(p["start_time"]),
                "elapsed_ms": m.get("elapsed_time"),
                "split_index": m.get("interval"),
                "latitude": p["latitude"],
                "longitude": p["longitude"],
                "altitude_m": p.get("altitude"),
                "accuracy_m": p.get("accuracy"),
            }
        )
    return points


# ---------- import ----------

CHILD_TABLES = (SamsungSplit, SamsungRoutePoint, SamsungSample)


def _replace_children(
    session: Session, run_id: str, splits: list[dict], route: list[dict], samples: list[dict]
) -> None:
    for model in CHILD_TABLES:
        session.execute(delete(model).where(model.run_id == run_id))
    for model, rows in ((SamsungSplit, splits), (SamsungRoutePoint, route), (SamsungSample, samples)):
        if rows:
            session.execute(insert(model), [{"run_id": run_id, **r} for r in rows])


def _remove_run(session: Session, run_id: str) -> None:
    for model in CHILD_TABLES:
        session.execute(delete(model).where(model.run_id == run_id))
    session.execute(delete(SamsungRun).where(SamsungRun.id == run_id))


def import_rows(session: Session, rows: Iterable[dict[str, str]], index: dict[str, Path]) -> ImportResult:
    result = ImportResult()
    for row in rows:
        if _i(row.get(P + "exercise_type")) not in RUN_TYPES:
            continue
        run_id = row.get(P + "datauuid")
        start = _csv_time(row.get(P + "start_time"))
        if not run_id or start is None:
            result.skipped.append(run_id or "<no id>")
            continue

        distance_m = _f(row.get(P + "distance")) or 0.0
        duration_ms = _i(row.get(P + "duration"))
        too_short = distance_m < MIN_RUN_DISTANCE_M
        too_fast = (
            not too_short
            and bool(duration_ms)
            and (duration_ms / 1000) / (distance_m / MILE_M) < MIN_PACE_SEC_PER_MILE
        )
        if too_short or too_fast:
            # Also remove it if an earlier import (before these rules existed) stored it.
            _remove_run(session, run_id)
            if too_short:
                result.accidental += 1
            else:
                result.implausible += 1
            continue

        live =_load_json(index, row.get(P + "live_data")) or []
        live_internal = _load_json(index, row.get("live_data_internal")) or []
        location = _load_json(index, row.get(P + "location_data")) or []
        location_internal = _load_json(index, row.get("location_data_internal"))

        splits = build_splits(live, live_internal, duration_ms)
        route = build_route(location, location_internal)
        samples = build_samples(live, live_internal)

        session.merge(
            SamsungRun(
                id=run_id,
                exercise_type=_i(row.get(P + "exercise_type")),
                start=start,
                end=_csv_time(row.get(P + "end_time")),
                timezone_offset=row.get(P + "time_offset") or None,
                duration_ms=duration_ms,
                distance_m=_f(row.get(P + "distance")),
                mean_speed_mps=_f(row.get(P + "mean_speed")),
                max_speed_mps=_f(row.get(P + "max_speed")),
                mean_cadence=_f(row.get(P + "mean_cadence")),
                max_cadence=_f(row.get(P + "max_cadence")),
                altitude_gain_m=_f(row.get(P + "altitude_gain")),
                altitude_loss_m=_f(row.get(P + "altitude_loss")),
                min_altitude_m=_f(row.get(P + "min_altitude")),
                max_altitude_m=_f(row.get(P + "max_altitude")),
                vo2_max=_f(row.get(P + "vo2_max")),
                has_route=bool(route),
                route_point_count=len(route),
                split_count=len(splits),
                imported_at=utcnow(),
                raw=_strip_heart_rate(row),
            )
        )
        session.flush()
        _replace_children(session, run_id, splits, route, samples)

        result.runs += 1
        result.with_route += bool(route)
        result.route_points += len(route)
        result.splits += len(splits)
    session.commit()
    return result


def import_export(path: str | Path) -> ImportResult:
    root = find_export_root(Path(path))
    log.info("Importing Samsung export from %s", root)
    rows = read_summary_rows(root)
    index = index_json_files(root)
    with SessionLocal() as session:
        result = import_rows(session, rows, index)
        result.matched = rebuild_matches(session).matched
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2:
        sys.exit('Usage: python -m app.samsung.export_import "<path to extracted export>"')
    init_db()
    r = import_export(sys.argv[1])
    print(
        f"Imported {r.runs} runs ({r.with_route} with GPS): {r.route_points:,} route points, "
        f"{r.splits} splits. Matched to WHOOP: {r.matched}. "
        f"Left out: {r.accidental} accidental, {r.implausible} glitched. Skipped (bad rows): {len(r.skipped)}"
    )
