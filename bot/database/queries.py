"""Named SQL query constants. No inline SQL scattered through business logic."""

# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

UPSERT_CANDLES = """
    INSERT INTO candles (time, symbol, timeframe, open, high, low, close, volume)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (time, symbol, timeframe) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume
"""

SELECT_CANDLES = """
    SELECT time, symbol, timeframe, open, high, low, close, volume
    FROM candles
    WHERE symbol = $1 AND timeframe = $2
    ORDER BY time DESC
    LIMIT $3
"""

# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

INSERT_SIGNAL = """
    INSERT INTO signals (
        symbol, timeframe, divergence_type, indicator,
        confidence, direction, entry_price, stop_loss,
        take_profit_1, take_profit_2, take_profit_3,
        reasoning, raw_payload, validated, validation_reason, created_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, NOW())
    RETURNING id
"""

SELECT_RECENT_SIGNALS = """
    SELECT * FROM signals
    WHERE symbol = $1
    ORDER BY created_at DESC
    LIMIT $2
"""

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

INSERT_ORDER = """
    INSERT INTO orders (
        signal_id, exchange_order_id, symbol, direction,
        state, entry_price, stop_loss, take_profit_1,
        take_profit_2, take_profit_3, quantity, created_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
    RETURNING id
"""

UPDATE_ORDER_STATE = """
    UPDATE orders
    SET state = $2, updated_at = NOW()
    WHERE id = $1
"""

UPDATE_ORDER_FILL = """
    UPDATE orders
    SET state = $2, filled_quantity = $3, filled_price = $4, updated_at = NOW()
    WHERE id = $1
"""

UPDATE_ORDER_CLOSE = """
    UPDATE orders
    SET state = 'closed', pnl = $2, fees = $3, filled_price = $4,
        closed_at = NOW(), updated_at = NOW()
    WHERE id = $1
"""

SELECT_CUMULATIVE_PNL = """
    SELECT COALESCE(SUM(pnl), 0) as total_pnl
    FROM orders
    WHERE state = 'closed'
"""

SELECT_OPEN_ORDERS = """
    SELECT * FROM orders
    WHERE state NOT IN ('closed', 'cancelled', 'rejected', 'error')
    ORDER BY created_at DESC
"""

SELECT_ORDERS_BY_SYMBOL = """
    SELECT * FROM orders
    WHERE symbol = $1 AND state NOT IN ('closed', 'cancelled', 'rejected', 'error')
    ORDER BY created_at DESC
"""

COUNT_OPEN_ORDERS = """
    SELECT COUNT(*) FROM orders
    WHERE state NOT IN ('closed', 'cancelled', 'rejected', 'error')
"""

# ---------------------------------------------------------------------------
# Portfolio Snapshots
# ---------------------------------------------------------------------------

INSERT_PORTFOLIO_SNAPSHOT = """
    INSERT INTO portfolio_snapshots (
        time, total_equity, available_balance,
        open_position_count, daily_pnl, daily_trades
    )
    VALUES (NOW(), $1, $2, $3, $4, $5)
"""

SELECT_DAILY_PNL = """
    SELECT COALESCE(SUM(pnl), 0) as daily_pnl,
           COUNT(*) as daily_trades
    FROM orders
    WHERE state = 'closed'
      AND closed_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')
"""

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

INSERT_CIRCUIT_BREAKER_EVENT = """
    INSERT INTO circuit_breaker_events (reason, details, triggered_at)
    VALUES ($1, $2, NOW())
    RETURNING id
"""

SELECT_ACTIVE_CIRCUIT_BREAKER = """
    SELECT * FROM circuit_breaker_events
    WHERE resolved_at IS NULL
    ORDER BY triggered_at DESC
    LIMIT 1
"""

RESOLVE_CIRCUIT_BREAKER = """
    UPDATE circuit_breaker_events
    SET resolved_at = NOW()
    WHERE id = $1
"""

# ---------------------------------------------------------------------------
# Analysis Cycles
# ---------------------------------------------------------------------------

INSERT_ANALYSIS_CYCLE = """
    INSERT INTO analysis_cycles (
        started_at, completed_at, symbols_analyzed,
        signals_found, signals_validated, orders_placed,
        errors, duration_ms
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id
"""
