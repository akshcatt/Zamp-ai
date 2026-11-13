"""Security for a small single-tenant deployment: optional login, signed sessions, CSRF, headers, rate limits.

Login is OFF unless both APP_USERNAME and APP_PASSWORD are set (local dev and tests stay frictionless).
When on:
  * the session is an HMAC-signed cookie (HttpOnly, SameSite=Lax, Secure on HTTPS), valid 7 days;
  * every state-changing request must echo the session's CSRF token in X-CSRF-Token;
  * login attempts are rate-limited per client IP.
APP_SECRET signs the cookie. If unset, a random one is generated per process (sessions end on restart).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "clearap_session"
SESSION_TTL = 7 * 24 * 3600
USERNAME = os.environ.get("APP_USERNAME", "").strip()
PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH_ENABLED = bool(USERNAME and PASSWORD)
SECRET = (os.environ.get("APP_SECRET") or secrets.token_hex(32)).encode()
PUBLIC_PATHS = ("/login", "/api/health", "/healthz", "/static/login.css", "/favicon.ico")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------- signed session cookie
def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(SECRET, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_session(user: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(
        {"u": user, "exp": int(time.time()) + SESSION_TTL, "csrf": secrets.token_urlsafe(24)}).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body.encode())}"


def read_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body.encode())):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    return data if data.get("exp", 0) > time.time() else None


def check_credentials(user: str, password: str) -> bool:
    ok_u = hmac.compare_digest(user.strip().encode(), USERNAME.encode())
    ok_p = hmac.compare_digest(password.encode(), PASSWORD.encode())
    return AUTH_ENABLED and ok_u and ok_p


# ---------------------------------------------------------------- rate limiting (in-memory sliding window)
class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


# ---------------------------------------------------------------- middleware
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; frame-src 'self'; object-src 'self'; "
        "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
    ),
}


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Rate limits: uploads and expensive endpoints per client IP.
        if request.method == "POST" and path in ("/api/runs", "/api/runs/batch"):
            if not limiter.allow(f"up:{client_ip(request)}", 60, 60):
                return JSONResponse({"detail": "Too many uploads. Wait a minute and try again."}, status_code=429)
        if AUTH_ENABLED and not path.startswith(PUBLIC_PATHS):
            sess = read_session(request.cookies.get(COOKIE))
            if not sess:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Sign in required"}, status_code=401)
                return RedirectResponse("/login", status_code=303)
            if request.method in UNSAFE and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sess["csrf"]):
                return JSONResponse({"detail": "Security check failed. Refresh the page and try again."}, status_code=403)
            request.state.user = sess["u"]
            request.state.csrf = sess["csrf"]
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # revalidate, so a new deploy is picked up immediately
        return response


def current_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)
