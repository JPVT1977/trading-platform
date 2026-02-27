# Project: Divergence Trading Bot

> Project-specific context for Claude Code.

## Project Overview
- **Name:** trading-platform
- **Purpose:** Automated divergence trading bot that detects RSI/MACD/Stochastic divergences using Claude AI (tool_use), validates them through a deterministic rule engine, and executes orders across multiple brokers.
- **Tech Stack:** Python 3.13, asyncio, Pydantic, TA-Lib, Claude API (tool_use), APScheduler, aiohttp, asyncpg, Loguru
- **Hosting:** Fly.io Sydney region (`jpvt-trading-bot`), machine `17810972a37ee8`
- **Database:** PostgreSQL (Fly Postgres)
- **GitHub:** JPVT1977/trading-platform
- **Trading Mode:** Paper (testnet) — all three brokers in sandbox/demo mode
- **Last Updated:** 26 February 2026

---

## Architecture

### Layered Design

```
bot/main.py                         Entry point, scheduler, analysis_cycle()
bot/config.py                       Pydantic Settings — all config from env vars
bot/models.py                       Shared Pydantic models (Candle, DivergenceSignal, etc.)
bot/instruments.py                  Symbol registry, broker routing, epic-to-ticker mapping

bot/layer1_data/                    Data & Connectivity
  broker_interface.py               Abstract BrokerInterface (8 methods)
  broker_router.py                  Routes symbols to registered brokers by broker_id
  market_data.py                    CCXT wrapper for Binance
  oanda_client.py                   OANDA REST API client
  ig_client.py                      IG Markets REST API client
  ig_session.py                     IG session/auth management
  ig_rate_limiter.py                IG API rate limiter
  ig_stock_broker.py                Composite broker: Yahoo data + IG orders (stocks)
  yahoo_provider.py                 Yahoo Finance OHLCV fetcher (yfinance)
  indicators.py                     TA-Lib indicator computation
  payload_builder.py                Builds Claude analysis payloads

bot/layer2_intelligence/            AI & Signal Processing
  claude_client.py                  Claude API with tool_use for divergence detection
  tools.py                          Claude tool schemas
  prompts.py                        System prompts for Claude
  validator.py                      Deterministic rule engine (15 rules, <1ms)
  scoring.py                        Signal quality scoring (confidence dimensions)

bot/layer3_execution/               Order Management
  engine.py                         Order execution, position monitor, SL/TP management
  order_state.py                    FSM state machine for order lifecycle

bot/layer4_risk/                    Risk Management
  manager.py                        Position sizing, exposure limits, circuit breakers

bot/layer5_monitoring/              Observability
  health.py                         Health check server (aiohttp on port 8080)
  logger.py                         Loguru configuration
  telegram.py                       Telegram alert client
  sms.py                            ClickSend SMS alerts
  outcome_tracker.py                Signal outcome tracking (TP/SL hit verdicts)

bot/database/                       Persistence
  connection.py                     asyncpg pool + schema migration
  queries.py                        SQL queries
  schema.sql                        Database schema

bot/dashboard/                      Web Dashboard
  routes.py, views/, api/           aiohttp routes with Jinja2 templates
  middleware.py                     Auth middleware
  setup_users.py                    Dashboard user seeding
  static/, templates/               Frontend assets

tests/                              pytest-asyncio test suite (159 tests)
tasks/                              Session management (handoff, lessons, todo)
```

### Three Brokers

| Broker | Symbols | Data Source | Order Execution |
|--------|---------|-------------|-----------------|
| **Binance** | BTC/USDT, ETH/USDT, SOL/USDT, DOGE/USDT | CCXT (Binance API) | CCXT (Binance API) |
| **OANDA** | 24 instruments (8 forex pairs, 6 indices, 6 commodities, 2 bonds) | OANDA REST API | OANDA REST API |
| **IG Markets** | 8 stock CFDs (NVDA, AAPL, MSFT, AMZN, TSLA, META, GOOGL, AVGO) | Yahoo Finance (yfinance) | IG REST API |

IG blocks historical OHLCV for stock CFDs (`unauthorised.access.to.equity.exception`). The `IGStockBroker` composite pattern solves this: Yahoo for data, IG for orders. Indices/commodities on IG use IG directly.

### Per-Broker Risk Limits

| Setting | Binance | OANDA | IG |
|---------|---------|-------|-----|
| Max open positions | 2 | 5 | 5 |
| Max correlation exposure | 3 | 3 | 3 |
| Min confidence | 0.70 | 0.70 | 0.70 |

---

## Analysis Cycle Flow

APScheduler triggers `analysis_cycle()` every 5 minutes:

