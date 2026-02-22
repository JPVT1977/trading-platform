"""Login / Logout views."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import aiohttp_jinja2
import bcrypt
from aiohttp import web

from bot.dashboard import queries as dq


class AuthViews:
    """Authentication views: login form, login POST, logout."""

    def __init__(self, db_pool) -> None:
        self._pool = db_pool

    @aiohttp_jinja2.template("login.html")
    async def login_page(self, request: web.Request) -> dict:
        """GET /login — render login form."""
        # If already logged in, redirect to dashboard
        session_id = request.cookies.get("session_id")
        if session_id:
            row = await self._pool.fetchrow(dq.GET_SESSION, session_id)
            if row:
                raise web.HTTPFound("/dashboard")

        return {"error": None}

    async def login_post(self, request: web.Request) -> web.Response:
        """POST /login — verify credentials, create session."""
        data = await request.post()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))

        if not email or not password:
            return aiohttp_jinja2.render_template(
                "login.html", request, {"error": "Email and password required"}
            )

        # Look up user
        user = await self._pool.fetchrow(dq.GET_USER_BY_EMAIL, email)
        if not user:
            return aiohttp_jinja2.render_template(
                "login.html", request, {"error": "Invalid email or password"}
            )

        # Verify password
        if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            return aiohttp_jinja2.render_template(
                "login.html", request, {"error": "Invalid email or password"}
            )

        # Create session
        session_id = secrets.token_hex(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        ip_address = request.remote or "unknown"

        await self._pool.execute(
            dq.CREATE_SESSION,
            session_id, user["id"], expires_at, ip_address,
        )
        await self._pool.execute(dq.UPDATE_LAST_LOGIN, user["id"])

        # Set cookie and redirect
        response = web.HTTPFound("/dashboard")
        response.set_cookie(
            "session_id",
            session_id,
            max_age=86400,  # 24 hours
            httponly=True,
            secure=True,
            samesite="Strict",
        )
        raise response

    async def logout(self, request: web.Request) -> web.Response:
        """POST /logout — delete session and redirect to login."""
        session_id = request.cookies.get("session_id")
        if session_id:
            await self._pool.execute(dq.DELETE_SESSION, session_id)

        response = web.HTTPFound("/login")
        response.del_cookie("session_id")
        raise response
