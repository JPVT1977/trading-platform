# Task Tracking

> Updated by Claude Code during every session.

## In Progress
- [ ] Investigate loss pattern — total P&L -$563.78, SL:TP hit ratio 1.6:1

## Completed
- [x] Fix P&L bug (27 Feb) — added exit_price column, recalculated historical P&L for 19 orders, fixed filled_price overwrite
- [x] Loosen validator to get trades flowing (26 Feb) — Rules 10, 13, 14 relaxed/disabled, deployed

## Backlog
- [ ] Review trade quality by broker (Binance -$83, OANDA -$462) — tighten rules if pattern continues
- [ ] Consider re-enabling Rule 14 (candle patterns) if testing shows it improves win rate
- [ ] Consider re-enabling Rule 13 (volume gate) with a more appropriate threshold
- [ ] Dynamic USD→AUD rate fetch (currently hardcoded `_USD_TO_AUD`)
- [ ] Test multi-TF confirmation mode (`use_multi_tf_confirmation=True`)
- [ ] Add `aiohttp_jinja2` to test deps so test_health.py runs in CI
