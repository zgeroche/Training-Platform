"""FastAPI app: WHOOP connect flow, background poller, and JSON endpoints.

Run with:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import html
import ipaddress
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.calendar_data import calendar_month
from app.config import get_settings
from app.db import SessionLocal, init_db, utcnow
from app.days import cycle_on, day_context, day_for_moment
from app.matching import RUN_SPORTS, rebuild_matches
from app.models import (
    PlannedRun,
    PlanPhase,
    PlanTemplateDay,
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
from app.plans import phase_dict, plans_between
from app.samsung.ingest import RunUpload, ingest_run
from app.timefmt import ago, fmt_local, to_local
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

# The server listens on the home network so the phone can upload runs. Other devices on the
# network may only reach the (key-protected) upload API; pages, run data and WHOOP controls
# stay limited to this PC.
LAN_ALLOWED_PREFIXES = ("/api/samsung/",)


def _is_this_pc(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def only_uploads_from_other_devices(request: Request, call_next):
    if not _is_this_pc(request.client.host if request.client else None) and not request.url.path.startswith(
        LAN_ALLOWED_PREFIXES
    ):
        return PlainTextResponse("Only available on the PC running the server.", status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        # Pages and their scripts change as the app grows: make browsers re-check them on every load
        # (a cheap "not modified" reply when unchanged) instead of showing a stale cached copy.
        response.headers["Cache-Control"] = "no-cache"
    return response


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
        newest_run, last_import = s.execute(select(func.max(SamsungRun.start), func.max(SamsungRun.imported_at))).one()
        # Unmatched WHOOP runs since the newest Samsung run: recorded, but not on the Runs page yet.
        # (WHOOP often starts a minute after the Samsung run, so "after the newest run" alone isn't enough.)
        waiting = s.scalars(
            select(WhoopWorkout)
            .where(WhoopWorkout.sport_name.in_(RUN_SPORTS))
            .where(WhoopWorkout.start >= (newest_run or datetime.min) - timedelta(hours=1))
            .where(WhoopWorkout.id.not_in(select(RunMatch.whoop_workout_id)))
            .order_by(WhoopWorkout.start)
        ).all()

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

    # WHOOP: one line for the whole sync (all four data types run together).
    if not states:
        whoop_status = "<p>WHOOP has not synced yet.</p>"
    else:
        successes = [st.last_success_at for st in states]
        last_ok = min(successes) if all(successes) else None
        last_try = max(st.last_attempt_at for st in states if st.last_attempt_at)
        next_sync = last_try + timedelta(minutes=max(settings.sync_interval_minutes, 5))
        whoop_status = (
            f"<p><b>WHOOP</b>: last synced {fmt_local(last_ok)} ({ago(last_ok)}). "
            f"Next automatic sync around {fmt_local(next_sync, date=False)}.</p>"
            if last_ok
            else "<p><b>WHOOP</b>: no complete sync yet.</p>"
        )
        errors = [st for st in states if st.last_error]
        if errors:
            whoop_status += "<p class=warn>The last sync had a problem: " + "; ".join(
                f"{html.escape(st.resource.removeprefix('whoop.'))}: {html.escape(st.last_error[:200])}" for st in errors
            ) + "</p>"

    # Samsung: imported by hand from an export, so say so and show what's missing.
    if newest_run is None:
        samsung_status = "<p><b>Samsung</b>: no runs imported yet.</p>"
    else:
        samsung_status = (
            f"<p><b>Samsung</b>: newest run {fmt_local(newest_run)}. "
            f"Last received {fmt_local(last_import)} ({ago(last_import)}). "
            "New runs arrive when you tap Sync to PC in the Training Sync phone app.</p>"
        )
    if waiting:
        items = "".join(
            f"<li>{fmt_local(w.start)} · strain {w.strain:.1f}</li>" if w.strain is not None
            else f"<li>{fmt_local(w.start)}</li>"
            for w in waiting
        )
        samsung_status += (
            f"<div class=waiting><b>Waiting for Samsung data:</b> WHOOP recorded "
            f"{len(waiting)} run{'s' if len(waiting) != 1 else ''} that "
            f"{'aren' if len(waiting) != 1 else 'isn'}'t on the Runs page yet. "
            f"{'They' if len(waiting) != 1 else 'It'}'ll appear after the next phone sync.<ul>{items}</ul></div>"
        )
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Training Platform</title>
<style>body{{font-family:system-ui;max-width:860px;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;margin:1rem 0}}td,th{{border:1px solid #ccc;padding:4px 10px;text-align:left}}
.warn{{color:#b45309}}.btn,button{{display:inline-block;padding:6px 14px;margin:4px 0}}form{{display:inline-block;margin-right:8px}}
.waiting{{border-left:3px solid #b45309;padding:4px 12px;margin:8px 0}}.waiting ul{{margin:4px 0}}</style>
</head><body><h1>Training Platform</h1><p><a href=/runs><b>Runs →</b></a> &nbsp; <a href=/training><b>Training →</b></a></p>{action}
<h2>Sync status</h2>{whoop_status}{samsung_status}
<h2>Stored records</h2><table>{count_rows}</table>
<p>JSON: <a href=/api/runs>/api/runs</a> · <a href=/api/workouts>/api/workouts</a> · <a href=/api/recovery>/api/recovery</a> ·
<a href=/api/sleep>/api/sleep</a> · <a href=/api/cycles>/api/cycles</a> · <a href=/docs>/docs</a></p>
</body></html>"""


