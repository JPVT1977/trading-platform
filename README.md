# Divergence Trading Bot

Claude AI-powered divergence detection and trade execution system. Detects bullish/bearish divergences across multiple indicators and timeframes, validates signals through a deterministic rule engine, and executes trades with strict risk management.

## Architecture

```
Layer 5: Monitoring     — Loguru logging, Telegram alerts, health checks
Layer 4: Risk Mgmt      — Position sizing, exposure limits, circuit breakers
Layer 3: Execution       — Deterministic engine, order FSM, fill tracking
Layer 2: Intelligence    — Claude API (tool_use), divergence classification
Layer 1: Data            — CCXT market data, TA-Lib indicators
```

## Quick Start

```bash
# Install TA-Lib system library
brew install ta-lib

# Install dependencies
pip install -r requirements-dev.txt

# Copy and configure environment
cp .env.example .env

# Run tests
pytest -v

# Run bot (paper trading mode)
python -m bot.main
```

## Deployment

Deployed on Fly.io (Sydney region). Push to `main` triggers auto-deploy.

```bash
flyctl deploy --remote-only
flyctl logs --app trading-bot --no-tail
```

## Tech Stack

- **Language:** Python 3.13
- **AI:** Claude Sonnet 4.5 (structured tool_use)
- **Data:** CCXT (async) + TA-Lib
- **Database:** PostgreSQL (asyncpg)
- **Scheduling:** APScheduler
- **Hosting:** Fly.io
- **CI/CD:** GitHub Actions

## License

MIT
