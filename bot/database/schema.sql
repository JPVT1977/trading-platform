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

-- ===================================================================
-- Dashboard: Users and Sessions
-- ===================================================================

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,  -- 32-byte hex token
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires
    ON sessions (expires_at);

-- ===================================================================
-- Dashboard: Performance indexes
-- ===================================================================

CREATE INDEX IF NOT EXISTS idx_signals_created_at
    ON signals (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_closed
    ON orders (closed_at DESC) WHERE state = 'closed';

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_time
    ON portfolio_snapshots (time DESC);

CREATE INDEX IF NOT EXISTS idx_circuit_breaker_time
    ON circuit_breaker_events (triggered_at DESC);

-- ===================================================================
-- Signal Performance Outcomes
-- ===================================================================

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id           UUID NOT NULL UNIQUE REFERENCES signals(id),
    entry_price         DOUBLE PRECISION NOT NULL,
    direction           TEXT NOT NULL,
    -- Checkpoint prices (NULL until time elapsed)
    price_1h            DOUBLE PRECISION,
    price_4h            DOUBLE PRECISION,
    price_12h           DOUBLE PRECISION,
    price_24h           DOUBLE PRECISION,
    -- Returns (positive = signal was correct)
    return_1h           DOUBLE PRECISION,
    return_4h           DOUBLE PRECISION,
    return_12h          DOUBLE PRECISION,
    return_24h          DOUBLE PRECISION,
    -- Max excursions
    max_favorable_price DOUBLE PRECISION,
    max_adverse_price   DOUBLE PRECISION,
    max_favorable_pct   DOUBLE PRECISION,
    max_adverse_pct     DOUBLE PRECISION,
    -- TP/SL hit tracking
    tp1_hit             BOOLEAN DEFAULT FALSE,
    tp1_hit_at          TIMESTAMPTZ,
    tp2_hit             BOOLEAN DEFAULT FALSE,
    tp2_hit_at          TIMESTAMPTZ,
    tp3_hit             BOOLEAN DEFAULT FALSE,
    tp3_hit_at          TIMESTAMPTZ,
    sl_hit              BOOLEAN DEFAULT FALSE,
    sl_hit_at           TIMESTAMPTZ,
    -- Verdict
    verdict             TEXT,  -- correct / incorrect / partial / pending
    last_checked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fully_resolved      BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_unresolved
    ON signal_outcomes (fully_resolved, last_checked_at) WHERE fully_resolved = FALSE;

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_verdict
    ON signal_outcomes (verdict);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_created
    ON signal_outcomes (created_at DESC);

-- ===================================================================
-- Multi-Broker: Add broker column to existing tables (idempotent)
-- ===================================================================

DO $$ BEGIN
    ALTER TABLE signals ADD COLUMN broker TEXT NOT NULL DEFAULT 'binance';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE orders ADD COLUMN broker TEXT NOT NULL DEFAULT 'binance';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE portfolio_snapshots ADD COLUMN broker TEXT NOT NULL DEFAULT 'binance';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE candles ADD COLUMN broker TEXT NOT NULL DEFAULT 'binance';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_signals_broker
    ON signals (broker, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_broker
    ON orders (broker, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_broker_open
    ON orders (broker, state) WHERE state NOT IN ('closed', 'cancelled', 'rejected');

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_broker
    ON portfolio_snapshots (broker, time DESC);

-- Fix: portfolio_snapshots PK must include broker for multi-broker support
DO $$ BEGIN
    -- Drop old PK on (time) and create composite PK on (time, broker)
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'portfolio_snapshots'::regclass
          AND contype = 'p'
          AND array_length(conkey, 1) = 1
    ) THEN
        ALTER TABLE portfolio_snapshots DROP CONSTRAINT portfolio_snapshots_pkey;
        ALTER TABLE portfolio_snapshots ADD PRIMARY KEY (time, broker);
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not migrate portfolio_snapshots PK: %', SQLERRM;
END $$;

-- ===================================================================
-- Breakeven + Profit-Lock trailing stop columns (idempotent)
-- ===================================================================

DO $$ BEGIN
    ALTER TABLE orders ADD COLUMN original_stop_loss DOUBLE PRECISION;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE orders ADD COLUMN sl_trail_stage INTEGER DEFAULT 0;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
