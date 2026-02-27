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
- **Multi-TF confirmation enabled (27 Feb):** `use_multi_tf_confirmation=True` by default. 4h setups + 1h triggers. Trade volume will drop significantly.
- **Partial TP new (27 Feb):** 2-stage close flow not yet battle-tested in production. Monitor for edge cases (e.g. rapid price movement through both TP1 and TP2 in one monitor cycle).

### Next Steps
1. **Deploy partial TP + MTF changes** — set Fly.io secrets (`USE_MULTI_TF_CONFIRMATION=true`, `MIN_RISK_REWARD=2.0`, `TP1_CLOSE_PCT=0.5`) and deploy
2. **Monitor new trade flow** — watch logs for "Multi-TF: 4h setup CREATED", "Multi-TF: CONFIRMED", "PARTIAL CLOSE TP1" messages
3. **Verify accumulated P&L display** — check dashboard shows correct total P&L for partially-closed positions
4. Consider dynamic USD→AUD rate fetch for accurate equity tracking
5. Add `aiohttp_jinja2` to test deps so test_health.py runs in CI
6. Review trade quality after 48-72h with new parameters

### Critical Context
- **Never modify `broker_interface.py`** — all brokers implement this contract
- **IG stock epics must be in `IG_EPIC_TO_TICKER`** — adding a new stock requires updating the mapping in `instruments.py`
- **Drawdown kill switch** trips if equity drops below 15% from peak. Reset requires DB cleanup + machine restart.
- **Pre-existing CCXT SIGINT on first Fly.io boot** — known, harmless, self-heals on restart
- All three brokers are in **paper/sandbox/demo mode**. No real money at risk.