# ---------- web pages (static HTML that reads the JSON API) ----------

WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.get("/runs", include_in_schema=False)
def runs_page() -> FileResponse:
    return FileResponse(WEB_DIR / "runs.html")


@app.get("/training", include_in_schema=False)
def training_page() -> FileResponse:
    return FileResponse(WEB_DIR / "training.html")


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
        run_obj = row[0]
        data["day"] = day_for_moment(s, run_obj.start)  # that morning's recovery, last night's sleep, etc.
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


@app.get("/api/days/{day}")
def get_day(day: date) -> dict:
    """One day (a WHOOP cycle): recovery, sleep, every activity, and the day before's activities."""
    with SessionLocal() as s:
        cycle = cycle_on(s, day)
        if cycle is None:
            raise HTTPException(404, f"No WHOOP data for {day.isoformat()}")
        return day_context(s, cycle)


@app.get("/api/calendar")
def get_calendar(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; default this month")) -> dict:
    """A Monday-first month grid: runs, other WHOOP activities and recovery for each day, plus week totals."""
    today = to_local(datetime.now(timezone.utc).replace(tzinfo=None)).date()
    if month:
        year, mon = (int(x) for x in month.split("-"))
        if not 1 <= mon <= 12:
            raise HTTPException(422, "Month must be 01-12")
    else:
        year, mon = today.year, today.month
    with SessionLocal() as s:
        return calendar_month(s, year, mon, today)


Kind = Literal["run", "rest", "unplanned"]
Length = Literal["short", "medium", "long"]
Effort = Literal["easy", "race_pace", "tempo"]


class PlanIn(BaseModel):
    """A day's plan, replacing what the weekly template says for that day."""

    kind: Kind = "run"
    length: Length | None = None
    effort: Effort | None = None
    distance_mi: float | None = Field(None, gt=0, le=100, description="Planned distance in miles (optional)")
    note: str | None = Field(None, max_length=200)


@app.put("/api/plans/{day}")
def set_plan(day: date, plan: PlanIn) -> dict:
    """Change one day's plan. It then stays as set, whatever the weekly template says."""
    with SessionLocal() as s:
        row = s.get(PlannedRun, day)
        now = utcnow()
        if row is None:
            row = PlannedRun(day=day, created_at=now)
            s.add(row)
        is_run = plan.kind == "run"
        row.kind = plan.kind
        row.length = plan.length if is_run else None
        row.effort = plan.effort if is_run else None
        row.distance_mi = round(plan.distance_mi, 2) if is_run and plan.distance_mi else None
        row.note = (plan.note or "").strip() or None
        row.updated_at = now
        s.commit()
        return {"date": day.isoformat(), "plan": plans_between(s, day, day)[day]}


@app.delete("/api/plans/{day}")
def reset_plan(day: date) -> dict:
    """Undo a day's change, so it follows the weekly template again. Returns the plan it now has."""
    with SessionLocal() as s:
        row = s.get(PlannedRun, day)
        if row is not None:
            s.delete(row)
            s.commit()
        return {"date": day.isoformat(), "plan": plans_between(s, day, day)[day]}


class TemplateDayIn(BaseModel):
    kind: Kind = "unplanned"
    length: Length | None = None
    effort: Effort | None = None
    default_miles: float | None = Field(None, gt=0, le=100)


class PhaseIn(BaseModel):
    name: str | None = Field(None, max_length=80)
    days: list[TemplateDayIn] = Field(min_length=7, max_length=7, description="Monday first")


@app.get("/api/plan/phases")
def list_phases() -> list[dict]:
    with SessionLocal() as s:
        return [phase_dict(s, p) for p in s.scalars(select(PlanPhase).order_by(PlanPhase.starts_on))]


@app.put("/api/plan/phases/{starts_on}")
def save_phase(starts_on: date, body: PhaseIn) -> dict:
    """Create or replace the phase starting on `starts_on` and its weekly template."""
    with SessionLocal() as s:
        now = utcnow()
        phase = s.scalars(select(PlanPhase).where(PlanPhase.starts_on == starts_on)).first()
        if phase is None:
            phase = PlanPhase(starts_on=starts_on, created_at=now)
            s.add(phase)
        phase.name = (body.name or "").strip() or None
        phase.updated_at = now
        s.flush()
        s.execute(delete(PlanTemplateDay).where(PlanTemplateDay.phase_id == phase.id))
        for wd, d in enumerate(body.days):
            is_run = d.kind == "run"
            s.add(PlanTemplateDay(phase_id=phase.id, weekday=wd, kind=d.kind,
                                  length=d.length if is_run else None, effort=d.effort if is_run else None,
                                  default_miles=d.default_miles if is_run else None))
        s.commit()
        return phase_dict(s, phase)


@app.delete("/api/plan/phases/{starts_on}", status_code=204)
def delete_phase(starts_on: date) -> None:
    with SessionLocal() as s:
        phase = s.scalars(select(PlanPhase).where(PlanPhase.starts_on == starts_on)).first()
        if phase is not None:
            s.execute(delete(PlanTemplateDay).where(PlanTemplateDay.phase_id == phase.id))
            s.delete(phase)
            s.commit()


# ---------- phone uploads ----------

def require_ingest_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().ingest_api_key
    if not expected:
        raise HTTPException(503, "Uploads are turned off: set INGEST_API_KEY in .env and restart the server.")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Missing or wrong X-API-Key")


@app.post("/api/samsung/runs", dependencies=[Depends(require_ingest_key)])
def upload_runs(runs: list[RunUpload]) -> dict:
    """Store runs sent by the phone app (replaces a run with the same id), then re-match WHOOP."""
    with SessionLocal() as s:
        results = [ingest_run(s, up) for up in runs]
        s.commit()
        matched = rebuild_matches(s).matched
    return {"results": results, "runs_matched_to_whoop": matched}


@app.get("/api/samsung/latest", dependencies=[Depends(require_ingest_key)])
def latest_samsung_run() -> dict:
    """Newest stored run start (epoch ms), so the phone knows where to resume."""
    with SessionLocal() as s:
        newest = s.scalar(select(func.max(SamsungRun.start)))
    return {"latest_start": int(newest.replace(tzinfo=timezone.utc).timestamp() * 1000) if newest else None}


@app.get("/api/recovery")
def list_recovery(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopRecovery, WhoopRecovery.created_at, limit)


@app.get("/api/sleep")
def list_sleep(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopSleep, WhoopSleep.start, limit)


@app.get("/api/cycles")
def list_cycles(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopCycle, WhoopCycle.start, limit)
