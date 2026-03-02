# Session Handoff

> Updated by Claude Code at the end of every session.
> Read FIRST at the start of every new session.

## Last Session — 2 March 2026

### What Was Done (Full Project History — 63 commits, 22–25 Feb 2026)

**Day 1 (22 Feb):** Initial scaffold → complete 5-layer bot in a single day.
- Project scaffolding, complete divergence trading bot (all 5 layers)
- Claude model ID fix and JSONB serialisation bug
- IndicatorSet None-handling for TA-Lib warmup periods
- Dashboard (Nucleus360), ClickSend SMS alerts, Telegram alerts
- Paper trade position monitor (SL/TP with real P&L)
- Auto-refresh dashboard (30s), Melbourne timezone
- Signal performance tracker, live heartbeat, per-symbol cycle detail
- Fix deploy-triggered trade churn (seed candle dedup cache from DB on startup)
- Fix candle cache seed (fetch from exchange, not empty candles table)
- Switched exchange to Binance, fixed dashboard bugs
- Hybrid candle analysis (detect on forming candles, not just closed)
- Added CCI/Williams %R indicators, max drawdown kill switch, expanded to 10 symbols
- Multi-TF confirmation (4h setup + 1h trigger)
- **18-bug audit** (bulletproof sweep): IG response leak, fee calc, PK, asyncio, pip_value, throttle import, currencyCode
- Analysis interval: 15min → 5min → 1min
- Major overhaul: fixed trading deadlock, added reversals, faster SL/TP monitoring
- Persisted ALL detected signals, added live unrealized P&L to dashboard

**Day 2 (23 Feb):** Dashboard polish, multi-broker expansion, risk engine.
- OANDA forex broker integration (alongside Binance)
- Dashboard password features (change, reset)
- IG Markets broker integration + Broker Connections dashboard page
- Expanded OANDA to 30 instruments with per-asset-class correlation tracking
- Fixed 7 bugs (IG response leak, fee calc, PK, asyncio, pip_value, throttle, currencyCode)
- Equity allocation breakdown (in-trade vs available)
- Dashboard AUD conversion + leverage display
- Public stats page (bookmarkable, mobile-friendly, token-protected)
- Equity double-conversion fix (store snapshots in AUD at write time)
- OANDA position sizing fix (AUD rates, guard zero entry price)
- Circuit breaker reset button on risk dashboard
- Reduced crypto to BTC/USDT only
- Drawdown check fix (convert equity to AUD before comparing)
- Per-broker equity split on dashboard overview
- Combined equity fix (sum latest snapshot per broker)
- Trading hours and risk model info panels
- Breakeven + profit-lock trailing stop loss
- Fixed equity breakdown bugs (multiple iterations: capital at risk, margin used, SL risk)
- Position sizing safety guard (prevent undersized trades)
- Signal Quality Engine: scoring, validation rules 9-14, candle pattern detection
- Fixed 6 audit bugs in Signal Quality Engine
- 104 ruff lint fixes

**Day 3 (24 Feb):** IG Markets stocks, position tuning.
- Adjusted broker position limits (OANDA 4→10, Binance 4→2)
- Split positions 50/50: OANDA 5, IG 5, Binance 2
- Risk dashboard fix (show per-broker totals, not global max)
- Added 8 stock CFDs + USB02Y bond + STOCK asset class
- Fixed IG epic codes (CASH not DAILY)
- Added Yahoo Finance data provider for IG stock CFDs (composite broker pattern)

**Day 4 (25 Feb):** Validator tuning, cost reduction, session management.
- Relaxed volume validation threshold (50% → 35% → 10% of SMA)
- Added session management system (tasks/ directory, expanded CLAUDE.md)
- Skip Claude API calls on forming candles (cut costs ~95%)
- Relaxed validator thresholds: min_risk_reward 2.0→1.5, max_atr_multiple 5.0→7.0, min_divergence_magnitude_rsi 5.0→3.0

**Day 5 (26 Feb):** Validator loosening to get trades flowing.
- Bot was detecting 35+ signals per cycle but zero trades executing — Rules 10, 13, 14 blocking everything
- Rule 10: Loosened swing length minimums (min_swing_bars_4h 15→10, min_swing_bars_1h 10→7)
- Rule 13: Disabled volume gate (volume_low_threshold 0.10→0.0) — was the #1 blocker (~15 rejections/cycle). Rule 12 (zero volume guard) still active as safety net
- Rule 14: Made candlestick pattern requirement configurable (require_candle_pattern=False). High-confidence signals were being blocked purely for lacking a pattern in last 3 bars
- Updated tests: 37 validator tests (147 total), added test_disabled_when_threshold_zero + test_skips_when_toggle_disabled
- Set Fly.io secrets + deployed successfully. Health check OK.

