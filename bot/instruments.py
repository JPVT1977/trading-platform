"""Symbol metadata registry — routes symbols to brokers and provides instrument info.

Every symbol flows through here to determine which broker handles it and
what pip/lot characteristics it has.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BrokerType(StrEnum):
    BINANCE = "binance"
    OANDA = "oanda"
    IG = "ig"


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    FOREX = "forex"
    INDEX = "index"
    COMMODITY = "commodity"
    BOND = "bond"
    STOCK = "stock"


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    broker: BrokerType
    display_name: str
    asset_class: AssetClass
    pip_size: float
    pip_value_per_unit: float  # USD per pip per 1 unit
    min_units: float
    max_leverage: float
    fee_rate: float  # 0 for OANDA (spread-based)
    base_currency: str
    quote_currency: str


# --- OANDA instruments (forex, indices, commodities, bonds) ---

OANDA_INSTRUMENTS: dict[str, InstrumentInfo] = {
    # Forex — Major pairs
    "EUR_USD": InstrumentInfo(
        symbol="EUR_USD", broker=BrokerType.OANDA, display_name="EUR/USD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="USD",
    ),
    "GBP_USD": InstrumentInfo(
        symbol="GBP_USD", broker=BrokerType.OANDA, display_name="GBP/USD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="GBP", quote_currency="USD",
    ),
    "AUD_USD": InstrumentInfo(
        symbol="AUD_USD", broker=BrokerType.OANDA, display_name="AUD/USD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="USD",
    ),
    "USD_JPY": InstrumentInfo(
        symbol="USD_JPY", broker=BrokerType.OANDA, display_name="USD/JPY",
        asset_class=AssetClass.FOREX,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="USD", quote_currency="JPY",
    ),
    "EUR_GBP": InstrumentInfo(
        symbol="EUR_GBP", broker=BrokerType.OANDA, display_name="EUR/GBP",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="GBP",
    ),
    "AUD_NZD": InstrumentInfo(
        symbol="AUD_NZD", broker=BrokerType.OANDA, display_name="AUD/NZD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="NZD",
    ),
    # Forex — JPY crosses
    "GBP_JPY": InstrumentInfo(
        symbol="GBP_JPY", broker=BrokerType.OANDA, display_name="GBP/JPY",
        asset_class=AssetClass.FOREX,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="GBP", quote_currency="JPY",
    ),
    "EUR_JPY": InstrumentInfo(
        symbol="EUR_JPY", broker=BrokerType.OANDA, display_name="EUR/JPY",
        asset_class=AssetClass.FOREX,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="JPY",
    ),
    "AUD_JPY": InstrumentInfo(
        symbol="AUD_JPY", broker=BrokerType.OANDA, display_name="AUD/JPY",
        asset_class=AssetClass.FOREX,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="JPY",
    ),
    # Forex — Other majors/crosses
    "NZD_USD": InstrumentInfo(
        symbol="NZD_USD", broker=BrokerType.OANDA, display_name="NZD/USD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="NZD", quote_currency="USD",
    ),
    "USD_CAD": InstrumentInfo(
        symbol="USD_CAD", broker=BrokerType.OANDA, display_name="USD/CAD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="USD", quote_currency="CAD",
    ),
    "EUR_AUD": InstrumentInfo(
        symbol="EUR_AUD", broker=BrokerType.OANDA, display_name="EUR/AUD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="AUD",
    ),
    "GBP_AUD": InstrumentInfo(
        symbol="GBP_AUD", broker=BrokerType.OANDA, display_name="GBP/AUD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="GBP", quote_currency="AUD",
    ),
    "USD_CHF": InstrumentInfo(
        symbol="USD_CHF", broker=BrokerType.OANDA, display_name="USD/CHF",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="USD", quote_currency="CHF",
    ),
    "EUR_NZD": InstrumentInfo(
        symbol="EUR_NZD", broker=BrokerType.OANDA, display_name="EUR/NZD",
        asset_class=AssetClass.FOREX,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=30.0, fee_rate=0.0,
        base_currency="EUR", quote_currency="NZD",
    ),
    # Commodities — Metals
    "XAU_USD": InstrumentInfo(
        symbol="XAU_USD", broker=BrokerType.OANDA, display_name="Gold",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="XAU", quote_currency="USD",
    ),
    "XAG_USD": InstrumentInfo(
        symbol="XAG_USD", broker=BrokerType.OANDA, display_name="Silver",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.001, pip_value_per_unit=0.001, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="XAG", quote_currency="USD",
    ),
    # Commodities — Energy
    "WTICO_USD": InstrumentInfo(
        symbol="WTICO_USD", broker=BrokerType.OANDA, display_name="WTI Crude Oil",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="OIL", quote_currency="USD",
    ),
    "BCO_USD": InstrumentInfo(
        symbol="BCO_USD", broker=BrokerType.OANDA, display_name="Brent Crude Oil",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="OIL", quote_currency="USD",
    ),
    "NATGAS_USD": InstrumentInfo(
        symbol="NATGAS_USD", broker=BrokerType.OANDA, display_name="Natural Gas",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.001, pip_value_per_unit=0.001, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="GAS", quote_currency="USD",
    ),
    "XCU_USD": InstrumentInfo(
        symbol="XCU_USD", broker=BrokerType.OANDA, display_name="Copper",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.0001, pip_value_per_unit=0.0001, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="XCU", quote_currency="USD",
    ),
    # Indices — US
    "SPX500_USD": InstrumentInfo(
        symbol="SPX500_USD", broker=BrokerType.OANDA, display_name="S&P 500",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="SPX", quote_currency="USD",
    ),
    "NAS100_USD": InstrumentInfo(
        symbol="NAS100_USD", broker=BrokerType.OANDA, display_name="NASDAQ 100",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="NAS", quote_currency="USD",
    ),
    "US30_USD": InstrumentInfo(
        symbol="US30_USD", broker=BrokerType.OANDA, display_name="Dow Jones 30",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="DJI", quote_currency="USD",
    ),
    "US2000_USD": InstrumentInfo(
        symbol="US2000_USD", broker=BrokerType.OANDA, display_name="Russell 2000",
        asset_class=AssetClass.INDEX,
        pip_size=0.1, pip_value_per_unit=0.1, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="RUT", quote_currency="USD",
    ),
    # Indices — International
    "AU200_AUD": InstrumentInfo(
        symbol="AU200_AUD", broker=BrokerType.OANDA, display_name="ASX 200",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="ASX", quote_currency="AUD",
    ),
    "DE30_EUR": InstrumentInfo(
        symbol="DE30_EUR", broker=BrokerType.OANDA, display_name="DAX 30",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="DAX", quote_currency="EUR",
    ),
    "UK100_GBP": InstrumentInfo(
        symbol="UK100_GBP", broker=BrokerType.OANDA, display_name="FTSE 100",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="UKX", quote_currency="GBP",
    ),
    "JP225_USD": InstrumentInfo(
        symbol="JP225_USD", broker=BrokerType.OANDA, display_name="Nikkei 225",
        asset_class=AssetClass.INDEX,
        pip_size=1.0, pip_value_per_unit=1.0, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="NKY", quote_currency="USD",
    ),
    # Bonds
    "USB10Y_USD": InstrumentInfo(
        symbol="USB10Y_USD", broker=BrokerType.OANDA, display_name="US 10Y Treasury",
        asset_class=AssetClass.BOND,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=10.0, fee_rate=0.0,
        base_currency="USB", quote_currency="USD",
    ),
    "USB02Y_USD": InstrumentInfo(
        symbol="USB02Y_USD", broker=BrokerType.OANDA, display_name="US 2Y Treasury",
        asset_class=AssetClass.BOND,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=10.0, fee_rate=0.0,
        base_currency="USB", quote_currency="USD",
    ),
}

# Backward-compatible alias
FOREX_INSTRUMENTS = OANDA_INSTRUMENTS


# --- IG Markets instruments (indices, commodities, share CFDs) ---

IG_INSTRUMENTS: dict[str, InstrumentInfo] = {
    "IX.D.SPTRD.IFE.IP": InstrumentInfo(
        symbol="IX.D.SPTRD.IFE.IP", broker=BrokerType.IG, display_name="S&P 500",
        asset_class=AssetClass.INDEX,
        pip_size=0.1, pip_value_per_unit=0.1, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="USD", quote_currency="USD",
    ),
    "IX.D.NASDAQ.IFE.IP": InstrumentInfo(
        symbol="IX.D.NASDAQ.IFE.IP", broker=BrokerType.IG, display_name="NASDAQ 100",
        asset_class=AssetClass.INDEX,
        pip_size=0.1, pip_value_per_unit=0.1, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="USD", quote_currency="USD",
    ),
    "IX.D.ASX.IFE.IP": InstrumentInfo(
        symbol="IX.D.ASX.IFE.IP", broker=BrokerType.IG, display_name="ASX 200",
        asset_class=AssetClass.INDEX,
        pip_size=0.1, pip_value_per_unit=0.1, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="AUD", quote_currency="AUD",
    ),
    "CS.D.USCGC.TODAY.IP": InstrumentInfo(
        symbol="CS.D.USCGC.TODAY.IP", broker=BrokerType.IG, display_name="Gold",
        asset_class=AssetClass.COMMODITY,
        pip_size=0.1, pip_value_per_unit=0.1, min_units=1,
        max_leverage=20.0, fee_rate=0.0,
        base_currency="XAU", quote_currency="USD",
    ),
    # Share CFDs — US Mega-caps
    "UA.D.NVDA.DAILY.IP": InstrumentInfo(
        symbol="UA.D.NVDA.DAILY.IP", broker=BrokerType.IG, display_name="NVIDIA",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="NVDA", quote_currency="USD",
    ),
    "UA.D.AAPL.DAILY.IP": InstrumentInfo(
        symbol="UA.D.AAPL.DAILY.IP", broker=BrokerType.IG, display_name="Apple",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="AAPL", quote_currency="USD",
    ),
    "UA.D.MSFT.DAILY.IP": InstrumentInfo(
        symbol="UA.D.MSFT.DAILY.IP", broker=BrokerType.IG, display_name="Microsoft",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="MSFT", quote_currency="USD",
    ),
    "UA.D.AMZN.DAILY.IP": InstrumentInfo(
        symbol="UA.D.AMZN.DAILY.IP", broker=BrokerType.IG, display_name="Amazon",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="AMZN", quote_currency="USD",
    ),
    "UA.D.TSLA.DAILY.IP": InstrumentInfo(
        symbol="UA.D.TSLA.DAILY.IP", broker=BrokerType.IG, display_name="Tesla",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="TSLA", quote_currency="USD",
    ),
    "UA.D.META.DAILY.IP": InstrumentInfo(
        symbol="UA.D.META.DAILY.IP", broker=BrokerType.IG, display_name="Meta",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="META", quote_currency="USD",
    ),
    "UA.D.GOOGL.DAILY.IP": InstrumentInfo(
        symbol="UA.D.GOOGL.DAILY.IP", broker=BrokerType.IG, display_name="Alphabet",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="GOOGL", quote_currency="USD",
    ),
    "UA.D.AVGO.DAILY.IP": InstrumentInfo(
        symbol="UA.D.AVGO.DAILY.IP", broker=BrokerType.IG, display_name="Broadcom",
        asset_class=AssetClass.STOCK,
        pip_size=0.01, pip_value_per_unit=0.01, min_units=1,
        max_leverage=5.0, fee_rate=0.0,
        base_currency="AVGO", quote_currency="USD",
    ),
}


def is_ig(symbol: str) -> bool:
    """Check if a symbol is an IG Markets instrument."""
    return symbol in IG_INSTRUMENTS


def is_oanda(symbol: str) -> bool:
    """Check if a symbol is an OANDA instrument."""
    return symbol in OANDA_INSTRUMENTS


# Backward-compatible alias
is_forex = is_oanda


def get_asset_class(symbol: str) -> AssetClass:
    """Return the asset class for a symbol."""
    if symbol in OANDA_INSTRUMENTS:
        return OANDA_INSTRUMENTS[symbol].asset_class
    if symbol in IG_INSTRUMENTS:
        return IG_INSTRUMENTS[symbol].asset_class
    return AssetClass.CRYPTO


def route_symbol(symbol: str) -> BrokerType:
    """Determine which broker handles a given symbol."""
    if symbol in IG_INSTRUMENTS:
        return BrokerType.IG
    if symbol in OANDA_INSTRUMENTS:
        return BrokerType.OANDA
    return BrokerType.BINANCE


def get_instrument(symbol: str) -> InstrumentInfo:
    """Return instrument metadata. Auto-generates for crypto symbols."""
    if symbol in IG_INSTRUMENTS:
        return IG_INSTRUMENTS[symbol]
    if symbol in OANDA_INSTRUMENTS:
        return OANDA_INSTRUMENTS[symbol]

    # Auto-generate for crypto (e.g. "BTC/USDT")
    parts = symbol.split("/")
    base = parts[0] if parts else symbol
    quote = parts[1] if len(parts) > 1 else "USDT"

    return InstrumentInfo(
        symbol=symbol,
        broker=BrokerType.BINANCE,
        display_name=symbol,
        asset_class=AssetClass.CRYPTO,
        pip_size=0.01,  # Not used for crypto sizing
        pip_value_per_unit=0.01,
        min_units=0.0,
        max_leverage=1.0,
        fee_rate=0.001,  # 0.1% per side
        base_currency=base,
        quote_currency=quote,
    )
