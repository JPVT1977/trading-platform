"""Signal outcome tracker — background job that tracks what happened after every signal.

Runs every 5 minutes via APScheduler:
1. Creates outcome rows for signals that don't have one yet
2. Updates unresolved outcomes with checkpoint prices, MFE/MAE, TP/SL hits
3. Marks outcomes as fully_resolved after 24h
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger

from bot.database import queries as q
from bot.database.connection import Database

if TYPE_CHECKING:
    from bot.layer1_data.broker_router import BrokerRouter

# Verdict thresholds (24h return %)
CORRECT_THRESHOLD = 0.5   # above +0.5% = correct
INCORRECT_THRESHOLD = -0.5  # below -0.5% = incorrect


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def track_signal_outcomes(db: Database, router: BrokerRouter) -> None:
    """Main entry point — called by APScheduler every 5 minutes."""
    try:
        created = await _create_missing_outcomes(db)
        updated = await _update_unresolved_outcomes(db, router)
        if created or updated:
            logger.info(f"Outcome tracker: created={created}, updated={updated}")
    except Exception as e:
        logger.error(f"Outcome tracker error: {e}")


async def _create_missing_outcomes(db: Database) -> int:
    """Find signals without outcome rows and create them."""
    rows = await db.pool.fetch(q.SELECT_SIGNALS_WITHOUT_OUTCOMES)
    count = 0
    for row in rows:
        try:
            await db.pool.execute(
                q.INSERT_OUTCOME,
                row["id"],
                float(row["entry_price"]),
                row["direction"],
            )
            count += 1
        except Exception as e:
            logger.warning(f"Failed to create outcome for signal {row['id']}: {e}")
    return count


async def _update_unresolved_outcomes(db: Database, router: BrokerRouter) -> int:
    """Update all unresolved outcomes with checkpoint prices and verdicts."""
    rows = await db.pool.fetch(q.SELECT_UNRESOLVED_OUTCOMES)
    if not rows:
        return 0

    # Group by symbol to minimize API calls
    by_symbol: dict[str, list] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    count = 0
    now = datetime.now(UTC)

    for symbol, outcomes in by_symbol.items():
        # Fetch 1h candles covering the oldest signal to now
        oldest_signal = _ensure_utc(min(o["signal_created_at"] for o in outcomes))
        hours_needed = int((now - oldest_signal).total_seconds() / 3600) + 2
        candle_limit = min(hours_needed, 500)

        try:
            broker = router.get_broker(symbol)
            candles = await broker.fetch_ohlcv(symbol, "1h", limit=candle_limit)
        except Exception as e:
            logger.warning(f"Failed to fetch candles for {symbol}: {e}")
            continue

        if not candles:
            continue

        for outcome in outcomes:
            try:
                await _process_single_outcome(db, outcome, candles, now)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to update outcome {outcome['id']}: {e}")

    return count


async def _process_single_outcome(
    db: Database, outcome, candles: list, now: datetime
) -> None:
    """Process a single outcome row — fill checkpoints, MFE/MAE, TP/SL, verdict."""
    signal_time = _ensure_utc(outcome["signal_created_at"])
    entry_price = float(outcome["entry_price"])
    direction = outcome["direction"]
    is_long = direction == "long"
    elapsed_hours = (now - signal_time).total_seconds() / 3600

    # Filter candles to those after signal time (normalize timezone first)
    relevant = [c for c in candles if _ensure_utc(c.timestamp) >= signal_time]
    if not relevant:
        return

    # --- Checkpoint prices (use candle close nearest to each checkpoint) ---
    checkpoints = {1: "price_1h", 4: "price_4h", 12: "price_12h", 24: "price_24h"}
    prices = {
        "price_1h": outcome["price_1h"],
        "price_4h": outcome["price_4h"],
        "price_12h": outcome["price_12h"],
        "price_24h": outcome["price_24h"],
    }

    for hours, key in checkpoints.items():
        if prices[key] is not None:
            continue  # Already filled
        if elapsed_hours < hours:
            continue  # Not enough time has passed
        target_time = signal_time + timedelta(hours=hours)
        closest = _find_closest_candle(relevant, target_time)
        if closest:
            prices[key] = closest.close

    # --- Returns (positive = signal was correct) ---
    returns = {}
    for hours, key in checkpoints.items():
        ret_key = key.replace("price_", "return_")
        price = prices[key]
        if price is not None and entry_price > 0:
            if is_long:
                returns[ret_key] = ((price - entry_price) / entry_price) * 100
            else:
                returns[ret_key] = ((entry_price - price) / entry_price) * 100
        else:
            returns[ret_key] = outcome.get(ret_key)

    # --- MFE / MAE ---
    mfe_price = outcome["max_favorable_price"]
    mae_price = outcome["max_adverse_price"]

    for c in relevant:
        if is_long:
            best = c.high
            worst = c.low
        else:
            best = c.low
            worst = c.high

        if mfe_price is None:
            mfe_price = best
        else:
            mfe_price = max(best, mfe_price) if is_long else min(best, mfe_price)

        if mae_price is None:
            mae_price = worst
        else:
            mae_price = min(worst, mae_price) if is_long else max(worst, mae_price)

    if mfe_price is not None and entry_price > 0:
        if is_long:
            mfe_pct = ((mfe_price - entry_price) / entry_price) * 100
        else:
            mfe_pct = ((entry_price - mfe_price) / entry_price) * 100
    else:
        mfe_pct = outcome["max_favorable_pct"]

    if mae_price is not None and entry_price > 0:
        if is_long:
            mae_pct = ((mae_price - entry_price) / entry_price) * 100
        else:
            mae_pct = ((entry_price - mae_price) / entry_price) * 100
    else:
        mae_pct = outcome["max_adverse_pct"]

    # --- TP / SL hit detection ---
    tp1_hit = outcome["tp1_hit"]
    tp1_hit_at = outcome["tp1_hit_at"]
    tp2_hit = outcome["tp2_hit"]
    tp2_hit_at = outcome["tp2_hit_at"]
    tp3_hit = outcome["tp3_hit"]
    tp3_hit_at = outcome["tp3_hit_at"]
    sl_hit = outcome["sl_hit"]
    sl_hit_at = outcome["sl_hit_at"]

    tp1 = outcome["take_profit_1"]
    tp2 = outcome["take_profit_2"]
    tp3 = outcome["take_profit_3"]
    sl = outcome["stop_loss"]

    for c in relevant:
        ts = c.timestamp
        if is_long:
            if tp1 and not tp1_hit and c.high >= tp1:
                tp1_hit, tp1_hit_at = True, ts
            if tp2 and not tp2_hit and c.high >= tp2:
                tp2_hit, tp2_hit_at = True, ts
            if tp3 and not tp3_hit and c.high >= tp3:
                tp3_hit, tp3_hit_at = True, ts
            if sl and not sl_hit and c.low <= sl:
                sl_hit, sl_hit_at = True, ts
        else:
            if tp1 and not tp1_hit and c.low <= tp1:
                tp1_hit, tp1_hit_at = True, ts
            if tp2 and not tp2_hit and c.low <= tp2:
                tp2_hit, tp2_hit_at = True, ts
            if tp3 and not tp3_hit and c.low <= tp3:
                tp3_hit, tp3_hit_at = True, ts
            if sl and not sl_hit and c.high >= sl:
                sl_hit, sl_hit_at = True, ts

    # --- Verdict ---
    fully_resolved = elapsed_hours >= 24
    verdict = _compute_verdict(
        tp1_hit, sl_hit, returns.get("return_24h"), fully_resolved
    )

    # --- Persist ---
    await db.pool.execute(
        q.UPDATE_OUTCOME,
        outcome["id"],
        prices["price_1h"], prices["price_4h"],
        prices["price_12h"], prices["price_24h"],
        returns.get("return_1h"), returns.get("return_4h"),
        returns.get("return_12h"), returns.get("return_24h"),
        mfe_price, mae_price, mfe_pct, mae_pct,
        tp1_hit, tp1_hit_at,
        tp2_hit, tp2_hit_at,
        tp3_hit, tp3_hit_at,
        sl_hit, sl_hit_at,
        verdict, fully_resolved,
    )


def _find_closest_candle(candles: list, target_time: datetime):
    """Find the candle closest to the target time."""
    target_utc = _ensure_utc(target_time)
    best = None
    best_diff = None
    for c in candles:
        diff = abs((_ensure_utc(c.timestamp) - target_utc).total_seconds())
        if best_diff is None or diff < best_diff:
            best = c
            best_diff = diff
    return best


def _compute_verdict(
    tp1_hit: bool, sl_hit: bool, return_24h: float | None, fully_resolved: bool
) -> str:
    """Determine verdict: correct / incorrect / partial / pending."""
    if tp1_hit and sl_hit:
        return "partial"
    if tp1_hit:
        return "correct"
    if sl_hit:
        return "incorrect"
    if fully_resolved and return_24h is not None:
        if return_24h > CORRECT_THRESHOLD:
            return "correct"
        if return_24h < INCORRECT_THRESHOLD:
            return "incorrect"
        return "partial"
    return "pending"
