# Task Tracking

> Updated by Claude Code during every session.

## In Progress
- [ ] Monitor 48-72h trade quality with new fixes (deployed 2 Mar)
- [ ] Deploy security hardening (10 fixes ready, not yet deployed)

## Completed
- [x] Full security audit + 10 hardening fixes (2 Mar) — CSRF, rate limiting, security headers, DB SSL, session timeout, event logging, pinned deps, pip audit CI, sanitised errors, GH Actions permissions
- [x] Fix 5 systematic profitability issues (2 Mar) — circuit breaker, OANDA sizing, reversal protection, ATR stops, directional cap
- [x] Fix P&L bug (27 Feb) — added exit_price column, recalculated historical P&L for 19 orders
- [x] Loosen validator to get trades flowing (26 Feb) — Rules 10, 13, 14 relaxed/disabled

## Backlog
- [ ] Review OANDA trade quality post-fix — should see smaller losses with notional cap
- [ ] Consider re-enabling Rule 14 (candle patterns) if win rate improves
- [ ] Consider re-enabling Rule 13 (volume gate) with a more appropriate threshold
- [ ] Dynamic USD→AUD rate fetch (currently hardcoded `_USD_TO_AUD`)
- [ ] Add `aiohttp_jinja2` to test deps so test_health.py runs in CI