**Day 6 (27 Feb):** P&L bug fix — historical orders had pnl=0 despite real price movement.
- **Root cause:** `UPDATE_ORDER_CLOSE` overwrote `filled_price` with exit price, AND early code (22 Feb) didn't calculate P&L on close. 30 of 40 closed orders had pnl=0 incorrectly.
- **Fix:** Added `exit_price` column to orders table. `UPDATE_ORDER_CLOSE` now writes to `exit_price`, never overwrites `filled_price`. Clear separation: `filled_price` = fill/entry price, `exit_price` = close price.
- **Data migration:** Idempotent SQL migration recalculated P&L for 19 historical orders using `(exit_price - entry_price) * quantity` with correct fee calculation (0.1% round-trip for Binance, 0 for OANDA/IG).
- **Cleanup:** 4 unfilled orders reclassified from `closed` → `cancelled`. Closed orders with missing `closed_at` timestamps fixed.
- **Result:** P&L went from 5 orders with data → 24 orders with data. Total realised P&L: -$563.78 (was showing only -$475.65 from 5 trades).
- **Files changed:** schema.sql, queries.py, models.py, engine.py, telegram.py, sms.py, dashboard queries + template, CLAUDE.md
- All 147 tests pass, lint clean. Deployed successfully.

**Day 7 (27 Feb, session 2):** Partial profit-taking + multi-TF confirmation + R:R tightening.
- **Partial TP system:** 50% closed at TP1, remaining 50% trails to TP2 with progressive stop tightening. New DB columns (`remaining_quantity`, `tp_stage`), new query (`UPDATE_ORDER_PARTIAL_CLOSE`), P&L accumulates across partials.
- **Multi-TF confirmation enabled:** `use_multi_tf_confirmation=True`. 4h signals stored as setups, only trade when 1h confirms. Dramatically reduces trade volume (50/2hr → 2-5/day).
- **R:R restored to 2.0:** `min_risk_reward` back from 1.5 to 2.0. With partial TP, effective R:R per trade ~2.5:1, breakeven win rate ~29%.
- **Engine refactored:** `monitor_open_positions()` now handles 2-stage close flow. Stage 0 (full position → TP1 partial close, SL to breakeven). Stage 1 (remaining → trailing stop toward TP2, progressive SL tightening).
- **Reversal close uses remaining_quantity:** `_close_position_for_reversal()` now uses `remaining_quantity` instead of original `quantity`.
- **Risk manager uses remaining_quantity:** `PortfolioState.open_positions` populated with `remaining_quantity` for accurate position sizing.
- **Partial close alerts:** New `send_partial_close_alert()` methods on both TelegramClient and SMSClient.
- **12 new tests:** `test_engine.py` covering P&L calculation, partial TP stages, trailing stops, short positions, and tp1_close_pct=0 fallback.
- **Files changed:** schema.sql, queries.py, config.py, models.py, engine.py, manager.py, telegram.py, sms.py, test_engine.py (new), test_config.py, CLAUDE.md
- All 159 tests pass, lint clean.

**Day 10 (2 Mar):** Deep dive performance analysis + 5 systematic fixes.
- **Performance analysis:** Since Friday, 7 closed trades: 3 wins (+$160), 4 losses (-$200). Net: -$40.22. All-time: -$1,065.32. OANDA biggest loser (-$923.61 of $10,000).
- **Fix 1: Circuit breaker false peak** — Binance `STARTING_EQUITY` was $5,000 (wrong, actual was $7,600). Made configurable via `binance_starting_equity` env var. Reset DB circuit breaker event. Drawdown now calculates correctly (< 1% vs false 23.3%).
- **Fix 2: OANDA position sizing cap** — Added 50% notional equity cap to OANDA sizing (same principle as crypto). Prevents outsized positions even when ATR math says go big.
- **Fix 3: Reversal protection for winners** — Reversal close now blocked if position has partial TP triggered (tp_stage > 0) or accumulated positive P&L. ETH was closed at +$7.53 (0.1R) by reversal instead of running to TP1. Now the partial TP system handles exits for winning trades.
- **Fix 4: Per-asset-class ATR stop minimums** — Crypto now requires 1.5x ATR (was 0.5x global), stocks 1.0x, commodities 1.0x. Forex/indices stay at 0.5x. BTC with 1.2% stop (noise) will now be rejected.
- **Fix 5: Directional exposure cap** — Max 70% of positions in one direction (`max_directional_pct=70`). All 9 open positions were long — now the bot must take some shorts to balance. Kicks in at 3+ positions.
- **7 new tests:** Directional cap (3 tests), reversal protection (2 tests), per-asset-class ATR (2 tests).
- **Files changed:** config.py, manager.py, validator.py, test_risk_manager.py, test_validator.py, conftest.py
- All 166 tests pass, lint clean. Deployed and verified healthy.

