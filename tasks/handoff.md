# Session Handoff

> Updated by Claude Code at the end of every session.
> Read FIRST at the start of every new session.

## Last Session — 27 February 2026

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
- 147 tests passing (excludes test_health.py — missing aiohttp_jinja2 dep)
- Web dashboard with auth, equity curves, signal history, risk view, broker connections, public stats page
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
- **Validator significantly loosened (25–26 Feb):** Rules 10, 13, 14 all relaxed or disabled to get paper trades flowing. Rule 13 (volume gate) fully disabled. Rule 14 (candle pattern) off by default. Review all thresholds before live trading.
- **test_health.py excluded from CI:** Requires `aiohttp_jinja2` not in test deps.
- **Multi-TF confirmation disabled:** `use_multi_tf_confirmation=False` by default. The logic is built but not battle-tested in production.

### Next Steps
1. **Investigate losses** — total realised P&L is -$563.78 across 24 closed trades. Signal quality needs review (SL hits outnumber TP1 hits 1.6:1)
2. Review trade quality by broker: Binance -$83.52 (mostly small), OANDA -$462.29 (NATGAS -$142, WTICO -$133 + -$231), IG (1 open, no closes yet)
3. Consider tightening validator rules back if loss pattern continues
4. Consider dynamic USD→AUD rate fetch for accurate equity tracking
5. Test multi-TF confirmation mode (currently off)
6. Add `aiohttp_jinja2` to test deps so test_health.py runs in CI

### Critical Context
- **Never modify `broker_interface.py`** — all brokers implement this contract
- **IG stock epics must be in `IG_EPIC_TO_TICKER`** — adding a new stock requires updating the mapping in `instruments.py`
- **Drawdown kill switch** trips if equity drops below 15% from peak. Reset requires DB cleanup + machine restart.
- **Pre-existing CCXT SIGINT on first Fly.io boot** — known, harmless, self-heals on restart
- All three brokers are in **paper/sandbox/demo mode**. No real money at risk.
