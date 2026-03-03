# Lessons Learned

> Updated by Claude Code after every correction or mistake.
> Reviewed at the start of every session.

## Rules (Escalated — mistakes made more than once)

_None yet_

## Session Lessons

### 2 Mar 2026 — ARCHITECTURE
**Mistake:** Binance STARTING_EQUITY was hardcoded at $5,000 when actual account had $7,600.
**Root Cause:** Early scaffold used a placeholder that was never updated. Peak equity snapshots from wrong starting point caused false circuit breaker trip.
**Rule:** All starting equity values must be configurable via env vars, never hardcoded constants.

### 2 Mar 2026 — ARCHITECTURE
**Mistake:** OANDA position sizes created losses 10x larger than Binance ($231 single WTI trade).
**Root Cause:** No notional cap on OANDA sizing — with tight stops, ATR-based sizing produced enormous unit counts. A 10-pip stop on oil = 1,300 units = massive exposure.
**Rule:** All brokers must have notional caps (max 50% of equity) regardless of ATR-based sizing math.

### 2 Mar 2026 — ARCHITECTURE
**Mistake:** Reversal signals closing winning trades at 0.1R instead of letting partial TP system manage exit.
**Root Cause:** Reversal close logic didn't check if position was already profitable.
**Rule:** Never reverse out of a position that has partial TP triggered or positive accumulated P&L.

### 2 Mar 2026 — ARCHITECTURE
**Mistake:** All 9 open positions were long — zero directional diversification.
**Root Cause:** No check for directional concentration in risk manager.
**Rule:** Enforce directional exposure caps when 3+ positions are open. Default 70%.

### 3 Mar 2026 — ARCHITECTURE
**Mistake:** EUR_USD long + GBP_USD long + US2000_USD long all stopped out together (-A$361). All three were effectively USD-short bets.
**Root Cause:** Asset class correlation limits (Check 5) allowed 4 same-direction forex positions. It didn't see that positions across different asset classes could share underlying currency risk.
**Rule:** Track net fiat currency exposure across ALL open positions. Max 2 positions effectively long/short any single currency (`max_currency_exposure`).

### 3 Mar 2026 — ARCHITECTURE
**Mistake:** Bullish divergence signals opened against instruments trending down with strong ADX. All three 26 Feb losses were counter-trend trades.
**Root Cause:** ADX < 20 rejection (Rule 7) only applied to crypto. No counter-trend filter for forex/indices.
**Rule:** Reject signals opposing confirmed strong trends (ADX >= 25 + price vs EMA200 + EMA slope must agree). Applies to ALL asset classes (`counter_trend_adx_threshold`).

### 4 Mar 2026 — ARCHITECTURE
**Mistake:** Pre-TP1 trailing stop code was dead — gated behind `if partial_tp == 0:` which never executes when `tp1_close_pct = 0.5`. Four positions stopped out for -$454.93 on 3 March despite being in profit during the day.
**Root Cause:** The gate was likely intended to disable trailing when partial TP was active, but the logic is backwards — trailing is *more* important before TP1 when the position is at full size. Additionally, TP1 partial close unconditionally set SL to entry price, regressing any trailed SL.
**Rule:** Never gate protective logic behind feature flags that disable it when the feature is active. SL updates must use max(current, new) for longs / min(current, new) for shorts — never go backwards.

### 2 Mar 2026 — INFRASTRUCTURE
**Mistake:** Added `ssl=ssl.create_default_context()` to asyncpg pool — caused `ConnectionResetError` on Fly Postgres.
**Root Cause:** Fly Postgres uses WireGuard (6PN) for network-level encryption, not application-level TLS. The Postgres server rejects TLS handshake attempts.
**Rule:** Never force application-level SSL on Fly Postgres internal connections. WireGuard already encrypts the traffic.
