"""Login / Logout views."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

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
        expires_at = datetime.now(UTC) + timedelta(hours=24)
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

    # ------------------------------------------------------------------
    # Reset password (unauthenticated — gated by DASHBOARD_RESET_CODE)
    # ------------------------------------------------------------------

    @aiohttp_jinja2.template("reset_password.html")
    async def reset_password_page(self, request: web.Request) -> dict:
        """GET /reset-password — render reset-password form."""
        reset_code = os.environ.get("DASHBOARD_RESET_CODE", "")
        if not reset_code:
            return {
                "error": "Password reset is not configured. "
                "Set DASHBOARD_RESET_CODE in Fly secrets.",
                "disabled": True,
            }
        return {"error": None, "success": None, "disabled": False}

    async def reset_password_post(self, request: web.Request) -> web.Response:
        """POST /reset-password — verify reset code and set new password."""
        data = await request.post()
        email = str(data.get("email", "")).strip().lower()
        reset_code = str(data.get("reset_code", ""))
        new_password = str(data.get("new_password", ""))
        confirm_password = str(data.get("confirm_password", ""))

        ctx: dict = {"disabled": False}

        expected_code = os.environ.get("DASHBOARD_RESET_CODE", "")
        if not expected_code:
            ctx["error"] = "Password reset is not configured."
            ctx["disabled"] = True
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        if not email or not reset_code or not new_password or not confirm_password:
            ctx["error"] = "All fields are required"
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        if not secrets.compare_digest(reset_code, expected_code):
            ctx["error"] = "Invalid reset code"
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        if new_password != confirm_password:
            ctx["error"] = "Passwords do not match"
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        if len(new_password) < 8:
            ctx["error"] = "Password must be at least 8 characters"
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        user = await self._pool.fetchrow(dq.GET_USER_BY_EMAIL, email)
        if not user:
            ctx["error"] = "No account found with that email"
            return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
        await self._pool.execute(dq.UPDATE_USER_PASSWORD, new_hash, user["id"])

        ctx["success"] = "Password reset successfully. You can now log in."
        return aiohttp_jinja2.render_template("reset_password.html", request, ctx)

    # ------------------------------------------------------------------
    # Change password
    # ------------------------------------------------------------------

    @aiohttp_jinja2.template("change_password.html")
    async def change_password_page(self, request: web.Request) -> dict:
        """GET /dashboard/change-password — render change-password form."""
        return {"active_page": "change_password", "user": request["user"]}

    async def change_password_post(self, request: web.Request) -> web.Response:
        """POST /dashboard/change-password — validate and update password."""
        user = request["user"]
        data = await request.post()
        current_password = str(data.get("current_password", ""))
        new_password = str(data.get("new_password", ""))
        confirm_password = str(data.get("confirm_password", ""))

        ctx = {"active_page": "change_password", "user": user}

        if not current_password or not new_password or not confirm_password:
            ctx["error"] = "All fields are required"
            return aiohttp_jinja2.render_template("change_password.html", request, ctx)

        if new_password != confirm_password:
            ctx["error"] = "New passwords do not match"
            return aiohttp_jinja2.render_template("change_password.html", request, ctx)

        if len(new_password) < 8:
            ctx["error"] = "New password must be at least 8 characters"
            return aiohttp_jinja2.render_template("change_password.html", request, ctx)

        # Fetch current hash from DB
        db_user = await self._pool.fetchrow(dq.GET_USER_BY_EMAIL, user["email"])
        if not db_user:
            ctx["error"] = "User not found"
            return aiohttp_jinja2.render_template("change_password.html", request, ctx)

        if not bcrypt.checkpw(
            current_password.encode("utf-8"),
            db_user["password_hash"].encode("utf-8"),
        ):
            ctx["error"] = "Current password is incorrect"
            return aiohttp_jinja2.render_template("change_password.html", request, ctx)

        # Hash and save
        new_hash = bcrypt.hashpw(
            new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
        await self._pool.execute(dq.UPDATE_USER_PASSWORD, new_hash, db_user["id"])

        ctx["success"] = "Password changed successfully"
        return aiohttp_jinja2.render_template("change_password.html", request, ctx)
