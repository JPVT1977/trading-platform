# Session Handoff

> Updated by Claude Code at the end of every session.
> Read FIRST at the start of every new session.

## Last Session — 3 March 2026

### What Was Done

**Currency Exposure Filter (Check 5b) — commit `d858e14`:**
- Added to `bot/layer4_risk/manager.py` after existing Check 5 (asset class correlation)
- Decomposes each open position into currency exposures: forex pairs split into base+quote, non-forex only tracks quote currency
- Rejects if any fiat currency has `abs(net_count) > max_currency_exposure` (default 2)
- Stablecoins (USDT/BUSD/USDC) normalised to USD
- Non-fiat bases (XAU, SPX, BTC etc.) excluded — already covered by asset-class limits
- New helpers: `_TRACKABLE_CURRENCIES`, `_STABLECOIN_ALIASES`, `_normalise_currency()`, `_get_currency_exposures()`
- **Why:** 26 Feb blowup — EUR_USD long + GBP_USD long + US2000_USD long = 3x short USD, all stopped out together (-A$361)

**Counter-Trend ADX Filter (Rule 7b) — commit `d858e14`:**
- Added to `bot/layer2_intelligence/validator.py` after existing Rule 7 (crypto choppy market)
- Rejects signals opposing a confirmed strong trend: ADX >= 25 + price vs EMA200 position + EMA200 slope must both agree
- Confirmed downtrend (price below EMA200 AND EMA200 falling) blocks LONG signals
- Confirmed uptrend (price above EMA200 AND EMA200 rising) blocks SHORT signals
- Ambiguous trends (mixed signals) pass through — only blocks clear counter-trend
- Applies to ALL asset classes, not just crypto
- **Why:** All three 26 Feb losses were bullish divergence signals against instruments trending down with strong ADX

**Config changes (`bot/config.py`):**
- `max_currency_exposure: int = 2`
- `counter_trend_adx_threshold: float = 25.0`

**Tests:**
- 6 new currency exposure tests (26 Feb scenario, diversification, cancellation, stablecoin, non-fiat, closed positions)
- 6 new counter-trend tests (downtrend/uptrend rejection, with-trend pass, below-threshold pass, ambiguous pass, all asset classes)
- 3 existing tests updated with `max_currency_exposure=10` to isolate their original intent
- **178 tests passing** (up from 166)

**CLAUDE.md updated:**
- Validator rules table: 15 → 16 rules (added Rule 7b)
- Key tuning parameters: added `max_currency_exposure` and `counter_trend_adx_threshold`
- Key design decisions: added #24 (currency exposure filter) and #25 (counter-trend ADX filter)

**Deployed to Fly.io** — verified healthy (`/health` returns 200)
**Committed and pushed** — `d858e14` on `origin/main`

### Decisions Made
- `max_currency_exposure = 2` — conservative start. The 26 Feb scenario is blocked at the third USD-short position. Can relax to 3 if too restrictive.
- `counter_trend_adx_threshold = 25.0` — standard ADX "strong trend" level. Requires both EMA position and slope to agree to avoid false positives during pullbacks.
- Counter-trend filter applies to all asset classes, not just crypto — the 26 Feb losses were forex/index, not crypto.

### Current Project State

**What works:**
- Full analysis cycle: data fetch → TA-Lib indicators → Claude divergence detection → 16-rule validator → risk check → order execution → SL/TP monitoring → alerts
- Three brokers: Binance (crypto, testnet), OANDA (forex/indices/commodities/bonds, practice), IG Markets (stocks, demo)
- 178 tests passing (excludes test_health.py — missing aiohttp_jinja2 dep)
- Web dashboard with auth, equity curves, signal history, risk view, broker connections, performance page
- Public stats page with all-time P&L banner
- Security hardened: CSRF, rate limiting, security headers, session timeout, event logging
- Telegram + SMS alerts on trade open/close and bot start/stop
- Deployed on Fly.io Sydney (`jpvt-trading-bot`, machine `17810972a37ee8`)

**Trading mode:** Paper (all three brokers in sandbox/demo/testnet)

**Financial snapshot (as of 2 Mar):**
- Starting capital: A$32,160
- Current equity: A$30,399 (-5.5%)
- Realised losses: -A$1,276 (mostly OANDA pre-fix)

### Files Modified This Session
- `bot/config.py` — 2 new settings
- `bot/layer4_risk/manager.py` — Check 5b + helpers
- `bot/layer2_intelligence/validator.py` — Rule 7b
- `tests/test_risk_manager.py` — 6 new tests + 3 updated
- `tests/test_validator.py` — 6 new tests
- `CLAUDE.md` — docs updated

### Known Issues
- **IG retry noise during off-hours:** IG returns null bid/offer for stocks when US markets are closed. Retries 3x before Yahoo fallback. Not a functional issue but adds log noise.
- **CCXT SIGINT on first Fly.io boot:** Health check timeout during CCXT import. Machine auto-restarts. Known, harmless.
- **Static USD→AUD rate:** `_USD_TO_AUD` in risk manager is hardcoded. Needs addressing before live.
- **Validator loosened (25–26 Feb):** Rules 13 (volume) disabled, Rule 14 (candle pattern) off. Review before live.
- **test_health.py excluded from CI:** Requires `aiohttp_jinja2` not in test deps.
- **backtest/ directory:** Untracked files exist (`backtest/__init__.py`, `data_loader.py`, `report.py`, `simulator.py`, `cache/`, `results/`). Not committed — appears to be work in progress.

### Next Steps
1. **Monitor trade quality for 48-72h** — verify currency exposure + counter-trend filters are working (check logs for new rejection reasons)
2. **Review OANDA trades specifically** — should see smaller losses now with all fixes combined
3. **Consider dynamic USD→AUD rate fetch** for accurate equity tracking
4. **Add `aiohttp_jinja2` to test deps** so test_health.py runs in CI
5. **Evaluate tightening rules back** if win rate improves (re-enable Rule 13 volume, Rule 14 candle patterns)
6. **Optimise IG ticker fetch** — skip IG API for stock CFDs during off-hours, go straight to Yahoo

### Critical Context
- **Never modify `broker_interface.py`** — all brokers implement this contract
- **IG stock epics must be in `IG_EPIC_TO_TICKER`** — adding a new stock requires updating the mapping in `instruments.py`
- **Drawdown kill switch** trips if equity drops below 15% from peak. Reset requires DB cleanup + machine restart.
- **Fly Postgres uses WireGuard** — never force application-level SSL on asyncpg (`ssl=` causes ConnectionResetError)
- All three brokers are in **paper/sandbox/demo mode**. No real money at risk.
- **Public stats token:** `T3tRmbKTJp0n5IMx3mr1b4eR-EunzaH0` (env var `PUBLIC_STATS_TOKEN`)
- **Tier limits hardcoded in 3 places** — see MEMORY.md for details
