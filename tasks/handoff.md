# Session Handoff

> Updated by Claude Code at the end of every session.
> Read FIRST at the start of every new session.

## Last Session — 2 March 2026 (session 3)

### What Was Done

**IG Stuck Trades Investigation:**
- User reported IG trades "seem stuck". Investigated thoroughly.
- **Finding:** 4 IG positions (AAPL, AMZN, GOOGL, META) are paper trades (`exchange_order_id=paper-ig-...`). They were never placed on IG — the bot is in paper mode. IG demo account correctly shows 0 positions.
- **Finding:** Positions aren't stuck — none have hit SL or TP yet. Current prices are within SL-TP range. Position monitor runs every 2 minutes and checks them.
- **Finding:** IG API retry errors in logs are from off-hours (US market closed) when IG returns null bid/offer. The `IGStockBroker.fetch_ticker` falls back to Yahoo Finance, which works. The retries are cosmetic noise, not a functional problem.
- **No code changes needed** — user confirmed "leave as is".

**Full P&L Breakdown:**
- Queried production DB for complete financial picture since bot started.
- **Total starting capital:** A$32,160 (Binance A$12,160 + OANDA A$10,000 + IG A$10,000)
- **Current equity:** A$30,399 (Binance A$11,643 + OANDA A$8,866 + IG A$9,890)
- **Total realised P&L:** -A$1,276 (Binance -$32, OANDA -$1,134, IG -$110)
- **Equity change:** -A$1,761 (-5.5%)
- **OANDA is the primary loss source** — oversized positions before the notional cap fix
- 7 open positions (4 IG stocks, 2 Binance crypto, 1 OANDA forex)

**Public Stats Page — All-Time P&L (commit `edbaa21`):**
- Added all-time P&L banner to public stats page (`/public/stats?token=...`)
- Shows: equity change (colour-coded), starting capital → current equity, realised P&L, wins/losses/win rate
- Auto-refreshes every 10 seconds via HTMX
- Fixed outdated starting capital in dashboard Risk Model panel (was A$15,000, now A$32,160 with IG)
- Fixed max positions display (was "4 per broker, 8 total", now "Binance: 2, OANDA: 5, IG: 5 = 12 total")
- **Files changed:** `public_stats.py`, `partials/public_stats_cards.html`, `partials/overview_stats.html`
- Deployed and verified. Pushed to origin/main.

### Decisions Made
- **IG positions left as-is** — paper trades working correctly, just haven't hit SL/TP
- **IG retry noise accepted** — cosmetic issue during off-hours, Yahoo fallback works

### Current Project State

**What works:**
- Full analysis cycle: data fetch → TA-Lib indicators → Claude divergence detection → 15-rule validator → risk check → order execution → SL/TP monitoring → alerts
- Three brokers: Binance (crypto, testnet), OANDA (forex/indices/commodities/bonds, practice), IG Markets (stocks, demo)
- 166 tests passing (excludes test_health.py — missing aiohttp_jinja2 dep)
- Web dashboard with auth, equity curves, signal history, risk view, broker connections, performance page
- Public stats page with all-time P&L banner
- Security hardened: CSRF, rate limiting, security headers, session timeout, event logging
- Telegram + SMS alerts on trade open/close and bot start/stop
- Deployed on Fly.io Sydney (`jpvt-trading-bot`, machine `17810972a37ee8`)

**Trading mode:** Paper (all three brokers in sandbox/demo/testnet)

**Open positions (as of 2 Mar):**
- Binance: BTC/USDT (long), ETH/USDT (long)
- IG: AAPL (long, entry 274.33, losing ~$10), AMZN (long, entry 208.23, small profit), GOOGL (long, entry 308.33, small profit), META (long, entry 646.71, flat)
- OANDA: USD_CAD (long)

**Financial snapshot:**
- Starting capital: A$32,160
- Current equity: A$30,399 (-5.5%)
- Realised losses: -A$1,276 (mostly OANDA pre-fix)

### Known Issues
- **IG retry noise during off-hours:** IG returns null bid/offer for stocks when US markets are closed. Retries 3x before Yahoo fallback. Not a functional issue but adds log noise. Could be optimised to skip IG and go straight to Yahoo during off-hours.
- **CCXT SIGINT on first Fly.io boot:** Health check timeout during CCXT import. Machine auto-restarts. Known, harmless.
- **Static USD→AUD rate:** `_USD_TO_AUD` in risk manager is hardcoded. Needs addressing before live.
- **Validator loosened (25–26 Feb):** Rules 13 (volume) disabled, Rule 14 (candle pattern) off. Review before live.
- **test_health.py excluded from CI:** Requires `aiohttp_jinja2` not in test deps.

### Next Steps
1. **Monitor trade quality for 48-72h** — verify profitability fixes are working (OANDA sizing cap, directional cap, ATR stops)
2. **Review OANDA trades specifically** — should see smaller losses now with notional cap
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
