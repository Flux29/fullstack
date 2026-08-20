"""Redis client wrapper.

Provides a class-based Redis client for connection management and operations.
"""

from redis import asyncio as aioredis

from app.core.config import settings


class RedisClient:
    """Redis client wrapper for connection lifecycle management.

    Usage in FastAPI lifespan:
        async with contextmanager():
            redis = RedisClient(settings.REDIS_URL)
            await redis.connect()
            yield {"redis": redis}
            await redis.close()
    """

    def __init__(self, url: str | None = None):
        self.url = url or settings.REDIS_URL
        self.client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis server."""
        self.client = aioredis.from_url(  # type: ignore[no-untyped-call]
            self.url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def close(self) -> None:
        """Close Redis connection."""
        if self.client:
            await self.client.close()
            self.client = None

    async def get(self, key: str) -> str | None:
        """Get a value by key.

        `connect()` sets decode_responses=True, so the value comes back decoded —
        but redis-py's annotation cannot express that and still admits `bytes`.
        Decode explicitly rather than asserting the flag through a type: ignore.
        """
        if not self.client:
            raise RuntimeError("Redis client not connected")
        value: str | bytes | None = await self.client.get(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def set(
        self,
        key: str,
        value: str,
        ttl: int | None = None,
    ) -> None:
        """Set a value with optional TTL (in seconds)."""
        if not self.client:
            raise RuntimeError("Redis client not connected")
        await self.client.set(key, value, ex=ttl)

    async def getdel(self, key: str) -> str | None:
        """Atomically read and delete a key (single-use tokens and codes).

        Same decode caveat as ``get``: decode_responses=True makes the value a
        str, but redis-py's annotation still admits bytes.
        """
        if not self.client:
            raise RuntimeError("Redis client not connected")
        value: str | bytes | None = await self.client.getdel(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def delete(self, key: str) -> int:
        """Delete a key. Returns number of keys deleted."""
        if not self.client:
            raise RuntimeError("Redis client not connected")
        return await self.client.delete(key)  # type: ignore[no-any-return]

    async def incr_with_ttl(self, key: str, ttl: int) -> int:
        """Increment a counter, starting its expiry window on first increment.

        Returns the post-increment value. The TTL is only set when the key is
        new, so the window is fixed from the first event rather than sliding.
        """
        if not self.client:
            raise RuntimeError("Redis client not connected")
        count = int(await self.client.incr(key))
        if count == 1:
            await self.client.expire(key, ttl)
        return count

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self.client:
            raise RuntimeError("Redis client not connected")
        return bool(await self.client.exists(key))

    async def ping(self) -> bool:
        """Ping Redis server. Returns True if connected."""
        if not self.client:
            return False
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    @property
    def raw(self) -> aioredis.Redis:
        """Access the underlying aioredis client for advanced operations."""
        if not self.client:
            raise RuntimeError("Redis client not connected")
        return self.client
