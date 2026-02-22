"""Seed dashboard users on startup. Called during schema migration.

Reads user credentials from env vars and upserts into the users table.
Passwords are hashed with bcrypt before storage.
"""

from __future__ import annotations

import bcrypt
from loguru import logger

from bot.config import Settings
from bot.dashboard import queries as dq


async def seed_dashboard_users(pool, settings: Settings) -> None:
    """Create or update dashboard user accounts from env vars."""
    users = []

    if settings.dashboard_user_1_password:
        users.append((
            settings.dashboard_user_1_email,
            settings.dashboard_user_1_password,
            settings.dashboard_user_1_name,
        ))

    if settings.dashboard_user_2_password:
        users.append((
            settings.dashboard_user_2_email,
            settings.dashboard_user_2_password,
            settings.dashboard_user_2_name,
        ))

    if not users:
        logger.info("No dashboard user passwords configured — skipping user seeding")
        return

    for email, password, name in users:
        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(rounds=12),
        ).decode("utf-8")

        await pool.execute(dq.UPSERT_USER, email, password_hash, name)
        logger.info(f"Dashboard user seeded: {email}")
