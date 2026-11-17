"""FastAPI app: WHOOP connect flow, background poller, and JSON endpoints.

Run with:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    RunMatch,
    SamsungRoutePoint,
    SamsungRun,
    SamsungSample,
    SamsungSplit,
    SyncState,
    WhoopCycle,
    WhoopRecovery,
    WhoopSleep,
    WhoopWorkout,
)
from app.whoop import auth
from app.whoop.sync import sync_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


async def _poller() -> None:
    interval = max(get_settings().sync_interval_minutes, 5) * 60
    while True:
        if auth.is_connected():
            try:
                await asyncio.to_thread(sync_all)
            except Exception:
                log.exception("Background WHOOP sync failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(_poller())
    yield
    task.cancel()


app = FastAPI(title="Training Platform", lifespan=lifespan)


# ---------- status page ----------

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    settings = get_settings()
    connected = auth.is_connected()
    with SessionLocal() as s:
        counts = {
            "Workouts": s.scalar(select(func.count()).select_from(WhoopWorkout)),
            "Sleeps": s.scalar(select(func.count()).select_from(WhoopSleep)),
            "Recoveries": s.scalar(select(func.count()).select_from(WhoopRecovery)),
            "Cycles": s.scalar(select(func.count()).select_from(WhoopCycle)),
            "Samsung runs": s.scalar(select(func.count()).select_from(SamsungRun)),
            "Runs matched to WHOOP": s.scalar(select(func.count()).select_from(RunMatch)),
        }
        states = s.scalars(select(SyncState).order_by(SyncState.resource)).all()

    if not settings.whoop_configured:
        action = "<p class=warn>Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in <code>.env</code>, then restart.</p>"
    elif connected:
        action = (
            "<p>WHOOP: <b>connected</b></p>"
            "<form method=post action=/sync/whoop><button>Sync now</button></form>"
            "<form method=post action=/auth/whoop/disconnect><button>Disconnect</button></form>"
        )
    else:
        action = "<p>WHOOP: not connected</p><a class=btn href=/auth/whoop/login>Connect WHOOP</a>"

    count_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in counts.items())
    state_rows = "".join(
        f"<tr><td>{st.resource}</td><td>{st.last_success_at or '-'}</td>"
        f"<td>{st.records_last_run}</td><td>{html.escape(st.last_error or '')}</td></tr>"
        for st in states
    )
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Training Platform</title>
<style>body{{font-family:system-ui;max-width:860px;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;margin:1rem 0}}td,th{{border:1px solid #ccc;padding:4px 10px;text-align:left}}
.warn{{color:#b45309}}.btn,button{{display:inline-block;padding:6px 14px;margin:4px 0}}form{{display:inline-block;margin-right:8px}}</style>
</head><body><h1>Training Platform</h1><p><a href=/runs><b>View runs →</b></a></p>{action}
<h2>Stored records</h2><table>{count_rows}</table>
<h2>Sync status (UTC)</h2><table><tr><th>Resource</th><th>Last success</th><th>Records last run</th><th>Last error</th></tr>{state_rows}</table>
<p>JSON: <a href=/api/runs>/api/runs</a> · <a href=/api/workouts>/api/workouts</a> · <a href=/api/recovery>/api/recovery</a> ·
<a href=/api/sleep>/api/sleep</a> · <a href=/api/cycles>/api/cycles</a> · <a href=/docs>/docs</a></p>
</body></html>"""


# ---------- web pages (static HTML that reads the JSON API) ----------

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/runs", include_in_schema=False)
def runs_page() -> FileResponse:
    return FileResponse(WEB_DIR / "runs.html")


@app.get("/runs/{run_id}", include_in_schema=False)
def run_page(run_id: str) -> FileResponse:
    return FileResponse(WEB_DIR / "run.html")


# ---------- WHOOP auth ----------

@app.get("/auth/whoop/login")
def whoop_login() -> RedirectResponse:
    if not get_settings().whoop_configured:
        raise HTTPException(400, "WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET are not set in .env")
    return RedirectResponse(auth.build_authorize_url())


