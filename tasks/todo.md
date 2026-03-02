# Task Tracking

> Updated by Claude Code during every session.

## In Progress
- [ ] Monitor 48-72h trade quality with new fixes (deployed 2 Mar)

## Completed
- [x] Add all-time P&L to public stats page (2 Mar) — banner with equity change, realised P&L, wins/losses/win rate
- [x] Fix outdated starting capital and position limits in overview Risk Model panel (2 Mar)
- [x] Investigate IG stuck trades (2 Mar) — paper trades, not stuck, just within SL-TP range
- [x] Full P&L breakdown analysis (2 Mar) — down A$1,761 (-5.5%), OANDA primary loss source
- [x] Deploy security hardening (2 Mar) — 10 fixes deployed and verified
- [x] Full security audit + 10 hardening fixes (2 Mar) — CSRF, rate limiting, security headers, session timeout, event logging, pinned deps, pip audit CI, sanitised errors, GH Actions permissions
- [x] Fix 5 systematic profitability issues (2 Mar) — circuit breaker, OANDA sizing, reversal protection, ATR stops, directional cap
- [x] Fix P&L bug (27 Feb) — added exit_price column, recalculated historical P&L for 19 orders
- [x] Loosen validator to get trades flowing (26 Feb) — Rules 10, 13, 14 relaxed/disabled

## Backlog
- [ ] Review OANDA trade quality post-fix — should see smaller losses with notional cap
- [ ] Optimise IG ticker fetch — skip IG API for stock CFDs during off-hours, go straight to Yahoo
- [ ] Consider re-enabling Rule 14 (candle patterns) if win rate improves
- [ ] Consider re-enabling Rule 13 (volume gate) with a more appropriate threshold
- [ ] Dynamic USD→AUD rate fetch (currently hardcoded `_USD_TO_AUD`)
- [ ] Add `aiohttp_jinja2` to test deps so test_health.py runs in CI