**Day 10 (2 Mar, session 2):** Full security audit + 10 security hardening fixes.
- **Security audit:** Comprehensive 7-area audit (secrets, auth, API, Fly.io, deps, data protection, infrastructure). Found 0 critical credential exposures, 7 HIGH findings in dashboard web layer.
- **Fix 1: Sanitised /health/deep errors** — replaced raw `str(e)` with generic `"check_failed"`. Errors logged server-side, not exposed to callers.
- **Fix 2: Security headers middleware** — Added `security_headers_middleware` to aiohttp. Sets X-Frame-Options: DENY, X-Content-Type-Options: nosniff, HSTS (63072000s), CSP, Referrer-Policy, Permissions-Policy.
- **Fix 3: Rate limiting on auth endpoints** — In-memory per-IP rate limiter (5 attempts/60s) on `/login` and `/reset-password`. Returns 429 when exceeded.
- **Fix 4: Fly.io health check switched to /health** — `fly.toml` now uses shallow `/health` instead of `/health/deep`. Deep check requires authentication (removed from `PUBLIC_PATHS`).
- **Fix 5: CSRF protection** — Per-session CSRF token in cookie (`csrf_token`). All HTML forms include hidden `csrf_token` field. HTMX sends it via `X-CSRF-Token` header (configured in base.html). Middleware validates on all authenticated POST requests.
- **Fix 6: Database SSL** — Added `ssl=ssl.create_default_context()` (CERT_NONE for Fly internal certs) to asyncpg pool. Defence-in-depth.
- **Fix 7: Pinned dependencies** — Created `requirements.lock` with exact versions. Dockerfile now uses `requirements.lock`. `requirements.txt` kept for development.
- **Fix 8: pip audit in CI** — Added `pip-audit` step to `test.yml` (continue-on-error).
- **Fix 9: Security event logging** — All auth events logged with `SECURITY:` prefix: failed logins, successful logins, password changes, password resets, circuit breaker resets. Includes IP address.
- **Fix 10: Session lifetime reduced** — 24h → 4h for session cookies. CSRF token lifetime matches. GH Actions permissions scoped to `contents: read`. Pagination bounded to max 10,000. Broker test errors sanitised (generic "Connection failed").
- **Files changed:** middleware.py (rewritten), health.py, auth.py, risk.py, brokers.py, positions.py, connection.py, Dockerfile, fly.toml, deploy.yml, test.yml, base.html, login.html, reset_password.html, change_password.html, requirements.lock (new)
- All 166 tests pass, lint clean.

### Decisions Made
- **Composite broker pattern for IG stocks:** Yahoo Finance provides OHLCV data, IG handles orders. IG blocks historical data for stock CFDs (`unauthorised.access.to.equity.exception`).
- **Only analyse closed candles:** Forming candles are skipped — saves ~95% Claude API cost. Only new candle close triggers analysis.
- **Per-broker risk isolation:** Each broker has independent position limits, correlation limits, and confidence thresholds. Not pooled.
- **Breakeven + profit-lock trailing SL:** At 50% progress to TP1, SL moves to entry (breakeven). At 75%, SL moves to entry + 25% of range.
- **Validator relaxation trend (25–26 Feb):** Multiple thresholds relaxed to allow trades through during low-activity paper trading. These may need tightening before live.
- **Rule 13 disabled (26 Feb):** Volume gate set to 0.0 — off-hours volume naturally low, SMA comparison penalises normal low-liquidity periods. Rule 12 (zero/near-zero guard) remains as safety net.
- **Rule 14 made optional (26 Feb):** Candlestick pattern confirmation disabled via `require_candle_pattern=False`. Can be re-enabled via env var without redeploy if testing shows patterns improve win rate.
- **AUD as base display currency:** All equity snapshots stored in AUD. Binance USD converted at a static rate (`_USD_TO_AUD`).
- **Signal-level dedup:** Once a divergence is found on a candle, that candle is not re-analysed next cycle. Clears on new candle.

