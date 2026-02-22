"""Symbol metadata registry — routes symbols to brokers and provides instrument info.

Every symbol flows through here to determine which broker handles it and
what pip/lot characteristics it has.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrokerType(str, Enum):
    BINANCE = "binance"
    OANDA = "oanda"


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    broker: BrokerType
    display_name: str
    pip_size: float
    pip_value_per_unit: float  # USD per pip per 1 unit
    min_units: float
    max_leverage: float
    fee_rate: float  # 0 for OANDA (spread-based)
    base_currency: str
    quote_currency: str


# --- Forex instruments (OANDA canonical format: underscore-separated) ---

FOREX_INSTRUMENTS: dict[str, InstrumentInfo] = {
    "EUR_USD": InstrumentInfo(
        symbol="EUR_USD", broker=BrokerType.OANDA, display_name="EUR/USD",
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="USD",
    ),
    "GBP_USD": InstrumentInfo(
        symbol="GBP_USD", broker=BrokerType.OANDA, display_name="GBP/USD",
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="GBP", quote_currency="USD",
    ),
    "AUD_USD": InstrumentInfo(
        symbol="AUD_USD", broker=BrokerType.OANDA, display_name="AUD/USD",
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="USD",
    ),
    "USD_JPY": InstrumentInfo(
        symbol="USD_JPY", broker=BrokerType.OANDA, display_name="USD/JPY",
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="USD", quote_currency="JPY",
    ),
    "EUR_GBP": InstrumentInfo(
        symbol="EUR_GBP", broker=BrokerType.OANDA, display_name="EUR/GBP",
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="GBP",
    ),
    "AUD_NZD": InstrumentInfo(
        symbol="AUD_NZD", broker=BrokerType.OANDA, display_name="AUD/NZD",
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="NZD",
    ),
}


def is_forex(symbol: str) -> bool:
    """Check if a symbol is a forex pair (handled by OANDA)."""
    return symbol in FOREX_INSTRUMENTS


def route_symbol(symbol: str) -> BrokerType:
    """Determine which broker handles a given symbol."""
    if symbol in FOREX_INSTRUMENTS:
        return BrokerType.OANDA
    return BrokerType.BINANCE


def get_instrument(symbol: str) -> InstrumentInfo:
    """Return instrument metadata. Auto-generates for crypto symbols."""
    if symbol in FOREX_INSTRUMENTS:
        return FOREX_INSTRUMENTS[symbol]

    # Auto-generate for crypto (e.g. "BTC/USDT")
    parts = symbol.split("/")
    base = parts[0] if parts else symbol
    quote = parts[1] if len(parts) > 1 else "USDT"

    return InstrumentInfo(
        symbol=symbol,
        broker=BrokerType.BINANCE,
        display_name=symbol,
        pip_size=0.01,  # Not used for crypto sizing
        pip_value_per_unit=0.01,
        min_units=0.0,
        max_leverage=1.0,
        fee_rate=0.001,  # 0.1% per side
        base_currency=base,
        quote_currency=quote,
    )
