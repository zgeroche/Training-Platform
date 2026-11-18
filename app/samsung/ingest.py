"""Runs uploaded by the phone app (Samsung Health Data SDK) instead of read from an export.

The phone sends one JSON document per run (see RunUpload). Times are epoch milliseconds (UTC),
so there's no time-zone ambiguity. The server works out active elapsed time, mile splits and
the route's split numbers, then stores the run exactly like an exported one.

Samsung heart rate is never accepted: the upload format has no field for it.
"""

from bisect import bisect_right
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import SamsungRun
from app.samsung.store import MILE_M, PARTIAL_THRESHOLD, rejection_reason, remove_run, save_run

RUNNING = 1002  # Samsung exercise type


class RoutePointIn(BaseModel):
    t: int = Field(description="Epoch ms (UTC)")
    lat: float
    lon: float
    alt: float | None = None
    acc: float | None = Field(None, description="Horizontal accuracy, meters")


class SampleIn(BaseModel):
    t: int = Field(description="Epoch ms (UTC)")
    distance_m: float | None = Field(None, description="Cumulative distance since the start")
    speed_mps: float | None = None
    cadence: float | None = None
    elapsed_ms: int | None = Field(None, description="Active time since start, if the SDK gives it")


class PauseIn(BaseModel):
    start: int = Field(description="Epoch ms (UTC)")
    end: int = Field(description="Epoch ms (UTC)")


class RunUpload(BaseModel):
    model_config = ConfigDict(extra="ignore")  # unknown fields (and any heart rate) are dropped

    id: str = Field(min_length=1, description="Samsung's uid for the exercise session")
    exercise_type: int = RUNNING
    start: int = Field(description="Epoch ms (UTC) when Start was pressed")
    end: int | None = Field(None, description="Epoch ms (UTC) when Stop was pressed")
    utc_offset_minutes: int | None = Field(None, description="Local offset at the start, e.g. -420 for MST")
    duration_ms: int | None = Field(None, description="Active time, pauses excluded")
    distance_m: float | None = None
    mean_speed_mps: float | None = None
    max_speed_mps: float | None = None
    mean_cadence: float | None = None
    max_cadence: float | None = None
    altitude_gain_m: float | None = None
    altitude_loss_m: float | None = None
    min_altitude_m: float | None = None
    max_altitude_m: float | None = None
    vo2_max: float | None = None
    pauses: list[PauseIn] = []
    route: list[RoutePointIn] = []
    samples: list[SampleIn] = []


# ---------- derived data ----------

def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def _offset_label(minutes: int | None) -> str | None:
    """-420 -> 'UTC-0700' (the export's format)."""
    if minutes is None:
        return None
    sign = "-" if minutes < 0 else "+"
    return f"UTC{sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"


def elapsed_at(t: int, start: int, pauses: list[PauseIn]) -> int:
    """Active time at wall-clock t: time since start minus any paused time before t."""
    paused = sum(max(0, min(p.end, t) - p.start) for p in pauses if p.start < t)
    return max(0, t - start - paused)


def splits_from_distance(points: list[tuple[int, float]], duration_ms: int | None,
                         total_distance_m: float | None) -> list[dict]:
    """Mile splits from (active elapsed ms, cumulative distance m) points.

    Each mile ends at the moment the distance crosses n x 1 mile (interpolated between
    readings). The last split is whatever is left after the final full mile.
    """
    pts = sorted((e, d) for e, d in points if d is not None)
    if len(pts) < 2:
        return []
    covered = pts[-1][1]
    total = max(total_distance_m or 0.0, covered)
    end_ms = max(duration_ms or 0, pts[-1][0])

    boundaries: list[int] = []  # elapsed ms at each full mile
    k = 1
    for (e0, d0), (e1, d1) in zip(pts, pts[1:]):
        while d1 >= k * MILE_M and k * MILE_M <= covered:
            target = k * MILE_M
            frac = 0.0 if d1 == d0 else min(1.0, max(0.0, (target - d0) / (d1 - d0)))
            boundaries.append(round(e0 + frac * (e1 - e0)))
            k += 1

    splits = []
    prev = 0
    for n, b in enumerate(boundaries, start=1):
        splits.append({"split_index": n, "distance_m": MILE_M, "duration_ms": b - prev,
                       "start_elapsed_ms": prev, "end_elapsed_ms": b, "is_partial": False})
        prev = b
    leftover = total - len(boundaries) * MILE_M
    if leftover > 0.5 and end_ms > prev:
        splits.append({"split_index": len(boundaries) + 1, "distance_m": leftover, "duration_ms": end_ms - prev,
                       "start_elapsed_ms": prev, "end_elapsed_ms": end_ms,
                       "is_partial": leftover < MILE_M * PARTIAL_THRESHOLD})
    return splits


