"""Thin HTTP client for the WHOOP v2 developer API.

Handles auth headers, token refresh on 401, 429 back-off, pagination, and spacing
requests out so we stay under WHOOP's 100 requests/minute limit.
"""

import logging
import time
from collections.abc import Iterator
from datetime import datetime

import httpx

from app.whoop import auth

log = logging.getLogger(__name__)

API_BASE = "https://api.prod.whoop.com/developer"
PAGE_SIZE = 25  # WHOOP's maximum
MIN_REQUEST_INTERVAL = 0.7  # seconds -> ~85 requests/minute at most
MAX_RETRIES = 5


def _iso(dt: datetime) -> str:
    """Naive-UTC datetime -> the ISO format WHOOP expects."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class WhoopClient:
    def __init__(self) -> None:
        self._http = httpx.Client(base_url=API_BASE, timeout=30)
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WhoopClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        refreshed = False
        for attempt in range(MAX_RETRIES):
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

            token = auth.get_access_token()
            resp = self._http.get(path, params=params, headers={"Authorization": f"Bearer {token}"})

            if resp.status_code == 401 and not refreshed:
                auth.get_access_token(force_refresh=True)
                refreshed = True
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                log.warning("WHOOP %s returned %s; retrying in %.0fs", path, resp.status_code, delay)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"WHOOP {path} failed after {MAX_RETRIES} attempts")

    def paginate(
        self, path: str, start: datetime | None = None, end: datetime | None = None
    ) -> Iterator[dict]:
        params: dict[str, str | int] = {"limit": PAGE_SIZE}
        if start:
            params["start"] = _iso(start)
        if end:
            params["end"] = _iso(end)
        while True:
            data = self.get(path, params)
            yield from data.get("records", [])
            next_token = data.get("next_token")
            if not next_token:
                return
            params["nextToken"] = next_token
