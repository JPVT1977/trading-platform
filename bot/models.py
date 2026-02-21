from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DivergenceType(str, Enum):
    BULLISH_REGULAR = "bullish_regular"
    BEARISH_REGULAR = "bearish_regular"
    BULLISH_HIDDEN = "bullish_hidden"
    BEARISH_HIDDEN = "bearish_hidden"


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderState(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    CLOSED = "closed"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorSet(BaseModel):
    """All computed indicators for a symbol/timeframe pair.

    TA-Lib returns None/NaN for the first N periods (warmup), so all
    indicator arrays must accept Optional values.
    """
    symbol: str
    timeframe: str
    timestamp: datetime
    rsi: list[Optional[float]]
    macd_line: list[Optional[float]]
    macd_signal: list[Optional[float]]
    macd_histogram: list[Optional[float]]
    obv: list[Optional[float]]
    mfi: list[Optional[float]]
    stoch_k: list[Optional[float]]
    stoch_d: list[Optional[float]]
    atr: list[Optional[float]]
    ema_short: list[Optional[float]]
    ema_medium: list[Optional[float]]
    ema_long: list[Optional[float]]
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]


# ---------------------------------------------------------------------------
# Intelligence Layer
# ---------------------------------------------------------------------------

class DivergenceSignal(BaseModel):
    """Output from Claude tool_use — structured divergence detection."""
    divergence_detected: bool
    divergence_type: Optional[DivergenceType] = None
    indicator: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    direction: Optional[SignalDirection] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    reasoning: str = ""
    symbol: str = ""
    timeframe: str = ""


class ValidationResult(BaseModel):
    """Result of deterministic signal validation."""
    passed: bool
    reason: str


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class TradeOrder(BaseModel):
    """Represents an order through its full lifecycle."""
    id: Optional[str] = None
    signal_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    symbol: str
    direction: SignalDirection
    state: OrderState = OrderState.PENDING
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    quantity: float = 0.0
    filled_quantity: float = 0.0
    filled_price: Optional[float] = None
    pnl: Optional[float] = None
    fees: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------

class RiskCheckResult(BaseModel):
    """Result of a risk management check."""
    approved: bool
    reason: str


class PortfolioState(BaseModel):
    """Current portfolio snapshot for risk calculations."""
    total_equity: float
    available_balance: float
    open_positions: list[TradeOrder] = Field(default_factory=list)
    daily_pnl: float = 0.0
    daily_trades: int = 0


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

class AnalysisCycleResult(BaseModel):
    """Summary of one analysis cycle."""
    started_at: datetime
    completed_at: Optional[datetime] = None
    symbols_analyzed: list[str] = Field(default_factory=list)
    signals_found: int = 0
    signals_validated: int = 0
    orders_placed: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_ms: Optional[int] = None
