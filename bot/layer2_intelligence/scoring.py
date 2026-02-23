"""Deterministic weighted scoring engine for divergence signals.

Six dimensions, max score 10.0. Signals below min_divergence_score are rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bot.config import Settings
from bot.instruments import AssetClass, get_asset_class
from bot.models import DivergenceSignal, IndicatorSet, ScoredSignal, SignalDirection


def _last_valid(arr: list[float | None]) -> float | None:
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def _score_indicator_confluence(signal: DivergenceSignal) -> float:
    """0-3 points based on number of confirming indicators."""
    count = len(signal.confirming_indicators)
    if count >= 5:
        return 3.0
    if count == 4:
        return 2.0
    if count == 3:
        return 1.5
    if count == 2:
        return 1.0
    return 0.0


def _score_swing_length(signal: DivergenceSignal) -> float:
    """0-2 points based on swing length quality."""
    bars = signal.swing_length_bars
    if bars is None:
        return 0.0

    if signal.timeframe == "4h":
        ideal_low, ideal_high = 15, 25
    else:
        ideal_low, ideal_high = 10, 20

    if ideal_low <= bars <= ideal_high:
        return 2.0

    # Penalty for being outside ideal range
    if bars < ideal_low:
        ratio = bars / ideal_low
        return max(0.0, 2.0 * ratio)
    # bars > ideal_high
    ratio = ideal_high / bars
    return max(0.0, 2.0 * ratio)


def _score_volume(indicators: IndicatorSet) -> float:
    """0-2 points based on current volume vs average."""
    if not indicators.volumes or not indicators.volume_sma:
        return 0.5

    current_vol = indicators.volumes[-1]
    avg_vol = _last_valid(indicators.volume_sma)
    if not avg_vol or avg_vol <= 0:
        return 0.5

    ratio = current_vol / avg_vol
    if ratio >= 1.5:
        return 2.0
    if ratio >= 1.2:
        return 1.5
    if ratio >= 0.8:
        return 1.0
    return 0.5


def _score_ema_alignment(signal: DivergenceSignal, indicators: IndicatorSet) -> float:
    """0-2 points. Divergence against trend = 2.0 (ideal reversal), with trend = 0.5."""
    ema_s = _last_valid(indicators.ema_short)
    ema_m = _last_valid(indicators.ema_medium)
    ema_l = _last_valid(indicators.ema_long)

    if ema_s is None or ema_m is None or ema_l is None:
        return 1.0

    if signal.direction == SignalDirection.LONG:
        # Bullish divergence against downtrend = ideal reversal
        if ema_s < ema_m < ema_l:
            return 2.0  # Against trend (reversal setup)
        if ema_s > ema_m > ema_l:
            return 0.5  # With trend (less valuable)
    elif signal.direction == SignalDirection.SHORT:
        # Bearish divergence against uptrend = ideal reversal
        if ema_s > ema_m > ema_l:
            return 2.0
        if ema_s < ema_m < ema_l:
            return 0.5

    return 1.0  # Mixed/ranging


def _score_adx(indicators: IndicatorSet) -> float:
    """0-1 point based on ADX trend strength."""
    adx = _last_valid(indicators.adx)
    if adx is None:
        return 0.5

    if adx >= 30:
        return 1.0
    if adx >= 25:
        return 0.75
    if adx >= 20:
        return 0.5
    return 0.25


def _score_session(signal: DivergenceSignal) -> float:
    """-0.5 to +0.5 based on trading session timing."""
    asset_class = get_asset_class(signal.symbol)

    # Crypto trades 24/7 — no session weighting
    if asset_class == AssetClass.CRYPTO:
        return 0.0

    now_utc = datetime.now(UTC)
    hour = now_utc.hour

    if asset_class == AssetClass.FOREX:
        # London/NY overlap (13:00-16:00 UTC) = best
        if 13 <= hour < 16:
            return 0.5
        # London session (07:00-16:00 UTC)
        if 7 <= hour < 16:
            return 0.25
        # NY session (13:00-21:00 UTC)
        if 13 <= hour < 21:
            return 0.25
        # Tokyo (00:00-09:00 UTC) or Sydney (22:00-07:00 UTC) — off-peak
        return -0.5

    if asset_class == AssetClass.INDEX:
        # Index: primary session hours (roughly US market hours in UTC)
        if 14 <= hour < 21:
            return 0.5
        if 7 <= hour < 16:
            return 0.25
        return -0.5

    return 0.0


def compute_score(
    signal: DivergenceSignal,
    indicators: IndicatorSet,
    settings: Settings,
) -> ScoredSignal:
    """Compute a deterministic quality score (1-10) for a divergence signal."""
    confluence = _score_indicator_confluence(signal)
    swing = _score_swing_length(signal)
    volume = _score_volume(indicators)
    ema = _score_ema_alignment(signal, indicators)
    adx = _score_adx(indicators)
    session = _score_session(signal)

    raw_score = confluence + swing + volume + ema + adx + session
    score = max(1.0, min(10.0, raw_score))

    breakdown = {
        "indicator_confluence": confluence,
        "swing_length": swing,
        "volume_confirmation": volume,
        "ema_alignment": ema,
        "adx_strength": adx,
        "session_weighting": session,
    }

    return ScoredSignal(signal=signal, score=score, breakdown=breakdown)
