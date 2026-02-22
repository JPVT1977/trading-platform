"""IG Markets session manager — handles CST/X-SECURITY-TOKEN lifecycle.

IG REST API requires a POST /session login to obtain CST and
X-SECURITY-TOKEN headers. Tokens expire after ~6 hours. This module
handles login, automatic re-authentication on 401, and proactive
refresh before expiry.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from bot.config import Settings

_DEMO_URL = "https://demo-api.ig.com/gateway/deal"
_LIVE_URL = "https://api.ig.com/gateway/deal"

# Proactive refresh 30 minutes before the 6-hour expiry
_TOKEN_LIFETIME_S = 6 * 3600
_REFRESH_BEFORE_S = 30 * 60


class IGSession:
    """Manages authenticated sessions with the IG REST API."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.ig_api_key
        self._username = settings.ig_username
        self._password = settings.ig_password
        self._account_id = settings.ig_account_id
        self._base_url = _DEMO_URL if settings.ig_demo else _LIVE_URL
        self._cst: str = ""
        self._security_token: str = ""
        self._token_obtained_at: float = 0.0
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def _login(self) -> None:
        """POST /session to obtain CST + X-SECURITY-TOKEN."""
        session = await self._ensure_session()
        headers = {
            "X-IG-API-KEY": self._api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "2",
        }
        payload = {
            "identifier": self._username,
            "password": self._password,
        }

        async with session.post(
            f"{self._base_url}/session",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"IG login failed ({resp.status}): {body}")

            self._cst = resp.headers.get("CST", "")
            self._security_token = resp.headers.get("X-SECURITY-TOKEN", "")
            self._token_obtained_at = time.monotonic()

            data = await resp.json()
            logger.info(
                f"IG session established (account: {data.get('currentAccountId', 'unknown')})"
            )

    def _token_expired(self) -> bool:
        if not self._cst:
            return True
        age = time.monotonic() - self._token_obtained_at
        return age >= (_TOKEN_LIFETIME_S - _REFRESH_BEFORE_S)

    async def _ensure_authenticated(self) -> None:
        """Log in if tokens are missing or about to expire."""
        if self._token_expired():
            async with self._lock:
                # Double-check after acquiring lock
                if self._token_expired():
                    await self._login()

    def _auth_headers(self, version: str = "1") -> dict[str, str]:
        return {
            "X-IG-API-KEY": self._api_key,
            "CST": self._cst,
            "X-SECURITY-TOKEN": self._security_token,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": version,
        }

    async def request(
        self,
        method: str,
        path: str,
        version: str = "1",
        **kwargs: Any,
    ) -> dict:
        """Make an authenticated request to the IG API.

        Automatically re-authenticates on 401 responses.
        """
        await self._ensure_authenticated()
        session = await self._ensure_session()
        url = f"{self._base_url}{path}"

        resp = await session.request(
            method, url, headers=self._auth_headers(version), **kwargs
        )

        # Re-authenticate once on 401
        if resp.status == 401:
            logger.warning("IG 401 — re-authenticating")
            await self._login()
            resp = await session.request(
                method, url, headers=self._auth_headers(version), **kwargs
            )

        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"IG API error ({resp.status} {method} {path}): {body}")

        # Some endpoints return empty body (e.g. DELETE)
        text = await resp.text()
        if not text:
            return {}
        return await resp.json(content_type=None)

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