1. **Layer 1 — Data:** Fetch OHLCV candles (only on new candle) -> compute TA-Lib indicators -> build payload
2. **Layer 2 — Intelligence:** Send payload to Claude (tool_use) -> receive `DivergenceSignal`
3. **Layer 2 — Validation:** Deterministic validator (15 rules, <1ms) gates the signal
4. **Layer 4 — Risk:** Risk manager checks portfolio limits, position sizing (ATR-based)
5. **Layer 3 — Execution:** Execute order if all checks pass
6. **Layer 5 — Monitoring:** Log + Telegram/SMS alert

### Multi-Timeframe Confirmation
- 4h signals stored as "setups" (valid for 24h)
- 1h signals confirm/trigger the 4h setups
- Enabled via `use_multi_tf_confirmation=True`

### Scheduled Jobs
- **Analysis cycle:** every 5 minutes (divergence detection)
- **Position monitor:** every 2 minutes (SL/TP checks, 2-stage partial TP: 50% at TP1, trail 50% to TP2)
- **Outcome tracker:** every 5 minutes (fills checkpoint prices, TP/SL hit verdicts)

---

## Validator Rules (15 Rules)

The deterministic validator in `validator.py` runs these rules in order. First failure rejects the signal:

| Rule | What It Checks |
|------|---------------|
| 0 | Signal has a direction (long/short) |
| 1 | Minimum confidence threshold (0.70) |
| 2 | Required fields present (entry, stop_loss) |
| 3 | Stop loss on correct side of entry |
| 4 | Risk:reward ratio >= 2.0 |
| 5 | RSI not contradicting direction |
| 6 | ATR stop distance (0.5x–5.0x ATR) |
| 7 | ADX trend strength (crypto only, >= threshold) |
| 8 | Ranging market detection (Bollinger bandwidth) |
| 9 | Minimum 2 confirming oscillators |
| 10 | Swing length minimums (10 bars 4h, 7 bars 1h) |
| 11 | RSI divergence magnitude >= 3.0 |
| 12 | Zero/near-zero volume guard |
| 13 | Low volume gate — **disabled** (threshold 0.0) |
| 14 | Candlestick reversal pattern — **disabled** (require_candle_pattern=False) |

### Key Tuning Parameters (config.py)

| Parameter | Current Value | Purpose |
|-----------|--------------|---------|
| `volume_low_threshold` | 0.0 (disabled) | Rule 13 — was 0.50→0.35→0.10→0.0 (disabled 26 Feb 2026) |
| `require_candle_pattern` | False | Rule 14 — candle pattern toggle (disabled 26 Feb 2026) |
| `min_divergence_score` | 5.0 | Minimum scoring threshold |
| `min_confirming_indicators` | 2 | Rule 9 — oscillator confluence |
| `min_swing_bars_4h` | 10 | Rule 10 — minimum 4h swing length (was 15, relaxed 26 Feb 2026) |
| `min_swing_bars_1h` | 7 | Rule 10 — minimum 1h swing length (was 10, relaxed 26 Feb 2026) |
| `min_divergence_magnitude_rsi` | 3.0 | Rule 11 — RSI magnitude floor (was 5.0, relaxed 25 Feb 2026) |
| `min_risk_reward` | 2.0 | Rule 4 — R:R floor (restored from 1.5, 27 Feb 2026) |
| `tp1_close_pct` | 0.5 | Partial TP — close 50% at TP1, trail 50% to TP2 (added 27 Feb 2026) |
| `use_multi_tf_confirmation` | True | Multi-TF — 4h setup + 1h trigger (enabled 27 Feb 2026) |
| `max_atr_multiple` | 7.0 | Rule 6 — ATR stop width ceiling (was 5.0, relaxed 25 Feb 2026) |
| `max_drawdown_pct` | 15.0 | Drawdown kill switch threshold |

---

## Key Integrations

- **Claude API** — `claude-sonnet-4-5-20250929` with tool_use for structured divergence detection
- **Binance** — via CCXT (testnet/sandbox for paper mode)
- **OANDA** — REST API v20 (practice account for paper mode)
- **IG Markets** — REST API (demo account for paper mode)
- **Yahoo Finance** — yfinance library for IG stock OHLCV data
- **PostgreSQL** — asyncpg driver, Fly Postgres
- **Telegram** — Bot API for trade alerts
- **ClickSend** — SMS alerts
- **TA-Lib** — Technical indicators (RSI, MACD, Stochastic, MFI, CCI, Williams %R, ATR, EMA, Bollinger Bands)

---

## Key Design Decisions

