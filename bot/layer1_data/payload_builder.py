from __future__ import annotations

from bot.config import Settings
from bot.models import IndicatorSet


def _trim(arr: list, n: int) -> list:
    """Take last n values and round floats to 6 decimals. Replace None as-is."""
    tail = arr[-n:]
    return [round(v, 6) if isinstance(v, float) else v for v in tail]


def build_analysis_payload(
    indicators: IndicatorSet, settings: Settings, candle_status: str = "closed"
) -> dict:
    """Build a compact JSON payload for Claude analysis.

    Only sends the last `payload_lookback` values to minimise token usage.
    NaN values have already been converted to None by the indicator pipeline.

    candle_status: "closed" if the latest candle just completed, "forming" if
    the most recent candle is still open and its OHLC may change.
    """
    n = settings.payload_lookback

    return {
        "symbol": indicators.symbol,
        "timeframe": indicators.timeframe,
        "timestamp": indicators.timestamp.isoformat(),
        "candle_count": n,
        "candle_status": candle_status,
        "price": {
            "close": _trim(indicators.closes, n),
            "high": _trim(indicators.highs, n),
            "low": _trim(indicators.lows, n),
        },
        "indicators": {
            "rsi": _trim(indicators.rsi, n),
            "macd": {
                "line": _trim(indicators.macd_line, n),
                "signal": _trim(indicators.macd_signal, n),
                "histogram": _trim(indicators.macd_histogram, n),
            },
            "obv": _trim(indicators.obv, n),
            "mfi": _trim(indicators.mfi, n),
            "stochastic": {
                "k": _trim(indicators.stoch_k, n),
                "d": _trim(indicators.stoch_d, n),
            },
            "cci": _trim(indicators.cci, n),
            "williams_r": _trim(indicators.williams_r, n),
            "atr": _trim(indicators.atr, n),
            "adx": _trim(indicators.adx, n),
            "ema": {
                "short": _trim(indicators.ema_short, n),
                "medium": _trim(indicators.ema_medium, n),
                "long": _trim(indicators.ema_long, n),
            },
        },
        "volume": _trim(indicators.volumes, n),
    }
