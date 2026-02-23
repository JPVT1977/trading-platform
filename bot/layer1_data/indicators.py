from __future__ import annotations

import numpy as np
import talib

from bot.config import Settings
from bot.models import Candle, IndicatorSet


def _nan_to_none(arr: np.ndarray) -> list[float | None]:
    """Convert numpy array to list, replacing NaN with None."""
    return [None if np.isnan(v) else float(v) for v in arr]


def compute_indicators(
    candles: list[Candle],
    symbol: str,
    timeframe: str,
    settings: Settings,
) -> IndicatorSet:
    """Compute all technical indicators from OHLCV candles using TA-Lib.

    All indicators are computed server-side. Claude never computes numbers.
    """
    opens = np.array([c.open for c in candles], dtype=np.float64)
    closes = np.array([c.close for c in candles], dtype=np.float64)
    highs = np.array([c.high for c in candles], dtype=np.float64)
    lows = np.array([c.low for c in candles], dtype=np.float64)
    volumes = np.array([c.volume for c in candles], dtype=np.float64)

    # RSI
    rsi = talib.RSI(closes, timeperiod=settings.rsi_period)

    # MACD
    macd_line, macd_signal, macd_histogram = talib.MACD(
        closes,
        fastperiod=settings.macd_fast,
        slowperiod=settings.macd_slow,
        signalperiod=settings.macd_signal,
    )

    # On-Balance Volume
    obv = talib.OBV(closes, volumes)

    # Money Flow Index
    mfi = talib.MFI(highs, lows, closes, volumes, timeperiod=settings.mfi_period)

    # Stochastic
    stoch_k, stoch_d = talib.STOCH(
        highs, lows, closes,
        fastk_period=settings.stoch_k_period,
        slowk_period=settings.stoch_d_period,
        slowk_matype=0,
        slowd_period=settings.stoch_slowing,
        slowd_matype=0,
    )

    # Commodity Channel Index
    cci = talib.CCI(highs, lows, closes, timeperiod=settings.cci_period)

    # Williams %R
    williams_r = talib.WILLR(highs, lows, closes, timeperiod=settings.williams_r_period)

    # Average True Range
    atr = talib.ATR(highs, lows, closes, timeperiod=settings.atr_period)

    # Average Directional Index (trend strength)
    adx = talib.ADX(highs, lows, closes, timeperiod=settings.atr_period)

    # Exponential Moving Averages
    ema_short = talib.EMA(closes, timeperiod=settings.ema_short)
    ema_medium = talib.EMA(closes, timeperiod=settings.ema_medium)
    ema_long = talib.EMA(closes, timeperiod=settings.ema_long)

    # Volume SMA
    volume_sma = talib.SMA(volumes, timeperiod=settings.volume_sma_period)

    # Candlestick patterns (100=bullish, -100=bearish, 0=none)
    ohlc = (opens, highs, lows, closes)
    candle_patterns = {
        "hammer": talib.CDLHAMMER(*ohlc).astype(int).tolist(),
        "engulfing": talib.CDLENGULFING(*ohlc).astype(int).tolist(),
        "morning_star": talib.CDLMORNINGSTAR(
            *ohlc, penetration=0.3,
        ).astype(int).tolist(),
        "piercing": talib.CDLPIERCING(*ohlc).astype(int).tolist(),
        "inverted_hammer": talib.CDLINVERTEDHAMMER(*ohlc).astype(int).tolist(),
        "shooting_star": talib.CDLSHOOTINGSTAR(*ohlc).astype(int).tolist(),
        "evening_star": talib.CDLEVENINGSTAR(
            *ohlc, penetration=0.3,
        ).astype(int).tolist(),
        "dark_cloud": talib.CDLDARKCLOUDCOVER(
            *ohlc, penetration=0.5,
        ).astype(int).tolist(),
        "hanging_man": talib.CDLHANGINGMAN(*ohlc).astype(int).tolist(),
    }

    return IndicatorSet(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=candles[-1].timestamp,
        rsi=_nan_to_none(rsi),
        macd_line=_nan_to_none(macd_line),
        macd_signal=_nan_to_none(macd_signal),
        macd_histogram=_nan_to_none(macd_histogram),
        obv=_nan_to_none(obv),
        mfi=_nan_to_none(mfi),
        stoch_k=_nan_to_none(stoch_k),
        stoch_d=_nan_to_none(stoch_d),
        cci=_nan_to_none(cci),
        williams_r=_nan_to_none(williams_r),
        atr=_nan_to_none(atr),
        adx=_nan_to_none(adx),
        ema_short=_nan_to_none(ema_short),
        ema_medium=_nan_to_none(ema_medium),
        ema_long=_nan_to_none(ema_long),
        closes=closes.tolist(),
        highs=highs.tolist(),
        lows=lows.tolist(),
        volumes=volumes.tolist(),
        volume_sma=_nan_to_none(volume_sma),
        candle_patterns=candle_patterns,
    )
