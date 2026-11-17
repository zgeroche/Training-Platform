---
name: start-server
description: Start the Training Platform server (FastAPI + WHOOP poller) on http://localhost:8000 in a Terminal panel tab, unless it is already running.
disable-model-invocation: true
---

# Start the Training Platform server

1. **Check whether it is already running.** Run in PowerShell:
   `[bool](Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)`
   If `True`, don't start a second copy. Tell the user it's already running at http://localhost:8000 and stop.

2. **Check the setup exists.** `.venv\Scripts\python.exe` and `.env` must both exist in the project root.
   If either is missing, tell the user which one and point them to the Setup section of README.md. Don't start the server.

3. **Start it in a Terminal panel tab** so the user can watch the logs and stop it with Ctrl-C.
   Use `mcp__terminal__run_in_terminal` (load it with ToolSearch if it is deferred) with:
   - command: `& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
   - cwd: the project root (absolute path)
   - title: `server`

4. **Confirm it came up.** Read the tab with `mcp__terminal__read_terminal` (`wait_for_output_ms` about 5000).
   - If it shows `Uvicorn running on http://127.0.0.1:8000`, it worked.
   - If it shows an error (for example a traceback, or the port already in use), report the relevant lines.

5. **Report briefly.** Say that the server is running, link the tab as `[server](#terminal/<tab_id>)`, and give the status page, http://localhost:8000.
   Mention that it syncs WHOOP right away, then every `SYNC_INTERVAL_MINUTES`.