1. **Fully async** — asyncio throughout. No sync blocking calls (yfinance wrapped in `asyncio.to_thread`).
2. **Claude tool_use** — Structured output via tool schemas. No JSON parsing.
3. **Deterministic validator** — Rule engine (<1ms) gates signals before execution. Zero API calls.
4. **Order FSM** — State machine prevents invalid order transitions.
5. **ATR position sizing** — Risk-based sizing, never fixed lot sizes.
6. **Circuit breakers** — Daily loss limit + drawdown kill switch auto-stop trading.
7. **APScheduler** — In-process scheduling, no Celery/Redis needed.
8. **asyncpg** — Async Postgres driver, not psycopg2.
9. **Composite broker pattern** — `IGStockBroker` transparently delegates data to Yahoo, orders to IG. Rest of codebase sees a normal `BrokerInterface`.
10. **Per-broker risk isolation** — Each broker has independent position limits, correlation limits, and confidence thresholds.
11. **Separate exit_price column** — `filled_price` stores the fill/entry price, `exit_price` stores the close price. `UPDATE_ORDER_CLOSE` writes to `exit_price`, never overwrites `filled_price`.
12. **Partial profit-taking** — 50% closed at TP1 (configurable via `tp1_close_pct`), remaining 50% trails to TP2 with progressive stop tightening. P&L accumulates across partial closes via `COALESCE(pnl, 0) + new_pnl`.
13. **Multi-TF confirmation** — 4h signals stored as setups (24h expiry), 1h signals only execute if matching 4h setup exists. Dramatically reduces trade volume but increases quality.

---

## File Structure Notes

- `bot/instruments.py` — **Central symbol registry**. All symbol routing (`route_symbol()`), epic-to-ticker mappings (`IG_EPIC_TO_TICKER`), and instrument metadata live here. Contains `BrokerType`, `AssetClass` enums and `InstrumentInfo` dataclass.
- `bot/models.py` — **All shared models**: `Candle`, `IndicatorSet`, `DivergenceSignal`, `ValidationResult`, `OrderState` enum, `SignalDirection`, `DivergenceType`.
- `bot/config.py` — **Single source of truth** for all configuration. Pydantic `BaseSettings` reads from env vars / `.env` file. Per-broker overrides via `get_max_open_positions(broker_id)` etc.
- `bot/main.py` — **Entrypoint**. Broker registration, scheduler setup, `analysis_cycle()`, `position_monitor()`, `track_signal_outcomes()`, candle cache seeding.
- `bot/layer1_data/broker_interface.py` — Abstract base with 8 methods: `broker_id`, `fetch_ohlcv`, `fetch_ticker`, `fetch_balance`, `create_limit_order`, `create_stop_order`, `cancel_order`, `check_connectivity`, `close`.

---

## Project-Specific Rules

- **Never modify `broker_interface.py`** — all brokers implement this contract. Changing it breaks everything.
- **All config via env vars** — never hardcode secrets. Use `flyctl secrets set` for production.
- **IG stock epics must be in `IG_EPIC_TO_TICKER`** — adding a new stock requires updating the mapping in `instruments.py`.
- **Pre-existing CCXT SIGINT on first Fly.io boot** — this is a known issue (health check timeout during CCXT import). The machine auto-restarts and second boot succeeds. Do not try to fix this.
- **test_health.py excluded from CI** — requires `aiohttp_jinja2` not in test deps. Run with `--ignore=tests/test_health.py`.
- **Drawdown kill switch** — trips if equity drops below `max_drawdown_pct` (15%) from peak in `portfolio_snapshots`. Reset requires DB cleanup + machine restart.

---

## Common Commands

```bash
# Run tests (excludes test_health.py — missing aiohttp_jinja2 dep)
python3 -m pytest tests/ --ignore=tests/test_health.py -v

# Lint
python3 -m ruff check bot/ tests/

# Deploy to Fly.io
flyctl deploy --remote-only --app jpvt-trading-bot

# Check status
flyctl status --app jpvt-trading-bot

# View logs (recent)
flyctl logs --app jpvt-trading-bot --no-tail

# View logs (streaming)
flyctl logs --app jpvt-trading-bot

# Set secrets
flyctl secrets set KEY=value --app jpvt-trading-bot

# SSH into machine
flyctl ssh console --app jpvt-trading-bot

# Health check
curl -s https://jpvt-trading-bot.fly.dev/health
curl -s https://jpvt-trading-bot.fly.dev/health/deep

# Restart machine
flyctl machines restart 17810972a37ee8 --app jpvt-trading-bot
```

---

## Trading Modes

| Mode | Exchange Calls | Orders | Use Case |
|------|---------------|--------|----------|
| `dev` | None (mock) | None | Local development, tests |
| `paper` | Real data | Simulated (testnet/sandbox/demo) | Validation before live |
| `live` | Real data | Real money | Production |

---

## Credentials Location

- **Fly.io secrets** — all production env vars (`flyctl secrets list --app jpvt-trading-bot`)
- **Local** — `.env` file in project root (gitignored)
- **Dashboard users** — seeded at startup from `DASHBOARD_USER_*` env vars

---

## Session Management

- `tasks/handoff.md` — read at session start, updated at session end
- `tasks/lessons.md` — updated after corrections/mistakes
- `tasks/todo.md` — task tracking during sessions
