"""Two-level cache for normalized embedding vectors."""

from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

InputKind = Literal["query", "document"]


def normalize_embedding_input(value: str) -> str:
    """Apply the version-one, code-safe input normalization contract."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


@dataclass(frozen=True, slots=True)
class CacheKey:
    digest: str
    redis_key: str
    instruction_hash: str
    normalized_input_hash: str
    normalized_input: str


def make_cache_key(
    *,
    model_id: str,
    model_version: str,
    dimensions: int,
    input_kind: InputKind,
    instruction: str,
    value: str,
) -> CacheKey:
    """Build the canonical, unambiguous v1 cache key."""
    normalized = normalize_embedding_input(value)
    payload = {
        "dimensions": dimensions,
        "input_kind": input_kind,
        "instruction": instruction,
        "model_id": model_id,
        "model_version": model_version,
        "normalized_input": normalized,
        "schema": 1,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    digest = hashlib.sha256(encoded).hexdigest()
    return CacheKey(
        digest=digest,
        redis_key=f"emb:v1:{digest}",
        instruction_hash=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        normalized_input_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        normalized_input=normalized,
    )


class EmbeddingCache:
    """Redis L1 backed by a durable PostgreSQL pgvector L2."""

    def __init__(
        self,
        *,
        redis_url: str,
        database_url: str,
        ttl_seconds: int,
        lock_seconds: int,
        engine: AsyncEngine | None = None,
        redis: Redis | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.lock_seconds = lock_seconds
        self._owns_engine = engine is None
        self._owns_redis = redis is None
        self.engine = engine or create_async_engine(database_url, pool_pre_ping=True)
        self.redis: Redis = redis or Redis.from_url(redis_url, decode_responses=True)

    @staticmethod
    def _serialize(vector: list[float]) -> str:
        return json.dumps(vector, allow_nan=False, separators=(",", ":"))

    @staticmethod
    def _deserialize(value: bytes | str) -> list[float]:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("Cached embedding is not a JSON array")
        return [float(item) for item in parsed]

    async def get_many(self, keys: list[CacheKey]) -> dict[str, list[float]]:
        if not keys:
            return {}

        cached: dict[str, list[float]] = {}
        redis_values = await self.redis.mget([key.redis_key for key in keys])
        for key, value in zip(keys, redis_values, strict=True):
            if value is not None:
                cached[key.digest] = self._deserialize(value)

        misses = [key for key in keys if key.digest not in cached]
        if not misses:
            return cached

        query = text(
            """
            SELECT cache_key, embedding::text AS embedding
            FROM embedding_cache
            WHERE cache_key IN :keys
            """
        ).bindparams(bindparam("keys", expanding=True))
        async with self.engine.begin() as connection:
            rows = (await connection.execute(query, {"keys": [k.digest for k in misses]})).all()
            if rows:
                await connection.execute(
                    text(
                        """
                        UPDATE embedding_cache
                        SET last_accessed_at = now(), hit_count = hit_count + 1
                        WHERE cache_key IN :keys
                        """
                    ).bindparams(bindparam("keys", expanding=True)),
                    {"keys": [row.cache_key for row in rows]},
                )

        redis_updates: dict[str, str] = {}
        key_by_digest = {key.digest: key for key in misses}
        for row in rows:
            vector = self._deserialize(row.embedding)
            cached[row.cache_key] = vector
            redis_updates[key_by_digest[row.cache_key].redis_key] = self._serialize(vector)
        if redis_updates:
            pipeline = self.redis.pipeline(transaction=False)
            for redis_key, value in redis_updates.items():
                pipeline.set(redis_key, value, ex=self.ttl_seconds)
            await pipeline.execute()
        return cached

    async def put_many(
        self,
        entries: list[tuple[CacheKey, list[float]]],
        *,
        model_id: str,
        model_version: str,
        model_revision: str,
        dimensions: int,
        input_kind: InputKind,
    ) -> None:
        if not entries:
            return
        statement = text(
            """
            INSERT INTO embedding_cache (
                cache_key, model_id, model_version, model_revision, dimensions,
                input_kind, instruction_hash, normalized_input_hash, embedding
            ) VALUES (
                :cache_key, :model_id, :model_version, :model_revision, :dimensions,
                :input_kind, :instruction_hash, :normalized_input_hash,
                CAST(:embedding AS vector)
            )
            ON CONFLICT (cache_key) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                last_accessed_at = now()
            """
        )
        params = [
            {
                "cache_key": key.digest,
                "model_id": model_id,
                "model_version": model_version,
                "model_revision": model_revision,
                "dimensions": dimensions,
                "input_kind": input_kind,
                "instruction_hash": key.instruction_hash,
                "normalized_input_hash": key.normalized_input_hash,
                "embedding": self._serialize(vector),
            }
            for key, vector in entries
        ]
        async with self.engine.begin() as connection:
            await connection.execute(statement, params)

        pipeline = self.redis.pipeline(transaction=False)
        for key, vector in entries:
            pipeline.set(key.redis_key, self._serialize(vector), ex=self.ttl_seconds)
        await pipeline.execute()

    async def acquire_lock(self, key: CacheKey) -> str | None:
        token = secrets.token_hex(16)
        acquired = await self.redis.set(
            f"{key.redis_key}:lock", token, ex=self.lock_seconds, nx=True
        )
        return token if acquired else None

    async def release_lock(self, key: CacheKey, token: str) -> None:
        await self.redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
              return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            f"{key.redis_key}:lock",
            token,
        )

    async def aclose(self) -> None:
        if self._owns_redis:
            await self.redis.aclose()
        if self._owns_engine:
            await self.engine.dispose()
