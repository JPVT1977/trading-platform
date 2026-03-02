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

### 2 Mar 2026 — INFRASTRUCTURE
**Mistake:** Added `ssl=ssl.create_default_context()` to asyncpg pool — caused `ConnectionResetError` on Fly Postgres.
**Root Cause:** Fly Postgres uses WireGuard (6PN) for network-level encryption, not application-level TLS. The Postgres server rejects TLS handshake attempts.
**Rule:** Never force application-level SSL on Fly Postgres internal connections. WireGuard already encrypts the traffic.
