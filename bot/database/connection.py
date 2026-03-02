from __future__ import annotations

from pathlib import Path

import asyncpg
from loguru import logger

from bot.config import Settings


class Database:
    """asyncpg connection pool with auto-migration on startup."""

    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.database_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create connection pool and run schema migration."""
        logger.info("Connecting to database...")

        # Fly Postgres uses WireGuard (6PN) for network encryption — not
        # application-level TLS. Forcing ssl= here causes ConnectionResetError.
        # The sslmode parameter in the DATABASE_URL controls SSL negotiation.
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )

        # Run schema migration on startup
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text()
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql)

        logger.info("Database connected and schema migrated")

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database disconnected")

    @property
    def pool(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("Database not connected — call connect() first")
        return self._pool
