"""Async, cache-aware embedding service for Docker Model Runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from abc import ABC, abstractmethod

from openai import AsyncOpenAI

from app.core.config import settings as app_settings
from app.services.rag.config import RAGSettings
from app.services.rag.embedding_cache import (
    CacheKey,
    EmbeddingCache,
    InputKind,
    make_cache_key,
)
from app.services.rag.models import Document

NORMALIZATION_VERSION = "l2-v1"


def _chunk_texts(document: Document) -> list[str]:
    return [page.chunk_content or "" for page in (document.chunked_pages or [])]


def embedding_fingerprint(settings: RAGSettings) -> str:
    config = settings.embeddings_config
    payload = {
        "dimensions": config.dim,
        "document_instruction": config.document_instruction,
        "model_artifact_revision": config.model_revision,
        "model_request_id": config.model,
        "model_version": config.model_version,
        "normalization": NORMALIZATION_VERSION,
        "query_instruction": config.query_instruction,
        "schema": 1,
    }
    value = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


class BaseEmbeddingProvider(ABC):
    @abstractmethod
    async def embed_texts(self, texts: list[str], *, dimensions: int) -> list[list[float]]:
        """Embed a batch in input order."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release provider resources."""


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI-compatible async provider used by Docker Model Runner."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._owns_client = client is None

    async def embed_texts(self, texts: list[str], *, dimensions: int) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise ValueError(
                f"Embedding provider returned {len(ordered)} vectors for {len(texts)} inputs"
            )
        return [item.embedding for item in ordered]

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.close()


class EmbeddingService:
    """Separates query/document formatting and owns the two-level cache."""

    def __init__(
        self,
        settings: RAGSettings,
        *,
        provider: BaseEmbeddingProvider | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        config = settings.embeddings_config
        if config.dim is None:  # guarded by config validation; keeps the type explicit
            raise ValueError("Embedding dimension is required")
        self.expected_dim = config.dim
        self.model = config.model
        self.model_version = config.model_version
        self.model_revision = config.model_revision
        self.query_instruction = config.query_instruction
        self.document_instruction = config.document_instruction
        self._fingerprint = embedding_fingerprint(settings)
        self.batch_size = config.batch_size
        self.provider = provider or OpenAIEmbeddingProvider(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
        )
        self.cache = cache or EmbeddingCache(
            redis_url=config.cache_url,
            database_url=app_settings.DATABASE_URL,
            ttl_seconds=config.cache_ttl_seconds,
            lock_seconds=config.cache_lock_seconds,
        )
        self._closed = False

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _instruction(self, input_kind: InputKind) -> str:
        return self.query_instruction if input_kind == "query" else self.document_instruction

    @staticmethod
    def _format_text(key: CacheKey, input_kind: InputKind, instruction: str) -> str:
        if input_kind == "query":
            return f"Instruct: {instruction}\nQuery: {key.normalized_input}"
        if instruction:
            return f"Instruct: {instruction}\nDocument: {key.normalized_input}"
        return key.normalized_input

    def _validate_vector(self, vector: list[float]) -> list[float]:
        if len(vector) < self.expected_dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected at least {self.expected_dim}, "
                f"got {len(vector)}"
            )
        truncated = [float(value) for value in vector[: self.expected_dim]]
        if any(not math.isfinite(value) for value in truncated):
            raise ValueError("Embedding provider returned a non-finite value")
        norm = math.sqrt(sum(value * value for value in truncated))
        if norm == 0:
            raise ValueError("Embedding provider returned a zero vector")
        return [value / norm for value in truncated]

    async def _embed(self, values: list[str], input_kind: InputKind) -> list[list[float]]:
        if not values:
            return []
        instruction = self._instruction(input_kind)
        keys = [
            make_cache_key(
                model_id=self.model,
                model_version=self.model_version,
                dimensions=self.expected_dim,
                input_kind=input_kind,
                instruction=instruction,
                value=value,
            )
            for value in values
        ]
        unique_keys = list({key.digest: key for key in keys}.values())
        cached = await self.cache.get_many(unique_keys)
        missing = [key for key in unique_keys if key.digest not in cached]

        owned: list[tuple[CacheKey, str]] = []
        waiting: list[CacheKey] = []
        for key in missing:
            token = await self.cache.acquire_lock(key)
            if token is None:
                waiting.append(key)
            else:
                owned.append((key, token))

        try:
            if waiting:
                for _ in range(10):
                    await asyncio.sleep(0.2)
                    completed = await self.cache.get_many(waiting)
                    cached.update(completed)
                    waiting = [key for key in waiting if key.digest not in cached]
                    if not waiting:
                        break

            compute_keys = [key for key, _ in owned] + waiting
            for offset in range(0, len(compute_keys), self.batch_size):
                batch_keys = compute_keys[offset : offset + self.batch_size]
                formatted = [self._format_text(key, input_kind, instruction) for key in batch_keys]
                raw_vectors = await self.provider.embed_texts(
                    formatted, dimensions=self.expected_dim
                )
                vectors = [self._validate_vector(vector) for vector in raw_vectors]
                entries = list(zip(batch_keys, vectors, strict=True))
                await self.cache.put_many(
                    entries,
                    model_id=self.model,
                    model_version=self.model_version,
                    model_revision=self.model_revision,
                    dimensions=self.expected_dim,
                    input_kind=input_kind,
                )
                cached.update({key.digest: vector for key, vector in entries})
        finally:
            await asyncio.gather(
                *(self.cache.release_lock(key, token) for key, token in owned),
                return_exceptions=True,
            )

        return [cached[key.digest] for key in keys]

    async def embed_query(self, query: str) -> list[float]:
        return (await self._embed([query], "query"))[0]

    async def embed_document(self, document: Document) -> list[list[float]]:
        return await self._embed(_chunk_texts(document), "document")

    async def warmup(self) -> None:
        await self.embed_query("embedding service warmup")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.provider.aclose()
        await self.cache.aclose()


_shared_service: EmbeddingService | None = None


def get_embedding_service(settings: RAGSettings) -> EmbeddingService:
    """Return the single embedding service owned by this process."""
    global _shared_service
    if _shared_service is None:
        _shared_service = EmbeddingService(settings)
    elif _shared_service.fingerprint != embedding_fingerprint(settings):
        raise RuntimeError("Embedding service was already initialized with another fingerprint")
    return _shared_service


async def close_embedding_service() -> None:
    global _shared_service
    if _shared_service is not None:
        await _shared_service.aclose()
        _shared_service = None
