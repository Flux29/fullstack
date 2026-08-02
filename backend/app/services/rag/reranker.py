"""Reranker implementations for RAG retrieval quality improvement."""

import asyncio
import logging
import math
import time
from abc import ABC, abstractmethod

import httpx

from app.services.rag.config import RAGSettings, RerankerConfig
from app.services.rag.models import SearchResult

logger = logging.getLogger(__name__)


class BaseReranker(ABC):
    """Abstract base for reranker providers."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def warmup(self) -> None: ...

    @abstractmethod
    async def aclose(self) -> None: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class DockerModelRunnerReranker(BaseReranker):
    """Rerank documents with Docker Model Runner's native ``/rerank`` API."""

    def __init__(
        self,
        config: RerankerConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model_name = config.model
        self.max_retries = config.max_retries
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_seconds),
        )

    @property
    def name(self) -> str:
        return f"DockerModelRunnerReranker({self.model_name})"

    async def _request(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        request = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post("/rerank", json=request)
                response.raise_for_status()
                break
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                transient = status in {404, 408, 409, 425, 429} or (
                    status is not None and status >= 500
                )
                if attempt >= self.max_retries or (
                    isinstance(exc, httpx.HTTPStatusError) and not transient
                ):
                    raise
                delay = min(0.5 * (2**attempt), 4.0)
                logger.warning(
                    "[RERANKER] DMR request unavailable (status=%s); retrying in %.1fs",
                    status,
                    delay,
                )
                await asyncio.sleep(delay)
        if response is None:  # pragma: no cover - loop always executes at least once
            raise RuntimeError("Docker Model Runner rerank request was not attempted")
        payload = response.json()
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("Docker Model Runner rerank response has no results list")

        scored: list[tuple[int, float]] = []
        seen: set[int] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                raise ValueError("Docker Model Runner returned an invalid rerank item")
            index = item.get("index")
            score = item.get("relevance_score")
            if not isinstance(index, int) or index < 0 or index >= len(documents):
                raise ValueError("Docker Model Runner returned an invalid document index")
            if index in seen:
                raise ValueError("Docker Model Runner returned a duplicate document index")
            if not isinstance(score, int | float) or not math.isfinite(float(score)):
                raise ValueError("Docker Model Runner returned a non-finite relevance score")
            seen.add(index)
            scored.append((index, float(score)))
        return scored

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        if not results or top_k <= 0:
            return []

        started = time.monotonic()
        try:
            scored = await self._request(
                query=query,
                documents=[result.content for result in results],
                top_n=min(top_k, len(results)),
            )
            reranked = [
                SearchResult(
                    content=results[index].content,
                    score=score,
                    metadata=results[index].metadata,
                    parent_doc_id=results[index].parent_doc_id,
                )
                for index, score in scored
            ]
            reranked.sort(key=lambda result: result.score, reverse=True)
            logger.info(
                "[RERANKER] Docker Model Runner reranked %d documents in %.3fs",
                len(results),
                time.monotonic() - started,
            )
            return reranked[:top_k]
        except Exception:
            logger.exception(
                "[RERANKER] Docker Model Runner reranking failed; preserving vector order"
            )
            return results[:top_k]

    async def warmup(self) -> None:
        await self._request(
            query="Docker Model Runner reranker warmup",
            documents=["Reranking service warmup document."],
            top_n=1,
        )
        logger.info("[RERANKER] Docker Model Runner ready: %s", self.model_name)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class RerankService:
    """Orchestrate reranking with the configured provider."""

    def __init__(
        self,
        settings: RAGSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        config = settings.reranker_config
        self._reranker: BaseReranker | None = None
        if config.provider == "docker_model_runner":
            self._reranker = DockerModelRunnerReranker(config, client=client)
            logger.info("[RERANKER] Using Docker Model Runner reranker")

        if self._reranker is None:
            logger.info("[RERANKER] Reranking disabled")

    @property
    def reranker(self) -> BaseReranker | None:
        return self._reranker

    @property
    def is_enabled(self) -> bool:
        return self._reranker is not None

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        if not self._reranker:
            return results[:top_k]
        return await self._reranker.rerank(query, results, top_k)

    async def warmup(self) -> None:
        if self._reranker:
            await self._reranker.warmup()

    async def aclose(self) -> None:
        if self._reranker:
            await self._reranker.aclose()


_shared_service: RerankService | None = None


def get_rerank_service(settings: RAGSettings) -> RerankService:
    """Return one reusable reranker service per process."""
    global _shared_service
    if _shared_service is None:
        _shared_service = RerankService(settings)
    return _shared_service


async def close_rerank_service() -> None:
    """Close and clear the process-level reranker service."""
    global _shared_service
    if _shared_service is not None:
        await _shared_service.aclose()
        _shared_service = None
