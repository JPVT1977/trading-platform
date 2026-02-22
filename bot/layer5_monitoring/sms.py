"""ClickSend SMS client — same provider + pattern as Guardian Assist.

Direct HTTP to ClickSend REST API v3 via aiohttp. No SDK dependency.
"""

from __future__ import annotations

import base64
import re

import aiohttp
from loguru import logger

from bot.config import Settings
from bot.models import DivergenceSignal, TradeOrder

CLICKSEND_SMS_URL = "https://rest.clicksend.com/v3/sms/send"


def _normalise_au_number(number: str) -> str:
    """Convert Australian mobile (0XXXXXXXXX) to +61XXXXXXXXX."""
    number = re.sub(r"[\s\-()]", "", number)
    if number.startswith("0") and len(number) == 10:
        return f"+61{number[1:]}"
    if not number.startswith("+"):
        return f"+{number}"
    return number


class SMSClient:
    """Async ClickSend SMS alerts — drop-in replacement for TelegramClient."""

    def __init__(self, settings: Settings) -> None:
        self._username = settings.clicksend_username
        self._api_key = settings.clicksend_api_key
        self._from_name = settings.clicksend_from_name
        self._to_numbers = [_normalise_au_number(n) for n in settings.sms_to_numbers]
        self._enabled = bool(self._username and self._api_key and self._to_numbers)
        self._session: aiohttp.ClientSession | None = None

        if not self._enabled:
            logger.info("SMS alerts disabled (missing ClickSend credentials or phone numbers)")

    def _auth_header(self) -> str:
        """Basic auth header for ClickSend API."""
        creds = f"{self._username}:{self._api_key}"
        encoded = base64.b64encode(creds.encode()).decode()
        return f"Basic {encoded}"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str) -> None:
        """Send an SMS to all configured numbers."""
        if not self._enabled:
            return

        # SMS has 160 char limit per segment; truncate gracefully
        if len(message) > 640:
            message = message[:637] + "..."

        session = await self._ensure_session()
        messages = [
            {
                "source": "sdk",
                "body": message,
                "to": number,
                "from": self._from_name,
            }
            for number in self._to_numbers
        ]

        try:
            async with session.post(
                CLICKSEND_SMS_URL,
                json={"messages": messages},
                headers={
                    "Authorization": self._auth_header(),
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.warning(f"ClickSend SMS failed ({resp.status}): {body}")
                else:
                    logger.debug(f"SMS sent to {len(self._to_numbers)} recipients")
        except Exception as e:
            logger.warning(f"SMS send error: {e}")

    async def send_signal_alert(self, signal: DivergenceSignal) -> None:
        """Send a formatted signal detection alert via SMS."""
        direction = signal.direction.value if signal.direction else "N/A"
        div_type = signal.divergence_type.value if signal.divergence_type else "N/A"
        msg = (
            f"SIGNAL: {signal.symbol} {signal.timeframe}\n"
            f"{div_type} | {direction.upper()}\n"
            f"Conf: {signal.confidence:.0%} | {signal.indicator or 'N/A'}\n"
            f"Entry: {signal.entry_price} SL: {signal.stop_loss}\n"
            f"TP1: {signal.take_profit_1}"
        )
        await self.send(msg)

    async def send_order_alert(self, order: TradeOrder) -> None:
        """Send a formatted order alert via SMS — different for open vs close."""
        direction = order.direction.value.upper() if hasattr(order.direction, "value") else str(order.direction).upper()
        state = order.state.value if hasattr(order.state, "value") else str(order.state)

        if state == "closed" and order.pnl is not None:
            pnl_prefix = "+" if order.pnl >= 0 else ""
            result = "WIN" if order.pnl >= 0 else "LOSS"
            fees_str = f"{order.fees:.2f}" if order.fees else "0.00"
            msg = (
                f"TRADE CLOSED: {order.symbol} {direction}\n"
                f"P&L: {pnl_prefix}{order.pnl:.2f} (fees: {fees_str})\n"
                f"Entry: {order.entry_price} Exit: {order.filled_price}\n"
                f"{result}"
            )
        else:
            msg = (
                f"TRADE OPENED: {order.symbol} {direction}\n"
                f"Entry: {order.entry_price} Qty: {order.quantity:.6f}\n"
                f"SL: {order.stop_loss} TP1: {order.take_profit_1}"
            )
        await self.send(msg)

    async def send_circuit_breaker_alert(self, reason: str) -> None:
        """Send a critical circuit breaker alert via SMS."""
        msg = f"CIRCUIT BREAKER TRIPPED\n{reason}\nAll trading halted."
        await self.send(msg)

    async def send_error_alert(self, error: str, context: str = "") -> None:
        """Send an error notification via SMS."""
        msg = f"ERROR: {context}\n{error}"[:640]
        await self.send(msg)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
