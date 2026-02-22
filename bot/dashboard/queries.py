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
           reasoning, broker, created_at
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
           validation_reason, reasoning, broker, created_at
    FROM signals
    ORDER BY created_at DESC
    LIMIT $1 OFFSET $2
"""

GET_SIGNALS_VALIDATED = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, take_profit_1, validated,
           validation_reason, reasoning, broker, created_at
    FROM signals
    WHERE validated = TRUE
    ORDER BY created_at DESC
    LIMIT $1 OFFSET $2
"""

GET_SIGNALS_REJECTED = """
    SELECT id, symbol, timeframe, divergence_type, indicator, confidence,
           direction, entry_price, stop_loss, take_profit_1, validated,
           validation_reason, reasoning, broker, created_at
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
           o.created_at, o.filled_price, o.broker,
           s.divergence_type, s.indicator, s.confidence, s.reasoning
    FROM orders o
    LEFT JOIN signals s ON o.signal_id = s.id
    WHERE o.state NOT IN ('closed', 'cancelled', 'rejected', 'error')
    ORDER BY o.created_at DESC
"""

GET_CLOSED_POSITIONS = """
    SELECT o.id, o.symbol, o.direction, o.entry_price, o.filled_price,
           o.stop_loss, o.take_profit_1, o.quantity, o.pnl, o.fees,
           o.state, o.created_at, o.closed_at, o.broker,
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

# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

GET_LAST_CYCLE = """
    SELECT started_at, completed_at, symbols_analyzed, signals_found, duration_ms
    FROM analysis_cycles
    ORDER BY started_at DESC
    LIMIT 1
"""

# ---------------------------------------------------------------------------
# Performance (Signal Outcomes)
# ---------------------------------------------------------------------------

GET_PERFORMANCE_HERO = """
    SELECT
        COUNT(*) FILTER (WHERE verdict = 'correct')   AS correct,
        COUNT(*) FILTER (WHERE verdict = 'incorrect')  AS incorrect,
        COUNT(*) FILTER (WHERE verdict = 'partial')    AS partial,
        COUNT(*) FILTER (WHERE verdict = 'pending')    AS pending,
        COUNT(*) FILTER (WHERE verdict IS NOT NULL)    AS total,
        COUNT(*) FILTER (WHERE tp1_hit)                AS tp1_hits,
        COUNT(*) FILTER (WHERE fully_resolved)         AS resolved
    FROM signal_outcomes
"""

GET_PERFORMANCE_RETURNS = """
    SELECT
        AVG(return_1h)  FILTER (WHERE return_1h IS NOT NULL)   AS avg_return_1h,
        AVG(return_4h)  FILTER (WHERE return_4h IS NOT NULL)   AS avg_return_4h,
        AVG(return_12h) FILTER (WHERE return_12h IS NOT NULL)  AS avg_return_12h,
        AVG(return_24h) FILTER (WHERE return_24h IS NOT NULL)  AS avg_return_24h,
        AVG(max_favorable_pct) FILTER (WHERE max_favorable_pct IS NOT NULL) AS avg_mfe,
        AVG(max_adverse_pct)   FILTER (WHERE max_adverse_pct IS NOT NULL)   AS avg_mae
    FROM signal_outcomes
"""

GET_PERFORMANCE_DAILY_ACCURACY = """
    SELECT
        DATE(s.created_at) AS day,
        COUNT(*) FILTER (WHERE so.verdict = 'correct') AS correct,
        COUNT(*) FILTER (WHERE so.verdict IN ('correct','incorrect','partial')) AS resolved
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE so.fully_resolved = TRUE
    GROUP BY DATE(s.created_at)
    ORDER BY day ASC
"""

GET_PERFORMANCE_BY_SYMBOL = """
    SELECT
        s.symbol,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE so.verdict = 'correct')  AS correct,
        COUNT(*) FILTER (WHERE so.verdict = 'incorrect') AS incorrect,
        AVG(so.return_24h) FILTER (WHERE so.return_24h IS NOT NULL) AS avg_return
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE so.fully_resolved = TRUE
    GROUP BY s.symbol
    ORDER BY total DESC
"""

GET_PERFORMANCE_BY_TIMEFRAME = """
    SELECT
        s.timeframe,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE so.verdict = 'correct')  AS correct,
        COUNT(*) FILTER (WHERE so.verdict = 'incorrect') AS incorrect,
        AVG(so.return_24h) FILTER (WHERE so.return_24h IS NOT NULL) AS avg_return
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE so.fully_resolved = TRUE
    GROUP BY s.timeframe
    ORDER BY total DESC
"""

GET_PERFORMANCE_BY_DIVERGENCE = """
    SELECT
        s.divergence_type,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE so.verdict = 'correct')  AS correct,
        COUNT(*) FILTER (WHERE so.verdict = 'incorrect') AS incorrect,
        AVG(so.return_24h) FILTER (WHERE so.return_24h IS NOT NULL) AS avg_return
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE so.fully_resolved = TRUE
    GROUP BY s.divergence_type
    ORDER BY total DESC
"""

GET_PERFORMANCE_VALIDATED_VS_REJECTED = """
    SELECT
        s.validated,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE so.verdict = 'correct')  AS correct,
        COUNT(*) FILTER (WHERE so.verdict = 'incorrect') AS incorrect,
        AVG(so.return_24h) FILTER (WHERE so.return_24h IS NOT NULL) AS avg_return
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE so.fully_resolved = TRUE
    GROUP BY s.validated
    ORDER BY s.validated DESC
"""

GET_MISSED_OPPORTUNITIES = """
    SELECT s.id, s.symbol, s.timeframe, s.divergence_type, s.direction,
           s.entry_price, s.validation_reason, s.created_at,
           so.return_24h, so.max_favorable_pct, so.verdict,
           so.tp1_hit, so.tp2_hit
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE s.validated = FALSE
      AND so.fully_resolved = TRUE
      AND (so.verdict = 'correct' OR so.max_favorable_pct > 1.0)
    ORDER BY so.max_favorable_pct DESC NULLS LAST
    LIMIT 20
"""

GET_BAD_SIGNALS = """
    SELECT s.id, s.symbol, s.timeframe, s.divergence_type, s.direction,
           s.entry_price, s.reasoning, s.created_at,
           so.return_24h, so.max_adverse_pct, so.verdict,
           so.sl_hit, so.sl_hit_at
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    WHERE s.validated = TRUE
      AND so.verdict = 'incorrect'
    ORDER BY so.max_adverse_pct ASC NULLS LAST
    LIMIT 20
"""

GET_PERFORMANCE_TABLE = """
    SELECT s.id, s.symbol, s.timeframe, s.divergence_type, s.direction,
           s.entry_price, s.validated, s.created_at,
           so.return_1h, so.return_4h, so.return_12h, so.return_24h,
           so.max_favorable_pct, so.max_adverse_pct,
           so.tp1_hit, so.tp2_hit, so.tp3_hit, so.sl_hit,
           so.verdict, so.fully_resolved
    FROM signal_outcomes so
    JOIN signals s ON so.signal_id = s.id
    ORDER BY s.created_at DESC
    LIMIT 100
"""
