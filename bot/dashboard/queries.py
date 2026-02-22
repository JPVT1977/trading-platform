"""Dashboard-specific SQL queries. Separate from bot/database/queries.py."""

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

GET_USER_BY_EMAIL = """
    SELECT id, email, password_hash, display_name
    FROM users WHERE email = $1
"""

CREATE_SESSION = """
    INSERT INTO sessions (id, user_id, created_at, expires_at, ip_address)
    VALUES ($1, $2, NOW(), $3, $4)
"""

GET_SESSION = """
    SELECT s.id, s.user_id, s.expires_at, u.email, u.display_name
    FROM sessions s JOIN users u ON s.user_id = u.id
    WHERE s.id = $1 AND s.expires_at > NOW()
"""

DELETE_SESSION = """
    DELETE FROM sessions WHERE id = $1
"""

CLEANUP_EXPIRED_SESSIONS = """
    DELETE FROM sessions WHERE expires_at < NOW()
"""

UPDATE_LAST_LOGIN = """
    UPDATE users SET last_login_at = NOW() WHERE id = $1
"""

UPSERT_USER = """
    INSERT INTO users (email, password_hash, display_name)
    VALUES ($1, $2, $3)
    ON CONFLICT (email) DO UPDATE SET
        password_hash = EXCLUDED.password_hash,
        display_name = EXCLUDED.display_name
"""

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

GET_OVERVIEW_STATS = """
    SELECT
        (SELECT COUNT(*) FROM orders
         WHERE state NOT IN ('closed', 'cancelled', 'rejected', 'error')) AS open_positions,
        (SELECT COALESCE(SUM(pnl), 0) FROM orders
         WHERE state = 'closed'
           AND closed_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')) AS daily_pnl,
        (SELECT COUNT(*) FROM orders
         WHERE state = 'closed'
           AND closed_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')) AS daily_trades
"""

GET_RECENT_SIGNALS = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, validated, validation_reason,
           reasoning, created_at
    FROM signals
    ORDER BY created_at DESC
    LIMIT $1
"""

GET_RECENT_CYCLES = """
    SELECT id, started_at, completed_at, symbols_analyzed,
           signals_found, signals_validated, orders_placed,
           errors, duration_ms
    FROM analysis_cycles
    ORDER BY started_at DESC
    LIMIT $1
"""

GET_LATEST_EQUITY = """
    SELECT total_equity, available_balance, daily_pnl
    FROM portfolio_snapshots
    ORDER BY time DESC
    LIMIT 1
"""

# ---------------------------------------------------------------------------
# Signals (paginated)
# ---------------------------------------------------------------------------

GET_SIGNALS_ALL = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, take_profit_1, validated,
           validation_reason, reasoning, created_at
    FROM signals
    ORDER BY created_at DESC
    LIMIT $1 OFFSET $2
"""

GET_SIGNALS_VALIDATED = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, take_profit_1, validated,
           validation_reason, reasoning, created_at
    FROM signals
    WHERE validated = TRUE
    ORDER BY created_at DESC
    LIMIT $1 OFFSET $2
"""

GET_SIGNALS_REJECTED = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, take_profit_1, validated,
           validation_reason, reasoning, created_at
    FROM signals
    WHERE validated = FALSE
    ORDER BY created_at DESC
    LIMIT $1 OFFSET $2
"""

COUNT_SIGNALS_ALL = "SELECT COUNT(*) FROM signals"
COUNT_SIGNALS_VALIDATED = "SELECT COUNT(*) FROM signals WHERE validated = TRUE"
COUNT_SIGNALS_REJECTED = "SELECT COUNT(*) FROM signals WHERE validated = FALSE"

# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

GET_OPEN_POSITIONS = """
    SELECT o.id, o.symbol, o.direction, o.entry_price, o.stop_loss,
           o.take_profit_1, o.take_profit_2, o.quantity, o.state,
           o.created_at, o.filled_price,
           s.divergence_type, s.indicator, s.confidence, s.reasoning
    FROM orders o
    LEFT JOIN signals s ON o.signal_id = s.id
    WHERE o.state NOT IN ('closed', 'cancelled', 'rejected', 'error')
    ORDER BY o.created_at DESC
"""

GET_CLOSED_POSITIONS = """
    SELECT o.id, o.symbol, o.direction, o.entry_price, o.filled_price,
           o.stop_loss, o.take_profit_1, o.quantity, o.pnl, o.fees,
           o.state, o.created_at, o.closed_at,
           s.divergence_type, s.indicator, s.confidence, s.reasoning
    FROM orders o
    LEFT JOIN signals s ON o.signal_id = s.id
    WHERE o.state IN ('closed', 'cancelled', 'rejected', 'error')
    ORDER BY o.closed_at DESC NULLS LAST
    LIMIT $1 OFFSET $2
"""

COUNT_CLOSED_POSITIONS = """
    SELECT COUNT(*) FROM orders
    WHERE state IN ('closed', 'cancelled', 'rejected', 'error')
"""

# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

GET_CIRCUIT_BREAKER_EVENTS = """
    SELECT id, reason, details, triggered_at, resolved_at
    FROM circuit_breaker_events
    ORDER BY triggered_at DESC
    LIMIT 20
"""

GET_ACTIVE_CIRCUIT_BREAKER = """
    SELECT * FROM circuit_breaker_events
    WHERE resolved_at IS NULL
    ORDER BY triggered_at DESC
    LIMIT 1
"""

# ---------------------------------------------------------------------------
# Equity Curve
# ---------------------------------------------------------------------------

GET_EQUITY_CURVE = """
    SELECT time, total_equity, available_balance, daily_pnl
    FROM portfolio_snapshots
    WHERE time >= NOW() - $1::interval
    ORDER BY time ASC
"""

GET_EQUITY_CURVE_ALL = """
    SELECT time, total_equity, available_balance, daily_pnl
    FROM portfolio_snapshots
    ORDER BY time ASC
"""
