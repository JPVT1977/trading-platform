# Task Tracking

> Updated by Claude Code during every session.

## In Progress
- [ ] Monitor paper trading — confirm trades start executing after validator loosening (26 Feb deploy)

## Completed
- [x] Loosen validator to get trades flowing (26 Feb) — Rules 10, 13, 14 relaxed/disabled, deployed

## Backlog
- [ ] Review trade quality once sample size builds — tighten rules if win rate is poor
- [ ] Consider re-enabling Rule 14 (candle patterns) if testing shows it improves win rate
- [ ] Consider re-enabling Rule 13 (volume gate) with a more appropriate threshold
- [ ] Dynamic USD→AUD rate fetch (currently hardcoded `_USD_TO_AUD`)
- [ ] Test multi-TF confirmation mode (`use_multi_tf_confirmation=True`)
- [ ] Add `aiohttp_jinja2` to test deps so test_health.py runs in CI