@app.get("/auth/whoop/callback")
def whoop_callback(
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    if error:
        raise HTTPException(400, f"WHOOP authorization failed: {error} {error_description or ''}".strip())
    if not auth.consume_state(state):
        raise HTTPException(400, "Invalid or expired state. Start again from the home page.")
    if not code:
        raise HTTPException(400, "Missing authorization code")
    auth.exchange_code(code)
    background.add_task(sync_all)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/whoop/disconnect")
def whoop_disconnect() -> RedirectResponse:
    auth.disconnect()
    return RedirectResponse("/", status_code=303)


@app.post("/sync/whoop")
def whoop_sync_now(background: BackgroundTasks) -> RedirectResponse:
    if not auth.is_connected():
        raise HTTPException(400, "WHOOP is not connected")
    background.add_task(sync_all)
    return RedirectResponse("/", status_code=303)


# ---------- JSON API ----------

def _rows(model, order_col, limit: int) -> list[dict]:
    with SessionLocal() as s:
        objs = s.scalars(select(model).order_by(order_col.desc()).limit(limit)).all()
    cols = [c.name for c in model.__table__.columns if c.name != "raw"]
    return [{c: getattr(o, c) for c in cols} for o in objs]


@app.get("/api/workouts")
def list_workouts(limit: int = Query(50, le=1000), sport: str | None = None) -> list[dict]:
    with SessionLocal() as s:
        q = select(WhoopWorkout).order_by(WhoopWorkout.start.desc()).limit(limit)
        if sport:
            q = q.where(WhoopWorkout.sport_name == sport)
        objs = s.scalars(q).all()
    cols = [c.name for c in WhoopWorkout.__table__.columns if c.name != "raw"]
    return [{c: getattr(o, c) for c in cols} for o in objs]


MILE_M = 1609.344
WHOOP_RUN_FIELDS = (
    "sport_name", "strain", "average_heart_rate", "max_heart_rate", "kilojoule",
    "zone_zero_milli", "zone_one_milli", "zone_two_milli", "zone_three_milli", "zone_four_milli", "zone_five_milli",
)


def _merged_run(run: SamsungRun, match: RunMatch | None, workout: WhoopWorkout | None) -> dict:
    """Best of both: time/duration/distance/route from Samsung, heart rate and strain from WHOOP."""
    pace = None
    if run.duration_ms and run.distance_m:
        pace = run.duration_ms / 1000 / (run.distance_m / MILE_M)
    return {
        "id": run.id,
        "start": run.start,
        "timezone_offset": run.timezone_offset,
        "duration_ms": run.duration_ms,  # Samsung active time: the run's duration
        "distance_m": run.distance_m,
        "distance_mi": run.distance_m / MILE_M if run.distance_m else None,
        "pace_sec_per_mile": pace,
        "altitude_gain_m": run.altitude_gain_m,
        "mean_cadence": run.mean_cadence,
        "has_route": run.has_route,
        "split_count": run.split_count,
        "whoop": None
        if workout is None
        else {
            "workout_id": workout.id,
            "start_diff_seconds": match.start_diff_seconds,
            "overlap_fraction": match.overlap_fraction,
            **{f: getattr(workout, f) for f in WHOOP_RUN_FIELDS},
        },
    }


def _run_query():
    return (
        select(SamsungRun, RunMatch, WhoopWorkout)
        .outerjoin(RunMatch, RunMatch.samsung_run_id == SamsungRun.id)
        .outerjoin(WhoopWorkout, WhoopWorkout.id == RunMatch.whoop_workout_id)
    )


@app.get("/api/runs")
def list_runs(limit: int = Query(50, le=1000), matched: bool | None = None) -> list[dict]:
    q = _run_query().order_by(SamsungRun.start.desc()).limit(limit)
    if matched is True:
        q = q.where(RunMatch.samsung_run_id.is_not(None))
    elif matched is False:
        q = q.where(RunMatch.samsung_run_id.is_(None))
    with SessionLocal() as s:
        return [_merged_run(*row) for row in s.execute(q).all()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, route: bool = True) -> dict:
    with SessionLocal() as s:
        row = s.execute(_run_query().where(SamsungRun.id == run_id)).first()
        if row is None:
            raise HTTPException(404, "Run not found")
        data = _merged_run(*row)
        data["splits"] = [
            {
                "split_index": sp.split_index,
                "distance_m": sp.distance_m,
                "duration_ms": sp.duration_ms,
                "pace_sec_per_mile": sp.duration_ms / 1000 / (sp.distance_m / MILE_M) if sp.distance_m else None,
                "is_partial": sp.is_partial,
            }
            for sp in s.scalars(
                select(SamsungSplit).where(SamsungSplit.run_id == run_id).order_by(SamsungSplit.split_index)
            )
        ]
        if route:
            data["route"] = [
                [p.latitude, p.longitude, p.altitude_m, p.elapsed_ms, p.split_index]
                for p in s.scalars(
                    select(SamsungRoutePoint).where(SamsungRoutePoint.run_id == run_id).order_by(SamsungRoutePoint.seq)
                )
            ]
            data["route_columns"] = ["latitude", "longitude", "altitude_m", "elapsed_ms", "split_index"]
    return data


@app.get("/api/runs/{run_id}/samples")
def get_run_samples(run_id: str) -> dict:
    """Samsung's per-second readings for one run, as compact rows (for the pace chart)."""
    with SessionLocal() as s:
        if s.get(SamsungRun, run_id) is None:
            raise HTTPException(404, "Run not found")
        rows = s.execute(
            select(SamsungSample.elapsed_ms, SamsungSample.distance_m, SamsungSample.speed_mps)
            .where(SamsungSample.run_id == run_id)
            .order_by(SamsungSample.seq)
        ).all()
    return {"columns": ["elapsed_ms", "distance_m", "speed_mps"], "rows": [list(r) for r in rows]}


@app.get("/api/recovery")
def list_recovery(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopRecovery, WhoopRecovery.created_at, limit)


@app.get("/api/sleep")
def list_sleep(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopSleep, WhoopSleep.start, limit)


@app.get("/api/cycles")
def list_cycles(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopCycle, WhoopCycle.start, limit)
