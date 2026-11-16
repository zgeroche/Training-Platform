"""FastAPI app: WHOOP connect flow, background poller, and JSON endpoints.

Run with:  uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import html
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import SyncState, WhoopCycle, WhoopRecovery, WhoopSleep, WhoopWorkout
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
</head><body><h1>Training Platform</h1>{action}
<h2>Stored records</h2><table>{count_rows}</table>
<h2>Sync status (UTC)</h2><table><tr><th>Resource</th><th>Last success</th><th>Records last run</th><th>Last error</th></tr>{state_rows}</table>
<p>JSON: <a href=/api/workouts>/api/workouts</a> · <a href=/api/recovery>/api/recovery</a> ·
<a href=/api/sleep>/api/sleep</a> · <a href=/api/cycles>/api/cycles</a> · <a href=/docs>/docs</a></p>
</body></html>"""


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


@app.get("/api/recovery")
def list_recovery(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopRecovery, WhoopRecovery.created_at, limit)


@app.get("/api/sleep")
def list_sleep(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopSleep, WhoopSleep.start, limit)


@app.get("/api/cycles")
def list_cycles(limit: int = Query(50, le=1000)) -> list[dict]:
    return _rows(WhoopCycle, WhoopCycle.start, limit)
