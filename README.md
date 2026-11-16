# Training Platform

Personal marathon-training data platform: WHOOP physiology + Samsung (Galaxy Watch) runs in one view.

**Status:** phase 1 (WHOOP sync) is built. Samsung ingest, run matching and the dashboard come next.

## Layout

```
app/
  config.py        settings from .env
  db.py            SQLite engine, timestamp helpers (all times stored as naive UTC)
  models.py        tables: oauth_token, sync_state, whoop_user/cycle/recovery/sleep/workout
  main.py          FastAPI app: status page, WHOOP connect flow, background poller, JSON API
  whoop/
    auth.py        OAuth 2.0 login, token exchange and refresh
    client.py      HTTP client: pagination, rate limiting, 401/429 handling
    sync.py        incremental sync into SQLite (python -m app.whoop.sync)
tests/
```

## Setup (Windows, PowerShell)

1. **Register a WHOOP app** at https://developer-dashboard.whoop.com
   - Redirect URL: `http://localhost:8000/auth/whoop/callback`
   - Scopes: read:recovery, read:cycles, read:workout, read:sleep, read:profile, read:body_measurement (and offline, if listed)
   - If the dashboard rejects an `http://localhost` redirect, tell Claude. The fallback is a local HTTPS certificate.
2. **Create `.env`** from the template and fill in the client ID and secret:
   ```powershell
   Copy-Item .env.example .env
   ```
3. **Install dependencies** (skip if `.venv` already exists):
   ```powershell
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
4. **Run the server:**
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```
5. Open http://localhost:8000 and click **Connect WHOOP**. After you approve, the first sync pulls your full history
   (set `WHOOP_BACKFILL_DAYS` to limit it). After that the server re-syncs every `SYNC_INTERVAL_MINUTES`.

Run the tests with `.\.venv\Scripts\python.exe -m pytest`.

## How sync works

- Each resource (cycles, sleep, recovery, workouts) re-fetches from *last success minus 3 days*. WHOOP keeps
  re-scoring recent records, and upserts make the re-fetch safe.
- Requests are spaced about 0.7 s apart (WHOOP allows 100/min). A 429 or 5xx response backs off and retries.
  A 401 refreshes the token once.
- The full API record is kept in each table's `raw` column, so fields we don't extract yet aren't lost.
- Deletions aren't picked up (that needs webhooks, which need public HTTPS).

## Open questions to verify with real data

- Whether WHOOP v2 workouts include anything beyond summary stats (no per-second HR stream is expected).
- `sport_name` values for runs (e.g. `running`), which the Samsung matching will key on.

## Security

- The server binds to `127.0.0.1` only. `.env` (client secret) and `data/` (tokens, health data) are git-ignored.
