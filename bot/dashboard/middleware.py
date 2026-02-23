"""Auth middleware — checks session cookie on every dashboard request."""

from __future__ import annotations

from aiohttp import web

from bot.dashboard import queries as dq

# Paths that don't require authentication
PUBLIC_PATHS = frozenset({
    "/login",
    "/reset-password",
    "/health",
    "/health/deep",
})

PUBLIC_PREFIXES = (
    "/static/",
    "/public/",
)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Check session_id cookie. Redirect to /login if missing or expired."""
    path = request.path

    # Allow public paths through
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await handler(request)

    # Check session cookie
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

    return await handler(request)
