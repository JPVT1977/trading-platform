-- Divergence Trading Bot — Database Schema
-- Compatible with standard PostgreSQL. TimescaleDB hypertables are optional.

-- Attempt to enable TimescaleDB (no-op if extension not available)
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'TimescaleDB not available — using standard PostgreSQL tables';
END
$$;

-- OHLCV candle data (high volume, time-series)
CREATE TABLE IF NOT EXISTS candles (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (time, symbol, timeframe)
);

-- Convert to hypertable if TimescaleDB is available
DO $$
BEGIN
    PERFORM create_hypertable('candles', 'time', if_not_exists => TRUE);
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipping hypertable for candles';
END
$$;

CREATE INDEX IF NOT EXISTS idx_candles_symbol_time
    ON candles (symbol, timeframe, time DESC);

-- Signals detected by Claude
CREATE TABLE IF NOT EXISTS signals (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol            TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    divergence_type   TEXT NOT NULL,
    indicator         TEXT,
    confidence        DOUBLE PRECISION NOT NULL,
    direction         TEXT,
    entry_price       DOUBLE PRECISION,
    stop_loss         DOUBLE PRECISION,
    take_profit_1     DOUBLE PRECISION,
    take_profit_2     DOUBLE PRECISION,
    take_profit_3     DOUBLE PRECISION,
    reasoning         TEXT,
    raw_payload       JSONB,
    validated         BOOLEAN DEFAULT FALSE,
    validation_reason TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON signals (symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_validated
    ON signals (validated, created_at DESC);

-- Orders and their lifecycle
CREATE TABLE IF NOT EXISTS orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id           UUID REFERENCES signals(id),
    exchange_order_id   TEXT,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'pending',
    entry_price         DOUBLE PRECISION NOT NULL,
    stop_loss           DOUBLE PRECISION NOT NULL,
    take_profit_1       DOUBLE PRECISION NOT NULL,
    take_profit_2       DOUBLE PRECISION,
    take_profit_3       DOUBLE PRECISION,
    quantity            DOUBLE PRECISION NOT NULL DEFAULT 0,
    filled_quantity     DOUBLE PRECISION DEFAULT 0,
    filled_price        DOUBLE PRECISION,
    pnl                 DOUBLE PRECISION,
    fees                DOUBLE PRECISION DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_open
    ON orders (state) WHERE state NOT IN ('closed', 'cancelled', 'rejected');

CREATE INDEX IF NOT EXISTS idx_orders_symbol
    ON orders (symbol, created_at DESC);

-- Portfolio snapshots (periodic equity curve tracking)
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    time                TIMESTAMPTZ NOT NULL,
    total_equity        DOUBLE PRECISION NOT NULL,
    available_balance   DOUBLE PRECISION NOT NULL,
    open_position_count INTEGER NOT NULL,
    daily_pnl           DOUBLE PRECISION NOT NULL,
    daily_trades        INTEGER NOT NULL,
    PRIMARY KEY (time)
);

DO $$
BEGIN
    PERFORM create_hypertable('portfolio_snapshots', 'time', if_not_exists => TRUE);
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Skipping hypertable for portfolio_snapshots';
END
$$;

-- Circuit breaker events (audit trail)
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reason          TEXT NOT NULL,
    details         JSONB,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

-- Analysis cycle log (debugging and performance tracking)
CREATE TABLE IF NOT EXISTS analysis_cycles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    symbols_analyzed    TEXT[] NOT NULL DEFAULT '{}',
    signals_found       INTEGER DEFAULT 0,
    signals_validated   INTEGER DEFAULT 0,
    orders_placed       INTEGER DEFAULT 0,
    errors              JSONB,
    duration_ms         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_analysis_cycles_time
    ON analysis_cycles (started_at DESC);
