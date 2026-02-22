"""Settings page — read-only display of all current configuration."""

from __future__ import annotations

import aiohttp_jinja2
from aiohttp import web


class SettingsViews:

    def __init__(self, settings) -> None:
        self._settings = settings

    @aiohttp_jinja2.template("settings.html")
    async def settings_page(self, request: web.Request) -> dict:
        """GET /dashboard/settings — configuration display."""
        s = self._settings
        return {
            "active_page": "settings",
            "user": request["user"],
            "groups": {
                "Trading": {
                    "Mode": s.trading_mode.value,
                    "Symbols": ", ".join(s.symbols),
                    "Timeframes": ", ".join(s.timeframes),
                    "Analysis Interval": f"{s.analysis_interval_minutes} min",
                },
                "Exchange": {
                    "Exchange": s.exchange_id,
                    "Sandbox": str(s.exchange_sandbox),
                    "API Key": "***" + s.exchange_api_key[-4:] if len(s.exchange_api_key) > 4 else "(not set)",
                },
                "Risk Management": {
                    "Max Position %": f"{s.max_position_pct}%",
                    "Max Daily Loss %": f"{s.max_daily_loss_pct}%",
                    "Max Open Positions": str(s.max_open_positions),
                    "Max Correlation Exposure": str(s.max_correlation_exposure),
                    "Min Risk/Reward": f"{s.min_risk_reward}x",
                    "Min Confidence": f"{s.min_confidence:.0%}",
                },
                "Indicators": {
                    "RSI Period": str(s.rsi_period),
                    "MACD Fast/Slow/Signal": f"{s.macd_fast}/{s.macd_slow}/{s.macd_signal}",
                    "Stochastic K/D/Slowing": f"{s.stoch_k_period}/{s.stoch_d_period}/{s.stoch_slowing}",
                    "MFI Period": str(s.mfi_period),
                    "ATR Period": str(s.atr_period),
                    "EMA Short/Medium/Long": f"{s.ema_short}/{s.ema_medium}/{s.ema_long}",
                    "Lookback Candles": str(s.lookback_candles),
                },
                "Claude AI": {
                    "Model": s.claude_model,
                    "Max Tokens": str(s.claude_max_tokens),
                    "API Key": "***" + s.anthropic_api_key[-4:] if len(s.anthropic_api_key) > 4 else "(not set)",
                },
                "Alerts": {
                    "SMS Enabled": str(bool(s.clicksend_username and s.clicksend_api_key)),
                    "SMS From": s.clicksend_from_name,
                    "SMS Recipients": str(len(s.sms_to_numbers)),
                    "Telegram Enabled": str(bool(s.telegram_bot_token)),
                },
            },
        }
