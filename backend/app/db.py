"""asyncpg connection pool, created at app startup and shared across requests."""
from __future__ import annotations

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    """Open the shared connection pool. Called once on startup."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.dsn,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=30,
        )


async def disconnect() -> None:
    """Close the pool. Called once on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    return _pool


async def fetch(query: str, *args) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args) -> asyncpg.Record | None:
    async with pool().acquire() as conn:
        return await conn.fetchrow(query, *args)
