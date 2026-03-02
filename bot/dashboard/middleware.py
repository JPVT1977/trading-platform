"""Security middleware — auth, CSRF, rate limiting, and security headers."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict

from aiohttp import web
from loguru import logger

from bot.dashboard import queries as dq

# ---------------------------------------------------------------------------
# Public path config
# ---------------------------------------------------------------------------

# Paths that don't require authentication
PUBLIC_PATHS = frozenset({
    "/login",
    "/reset-password",
    "/health",
})

PUBLIC_PREFIXES = (
    "/static/",
    "/public/",
)

# Paths exempt from CSRF validation (login itself generates the session)
CSRF_EXEMPT_PATHS = frozenset({
    "/login",
    "/reset-password",
})

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per-IP)
# ---------------------------------------------------------------------------

_RATE_LIMIT_MAX = 5  # max attempts per window
_RATE_LIMIT_WINDOW = 60  # seconds
_rate_store: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(ip: str) -> bool:
    """Check if an IP has exceeded the rate limit. Prunes expired entries."""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW

    # Prune old entries
    _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]

    if len(_rate_store[ip]) >= _RATE_LIMIT_MAX:
        return True

    _rate_store[ip].append(now)
    return False


RATE_LIMITED_PATHS = frozenset({"/login", "/reset-password"})

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    """Add security headers to every response."""
    response = await handler(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# ---------------------------------------------------------------------------
# Auth + CSRF + rate limiting middleware
# ---------------------------------------------------------------------------


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Check session, enforce CSRF on POST, rate-limit auth endpoints."""
    path = request.path
    method = request.method
    ip = request.remote or "unknown"

    # --- Rate limiting on auth POST endpoints ---
    if method == "POST" and path in RATE_LIMITED_PATHS:
        if _is_rate_limited(ip):
            logger.warning(f"SECURITY: Rate limit exceeded for {ip} on {path}")
            raise web.HTTPTooManyRequests(text="Too many attempts. Try again in 60 seconds.")

    # --- Allow public paths through ---
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await handler(request)

    # --- Check session cookie ---
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise web.HTTPFound("/login")

    # Validate session against database
    pool = request.app["db_pool"]
    row = await pool.fetchrow(dq.GET_SESSION, session_id)

    if not row:
        # Expired or invalid session — clear cookie and redirect
        response = web.HTTPFound("/login")
        response.del_cookie("session_id")
        raise response

    # Attach user info to request for use in views
    request["user"] = {
        "id": str(row["user_id"]),
        "email": row["email"],
        "display_name": row["display_name"],
    }

    # --- CSRF validation on authenticated POST requests ---
    if method == "POST" and path not in CSRF_EXEMPT_PATHS:
        # Check CSRF token from form data or HTMX header
        try:
            data = await request.post()
        except Exception:
            data = {}
        csrf_token = data.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected_token = request.cookies.get("csrf_token", "")

        if not csrf_token or not expected_token or csrf_token != expected_token:
            logger.warning(
                f"SECURITY: CSRF validation failed for {ip} on {path} "
                f"(user={request['user']['email']})"
            )
            raise web.HTTPForbidden(text="CSRF validation failed. Please refresh and try again.")

    return await handler(request)


# ---------------------------------------------------------------------------
# CSRF token helper
# ---------------------------------------------------------------------------


def generate_csrf_token(response: web.Response) -> str:
    """Generate a CSRF token and set it as a cookie on the response."""
    token = secrets.token_hex(32)
    response.set_cookie(
        "csrf_token",
        token,
        max_age=14400,  # 4 hours (matches session)
        httponly=False,  # JS needs to read this for HTMX headers
        secure=True,
        samesite="Strict",
    )
    return token
