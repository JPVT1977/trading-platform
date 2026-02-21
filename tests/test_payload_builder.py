"""Tests for the payload builder that prepares data for Claude."""

from bot.config import Settings, TradingMode
from bot.layer1_data.payload_builder import build_analysis_payload


def test_payload_structure(sample_indicator_set, settings):
    """Payload has the expected top-level keys."""
    payload = build_analysis_payload(sample_indicator_set, settings)

    assert payload["symbol"] == "BTC/USDT"
    assert payload["timeframe"] == "4h"
    assert "timestamp" in payload
    assert payload["candle_count"] == settings.payload_lookback
    assert "price" in payload
    assert "indicators" in payload
    assert "volume" in payload


def test_payload_lookback_length(sample_indicator_set, settings):
    """All arrays in payload should be payload_lookback length."""
    payload = build_analysis_payload(sample_indicator_set, settings)
    n = settings.payload_lookback

    assert len(payload["price"]["close"]) == n
    assert len(payload["price"]["high"]) == n
    assert len(payload["price"]["low"]) == n
    assert len(payload["indicators"]["rsi"]) == n
    assert len(payload["indicators"]["macd"]["line"]) == n
    assert len(payload["indicators"]["obv"]) == n
    assert len(payload["volume"]) == n


def test_payload_no_nan_values(sample_indicator_set, settings):
    """Payload should not contain NaN — they should be None."""
    payload = build_analysis_payload(sample_indicator_set, settings)

    def check_no_nan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_no_nan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, float):
                    assert v == v, f"NaN found at {path}[{i}]"  # NaN != NaN
        elif isinstance(obj, float):
            assert obj == obj, f"NaN found at {path}"

    check_no_nan(payload)


def test_payload_values_are_rounded(sample_indicator_set, settings):
    """Float values should be rounded to 6 decimal places."""
    payload = build_analysis_payload(sample_indicator_set, settings)

    for v in payload["price"]["close"]:
        if isinstance(v, float):
            str_val = f"{v:.10f}"
            # After 6 decimal places, remaining should be zeros
            decimal_part = str_val.split(".")[1]
            assert len(decimal_part.rstrip("0")) <= 6