def _split_of(elapsed: int | None, splits: list[dict]) -> int | None:
    if elapsed is None or not splits:
        return None
    ends = [s["end_elapsed_ms"] for s in splits]
    return splits[min(bisect_right(ends, elapsed), len(splits) - 1)]["split_index"]


def build_from_upload(up: RunUpload) -> tuple[SamsungRun, list[dict], list[dict], list[dict]]:
    pauses = sorted(up.pauses, key=lambda p: p.start)

    samples = []
    for s in sorted(up.samples, key=lambda s: s.t):
        if s.distance_m is None and s.speed_mps is None:
            continue
        samples.append({
            "elapsed_ms": s.elapsed_ms if s.elapsed_ms is not None else elapsed_at(s.t, up.start, pauses),
            "distance_m": s.distance_m,
            "speed_mps": s.speed_mps,
            "cadence": s.cadence,
        })
    # Fill gaps in cumulative distance (a speed-only reading carries the last known distance).
    last = 0.0
    for s in samples:
        last = s["distance_m"] if s["distance_m"] is not None else last
        s["distance_m"] = last
    for seq, s in enumerate(samples):
        s["seq"] = seq

    splits = splits_from_distance([(s["elapsed_ms"], s["distance_m"]) for s in samples],
                                  up.duration_ms, up.distance_m)

    route = []
    for p in sorted(up.route, key=lambda p: p.t):
        elapsed = elapsed_at(p.t, up.start, pauses)
        route.append({"seq": len(route), "ts": _utc(p.t), "elapsed_ms": elapsed,
                      "split_index": _split_of(elapsed, splits), "latitude": p.lat, "longitude": p.lon,
                      "altitude_m": p.alt, "accuracy_m": p.acc})

    run = SamsungRun(
        id=up.id, exercise_type=up.exercise_type, start=_utc(up.start),
        end=_utc(up.end) if up.end else None, timezone_offset=_offset_label(up.utc_offset_minutes),
        duration_ms=up.duration_ms, distance_m=up.distance_m,
        mean_speed_mps=up.mean_speed_mps, max_speed_mps=up.max_speed_mps,
        mean_cadence=up.mean_cadence, max_cadence=up.max_cadence,
        altitude_gain_m=up.altitude_gain_m, altitude_loss_m=up.altitude_loss_m,
        min_altitude_m=up.min_altitude_m, max_altitude_m=up.max_altitude_m, vo2_max=up.vo2_max,
        imported_at=utcnow(),
        raw={"source": "phone", **up.model_dump(exclude={"route", "samples", "pauses"})},
    )
    return run, splits, route, samples


def ingest_run(session: Session, up: RunUpload) -> dict:
    """Store (or replace) one uploaded run. Returns a short status for the phone. Caller commits."""
    if up.exercise_type != RUNNING:
        return {"id": up.id, "status": "skipped", "reason": "not a run"}
    reason = rejection_reason(up.distance_m, up.duration_ms)
    if reason:
        remove_run(session, up.id)
        return {"id": up.id, "status": "skipped", "reason": reason}
    run, splits, route, samples = build_from_upload(up)
    save_run(session, run, splits, route, samples)
    return {"id": up.id, "status": "stored", "splits": len(splits), "route_points": len(route),
            "samples": len(samples)}
