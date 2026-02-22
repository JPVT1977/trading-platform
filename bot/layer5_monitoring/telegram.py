from __future__ import annotations

import aiohttp
from loguru import logger

from bot.config import Settings
from bot.models import DivergenceSignal, TradeOrder


class TelegramClient:
    """Async Telegram alerts via direct HTTP — no heavy SDK dependency."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._enabled = bool(self._token and self._chat_id)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str, parse_mode: str = "HTML") -> None:
        """Send a message to the configured Telegram chat."""
        if not self._enabled:
            return

        try:
            session = await self._ensure_session()
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            async with session.post(url, json={
                "chat_id": self._chat_id,
                "text": message,
                "parse_mode": parse_mode,
            }) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Telegram send failed ({resp.status}): {body}")
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")

    async def send_signal_alert(self, signal: DivergenceSignal) -> None:
        """Send a formatted signal detection alert."""
        emoji = "\u2B06\uFE0F" if signal.direction and signal.direction.value == "long" else "\u2B07\uFE0F"
        msg = (
            f"<b>{emoji} Signal Detected</b>\n\n"
            f"<b>Symbol:</b> {signal.symbol}\n"
            f"<b>Timeframe:</b> {signal.timeframe}\n"
            f"<b>Type:</b> {signal.divergence_type.value if signal.divergence_type else 'N/A'}\n"
            f"<b>Indicator:</b> {signal.indicator or 'N/A'}\n"
            f"<b>Direction:</b> {signal.direction.value if signal.direction else 'N/A'}\n"
            f"<b>Confidence:</b> {signal.confidence:.0%}\n"
            f"<b>Entry:</b> {signal.entry_price}\n"
            f"<b>Stop Loss:</b> {signal.stop_loss}\n"
            f"<b>TP1:</b> {signal.take_profit_1}\n"
            f"<b>TP2:</b> {signal.take_profit_2 or 'N/A'}\n\n"
            f"<i>{signal.reasoning}</i>"
        )
        await self.send(msg)

    async def send_order_alert(self, order: TradeOrder) -> None:
        """Send a formatted order alert — different for open vs close."""
        state = order.state.value if hasattr(order.state, "value") else str(order.state)
        direction = order.direction.value if hasattr(order.direction, "value") else str(order.direction)

        if state == "closed" and order.pnl is not None:
            pnl_prefix = "+" if order.pnl >= 0 else ""
            result = "\u2705 WIN" if order.pnl >= 0 else "\u274C LOSS"
            msg = (
                f"<b>Position Closed</b> {result}\n\n"
                f"<b>Symbol:</b> {order.symbol}\n"
                f"<b>Direction:</b> {direction}\n"
                f"<b>Entry:</b> {order.entry_price}\n"
                f"<b>Exit:</b> {order.filled_price or 'N/A'}\n"
                f"<b>P&L:</b> {pnl_prefix}{order.pnl:.2f}\n"
                f"<b>Fees:</b> {order.fees:.2f}" if order.fees else ""
            )
        else:
            msg = (
                f"<b>Trade Opened</b>\n\n"
                f"<b>Symbol:</b> {order.symbol}\n"
                f"<b>Direction:</b> {direction}\n"
                f"<b>Entry:</b> {order.entry_price}\n"
                f"<b>Quantity:</b> {order.quantity}\n"
                f"<b>Stop Loss:</b> {order.stop_loss}\n"
                f"<b>TP1:</b> {order.take_profit_1}"
            )
        await self.send(msg)

    async def send_circuit_breaker_alert(self, reason: str) -> None:
        """Send a critical circuit breaker alert."""
        msg = (
            f"<b>\u26A0\uFE0F CIRCUIT BREAKER TRIPPED</b>\n\n"
            f"<b>Reason:</b> {reason}\n\n"
            f"All trading has been halted. Manual intervention required."
        )
        await self.send(msg)

    async def send_error_alert(self, error: str, context: str = "") -> None:
        """Send an error notification."""
        msg = f"<b>\u274C Error</b>\n\n{context}\n<code>{error}</code>"
        await self.send(msg)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
