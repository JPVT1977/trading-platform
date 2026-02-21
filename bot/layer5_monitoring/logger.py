from __future__ import annotations

import sys

from loguru import logger

from bot.config import Settings


def setup_logger(settings: Settings) -> logger.__class__:
    """Configure Loguru with structured output and file rotation."""

    # Remove default handler
    logger.remove()

    # Console handler — human-readable
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File handler — JSON structured, rotated daily, kept 30 days
    logger.add(
        "logs/trading-bot_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level} | {name}:{function}:{line} | {message}",
        rotation="00:00",
        retention="30 days",
        compression="gz",
        serialize=True,
    )

    logger.info(
        f"Logger initialised (level={settings.log_level}, mode={settings.trading_mode.value})"
    )

    return logger