### Current Project State

**What works:**
- Full analysis cycle: data fetch → TA-Lib indicators → Claude divergence detection → 15-rule validator → risk check → order execution → SL/TP monitoring → alerts
- Three brokers: Binance (crypto, testnet), OANDA (forex/indices/commodities/bonds, practice), IG Markets (stocks/indices/commodities, demo)
- 166 tests passing (excludes test_health.py — missing aiohttp_jinja2 dep)
- Web dashboard with auth, equity curves, signal history, risk view, broker connections, public stats page
- Security hardened: CSRF, rate limiting, security headers, DB SSL, session timeout, event logging
- Telegram + SMS alerts on trade open/close and bot start/stop
- Deployed on Fly.io Sydney (`jpvt-trading-bot`, machine `17810972a37ee8`)
- Signal outcome tracker (checkpoint prices, TP/SL hit verdicts)
- Portfolio snapshots with drawdown kill switch

**Trading mode:** Paper (all three brokers in sandbox/demo/testnet)

**Instruments:**
- Binance: BTC/USDT (1 symbol)
- OANDA: 30 instruments (15 forex, 6 commodities, 8 indices, 1 bond — USB02Y_USD added, USB10Y_USD already there)
- IG: 12 instruments (8 stock CFDs via Yahoo+IG composite, 3 indices, 1 commodity)

### Established Patterns & Conventions
- **Layered architecture:** L1 Data → L2 Intelligence → L3 Execution → L4 Risk → L5 Monitoring
- **BrokerInterface contract:** 8 abstract methods. Never modify `broker_interface.py`.
- **All config via env vars** (Pydantic BaseSettings). `flyctl secrets set` for production.
- **Instrument registry** in `instruments.py`. Adding a new instrument = add entry there + update IG_EPIC_TO_TICKER if stock.
- **Route by symbol:** `route_symbol()` determines which broker handles each symbol.
- **Tests:** `python3 -m pytest tests/ --ignore=tests/test_health.py -v`
- **Lint:** `python3 -m ruff check bot/ tests/`
- **Deploy:** `flyctl deploy --remote-only --app jpvt-trading-bot`

### Known Issues
- **CCXT SIGINT on first Fly.io boot:** Health check timeout during CCXT import. Machine auto-restarts and second boot succeeds. Do not try to fix.
- **Static USD→AUD rate:** `_USD_TO_AUD` in risk manager is hardcoded, not fetched live. Acceptable for paper trading, needs addressing before live.
- **Validator loosened (25–26 Feb):** Rules 13 (volume) disabled, Rule 14 (candle pattern) off. Review before live trading.
- **test_health.py excluded from CI:** Requires `aiohttp_jinja2` not in test deps.
- **Multi-TF confirmation enabled (27 Feb):** 4h setups + 1h triggers.
- **Partial TP (27 Feb):** 2-stage close flow. Monitor for rapid price-through-TP edge cases.
- **Directional cap (2 Mar):** 70% max one direction. May restrict some trades that would otherwise be valid. Configurable via `MAX_DIRECTIONAL_PCT` env var.
- **Per-asset-class ATR stops (2 Mar):** Crypto 1.5x, stocks/commodities 1.0x. May reject more signals. Configurable via env vars.

### Next Steps
1. **Deploy security hardening** — all 10 fixes ready, needs `flyctl deploy`
2. **Monitor trade quality for 48-72h** — verify new stops are wider, directional cap fires, reversals don't kill winners
3. **Review OANDA trades specifically** — should see smaller losses now with notional cap
4. **Consider dynamic USD→AUD rate fetch** for accurate equity tracking
5. **Add `aiohttp_jinja2` to test deps** so test_health.py runs in CI
6. **Evaluate tightening rules back** if win rate improves (re-enable Rule 13 volume, Rule 14 candle patterns)

### Critical Context
- **Never modify `broker_interface.py`** — all brokers implement this contract
- **IG stock epics must be in `IG_EPIC_TO_TICKER`** — adding a new stock requires updating the mapping in `instruments.py`
- **Drawdown kill switch** trips if equity drops below 15% from peak. Reset requires DB cleanup + machine restart.
- **Pre-existing CCXT SIGINT on first Fly.io boot** — known, harmless, self-heals on restart
- All three brokers are in **paper/sandbox/demo mode**. No real money at risk.
