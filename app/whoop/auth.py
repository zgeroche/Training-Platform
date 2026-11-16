"""WHOOP OAuth 2.0 (authorization code flow with refresh tokens).

WHOOP rotates refresh tokens: every refresh returns a new refresh token and the
old one stops working. `_token_lock` makes sure two threads never refresh at once.
"""

import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import OAuthToken

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

# "offline" is what makes WHOOP return a refresh token.
SCOPES = [
    "offline",
    "read:recovery",
    "read:cycles",
    "read:workout",
    "read:sleep",
    "read:profile",
    "read:body_measurement",
]

PROVIDER = "whoop"
_STATE_TTL_SECONDS = 600
_pending_states: dict[str, float] = {}
_token_lock = threading.Lock()


class WhoopAuthError(RuntimeError):
    pass


def build_authorize_url() -> str:
    settings = get_settings()
    now = time.time()
    for s, created in list(_pending_states.items()):
        if now - created > _STATE_TTL_SECONDS:
            _pending_states.pop(s, None)
    state = secrets.token_urlsafe(16)  # WHOOP requires >= 8 characters
    _pending_states[state] = now
    params = {
        "response_type": "code",
        "client_id": settings.whoop_client_id,
        "redirect_uri": settings.whoop_redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def consume_state(state: str | None) -> bool:
    if not state:
        return False
    created = _pending_states.pop(state, None)
    return created is not None and time.time() - created <= _STATE_TTL_SECONDS


def _post_token(data: dict[str, str]) -> dict:
    settings = get_settings()
    payload = {
        **data,
        "client_id": settings.whoop_client_id,
        "client_secret": settings.whoop_client_secret,
    }
    resp = httpx.post(TOKEN_URL, data=payload, timeout=30)
    if resp.status_code != 200:
        raise WhoopAuthError(f"Token request failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _save_token(body: dict) -> OAuthToken:
    with SessionLocal() as session:
        token = session.get(OAuthToken, PROVIDER) or OAuthToken(provider=PROVIDER)
        token.access_token = body["access_token"]
        # Keep the old refresh token if a response ever omits it.
        token.refresh_token = body.get("refresh_token") or token.refresh_token
        token.expires_at = utcnow() + timedelta(seconds=int(body.get("expires_in", 3600)))
        token.scope = body.get("scope")
        token.updated_at = utcnow()
        session.merge(token)
        session.commit()
        return token


def exchange_code(code: str) -> None:
    body = _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": get_settings().whoop_redirect_uri,
        }
    )
    with _token_lock:
        _save_token(body)


def is_connected() -> bool:
    with SessionLocal() as session:
        return session.get(OAuthToken, PROVIDER) is not None


def disconnect() -> None:
    with _token_lock, SessionLocal() as session:
        token = session.get(OAuthToken, PROVIDER)
        if token:
            session.delete(token)
            session.commit()


def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing it first if it is about to expire."""
    with _token_lock:
        with SessionLocal() as session:
            token = session.get(OAuthToken, PROVIDER)
        if token is None:
            raise WhoopAuthError("WHOOP is not connected. Open http://localhost:8000 and click Connect.")
        if not force_refresh and token.expires_at - utcnow() > timedelta(seconds=120):
            return token.access_token
        if not token.refresh_token:
            raise WhoopAuthError("No refresh token stored; reconnect WHOOP.")
        body = _post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "scope": "offline",
            }
        )
        return _save_token(body).access_token
