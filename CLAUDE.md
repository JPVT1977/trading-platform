# Trading Platform - Developer Manual

**Last Updated:** 23 February 2026
**Trading Mode:** Paper (testnet)
**Deployment:** Fly.io Sydney region (`jpvt-trading-bot`)
**GitHub:** JPVT1977/trading-platform

---

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `bot/` | All application code |
| `bot/config.py` | Pydantic Settings — all config from env vars |
| `bot/models.py` | All shared Pydantic data models |
| `bot/layer1_data/` | CCXT data fetching, TA-Lib indicators, payload builder |
| `bot/layer2_intelligence/` | Claude API with tool_use, deterministic validator |
| `bot/layer3_execution/` | Order execution engine, FSM state machine |
| `bot/layer4_risk/` | Position sizing, exposure limits, circuit breakers |
| `bot/layer5_monitoring/` | Loguru logging, Telegram alerts, health check |
| `bot/database/` | asyncpg pool, queries, schema |
| `tests/` | pytest-asyncio test suite |

---

## Key Design Decisions

1. **Fully async** — asyncio throughout. No sync blocking calls.
2. **Claude tool_use** — Structured output via tool schemas. No JSON parsing.
3. **Deterministic validator** — Rule engine (<1ms) gates signals before execution.
4. **Order FSM** — State machine prevents invalid order transitions.
5. **ATR position sizing** — Risk-based sizing, never fixed lot sizes.
6. **Circuit breakers** — Daily loss limit auto-stops trading.
7. **APScheduler** — In-process scheduling, no Celery/Redis needed.
8. **asyncpg** — Async Postgres driver, not psycopg2.

---

## Deployment

```bash
# Deploy to Fly.io
flyctl deploy --remote-only

# Check status
flyctl status --app jpvt-trading-bot

# View logs
flyctl logs --app jpvt-trading-bot --no-tail

# Set secrets
flyctl secrets set ANTHROPIC_API_KEY=sk-ant-... --app jpvt-trading-bot
```

Push to `main` triggers auto-deploy via GitHub Actions.

---

## Local Development

```bash
# Install TA-Lib system library
brew install ta-lib

# Install Python dependencies
pip install -r requirements-dev.txt

# Run tests
pytest -v

# Lint
ruff check bot/ tests/

# Type check
mypy bot/

# Run bot locally (requires .env file)
python -m bot.main
```

---

## Analysis Cycle Flow

1. APScheduler triggers `analysis_cycle()` every N minutes
2. For each symbol + timeframe:
   - Layer 1: Fetch OHLCV via CCXT -> compute TA-Lib indicators -> build payload
   - Layer 2: Send payload to Claude (tool_use) -> receive DivergenceSignal
   - Layer 2: Run deterministic validator on signal
   - Layer 4: Risk manager checks portfolio limits
   - Layer 3: Execute order if all checks pass
   - Layer 5: Log + Telegram alert

---

## Trading Modes

| Mode | Exchange Calls | Orders | Use Case |
|------|---------------|--------|----------|
| `dev` | None (mock) | None | Local development, tests |
| `paper` | Real data | Simulated (testnet) | Validation before live |
| `live` | Real data | Real money | Production |

---

## Environment Variables

All config via env vars. See `.env.example` for full list. Critical secrets managed via `flyctl secrets set`.
