"""Focused tests for Model Runner, caching, Docling, and fingerprint safety."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest

from app.core.exceptions import ValidationError
from app.services.rag.config import (
    DOCLING_FORMATS,
    EmbeddingsConfig,
    RAGSettings,
    RerankerConfig,
)
from app.services.rag.documents import DoclingServeParser
from app.services.rag.embedding_cache import CacheKey, make_cache_key
from app.services.rag.embeddings import BaseEmbeddingProvider, EmbeddingService
from app.services.rag.models import (
    Document,
    DocumentMetadata,
    DocumentPage,
    DocumentPageChunk,
    SearchResult,
)
from app.services.rag.reranker import DockerModelRunnerReranker
from app.services.rag.retrieval import RetrievalService


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, list[float]] = {}

    async def get_many(self, keys: list[CacheKey]) -> dict[str, list[float]]:
        return {key.digest: self.values[key.digest] for key in keys if key.digest in self.values}

    async def put_many(self, entries: list[tuple[CacheKey, list[float]]], **_: Any) -> None:
        self.values.update({key.digest: vector for key, vector in entries})

    async def acquire_lock(self, key: CacheKey) -> str:
        return key.digest

    async def release_lock(self, key: CacheKey, token: str) -> None:
        assert token == key.digest

    async def aclose(self) -> None:
        pass


class RecordingProvider(BaseEmbeddingProvider):
    def __init__(self, vector: list[float] | None = None) -> None:
        self.calls: list[tuple[list[str], int]] = []
        self.vector = vector or [1.0] * 2560

    async def embed_texts(self, texts: list[str], *, dimensions: int) -> list[list[float]]:
        self.calls.append((texts, dimensions))
        return [self.vector[:] for _ in texts]

    async def aclose(self) -> None:
        pass


def rag_settings(**embedding_overrides: Any) -> RAGSettings:
    return RAGSettings(
        embeddings_config=EmbeddingsConfig(
            dim=1024,
            query_instruction="retrieve passages",
            **embedding_overrides,
        )
    )


def test_cache_key_is_canonical_and_separates_input_kind() -> None:
    common = {
        "model_id": "model",
        "model_version": "version",
        "dimensions": 1024,
        "instruction": "instruction",
    }
    crlf = make_cache_key(**common, input_kind="query", value="  cafe\u0301\r\ncode  ")
    normalized = make_cache_key(**common, input_kind="query", value="caf\u00e9\ncode")
    document = make_cache_key(**common, input_kind="document", value="caf\u00e9\ncode")
    assert crlf.digest == normalized.digest
    assert crlf.redis_key.startswith("emb:v1:")
    assert document.digest != normalized.digest


@pytest.mark.anyio
async def test_embedding_batches_deduplicates_truncates_normalizes_and_caches() -> None:
    provider = RecordingProvider()
    cache = MemoryCache()
    service = EmbeddingService(rag_settings(batch_size=2), provider=provider, cache=cache)  # type: ignore[arg-type]
    document = Document(
        pages=[DocumentPage(page_num=1, content="alpha")],
        chunked_pages=[
            DocumentPageChunk(page_num=1, content="alpha", chunk_content="alpha"),
            DocumentPageChunk(page_num=1, content="alpha", chunk_content="alpha"),
            DocumentPageChunk(page_num=1, content="beta", chunk_content="beta"),
        ],
        metadata=DocumentMetadata(filename="a.txt", filesize=1, filetype="txt"),
    )

    vectors = await service.embed_document(document)
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == ["alpha", "beta"]
    assert provider.calls[0][1] == 1024
    assert len(vectors) == 3 and vectors[0] == vectors[1]
    assert len(vectors[0]) == 1024
    assert math.isclose(math.sqrt(sum(value * value for value in vectors[0])), 1.0)

    assert await service.embed_document(document) == vectors
    assert len(provider.calls) == 1
    query = await service.embed_query("alpha")
    assert len(provider.calls) == 2
    assert provider.calls[-1][0] == ["Instruct: retrieve passages\nQuery: alpha"]
    assert query == vectors[0]


@pytest.mark.anyio
@pytest.mark.parametrize("vector", [[1.0] * 100, [float("nan")] * 1024, [0.0] * 1024])
async def test_embedding_rejects_invalid_vectors(vector: list[float]) -> None:
    service = EmbeddingService(
        rag_settings(),
        provider=RecordingProvider(vector),
        cache=MemoryCache(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError):
        await service.embed_query("invalid")


def test_embedding_fingerprint_changes_with_instruction_and_revision() -> None:
    first = EmbeddingService(
        rag_settings(model_revision="one"),
        provider=RecordingProvider(),
        cache=MemoryCache(),  # type: ignore[arg-type]
    )
    second = EmbeddingService(
        rag_settings(model_revision="two"),
        provider=RecordingProvider(),
        cache=MemoryCache(),  # type: ignore[arg-type]
    )
    assert first.fingerprint != second.fingerprint


@pytest.mark.anyio
async def test_docling_parser_uses_v1_api_and_preserves_page_metadata(tmp_path: Any) -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        payload = {
            "status": "success",
            "errors": [],
            "document": {
                "md_content": "page one<!-- fullstack-docling-page-break -->page two",
                "json_content": {
                    "version": "1.10.0",
                    "origin": {"filename": "sample.pdf", "mimetype": "application/pdf"},
                    "pages": {"1": {"page_no": 1}, "2": {"page_no": 2}},
                    "texts": [
                        {
                            "self_ref": "#/texts/0",
                            "label": "text",
                            "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 2}}],
                        }
                    ],
                    "tables": [
                        {
                            "self_ref": "#/tables/0",
                            "label": "table",
                            "prov": [{"page_no": 2, "bbox": {"l": 3, "t": 4}}],
                        }
                    ],
                },
            },
        }
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docling")
    parser = DoclingServeParser(base_url="http://unused", client=client)
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-test")
    document = await parser.parse(path)

    assert seen_request is not None and seen_request.url.path == "/v1/convert/file"
    assert [page.content for page in document.pages] == ["page one", "page two"]
    assert document.pages[0].metadata["docling"]["bounding_boxes"]
    assert document.pages[1].metadata["docling"]["tables"][0]["self_ref"] == "#/tables/0"
    assert {".pdf", ".docx", ".pptx", ".xlsx", ".png"}.issubset(DOCLING_FORMATS)
    await client.aclose()


class MismatchStore:
    async def search(self, **_: Any) -> list[Any]:
        raise ValidationError(code="EMBEDDING_FINGERPRINT_MISMATCH", message="mismatch")


@pytest.mark.anyio
async def test_multi_collection_retrieval_does_not_hide_fingerprint_mismatch() -> None:
    service = RetrievalService(MismatchStore(), rag_settings())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await service.retrieve_multi("query", ["one", "two"])


def test_cache_vectors_are_strict_json() -> None:
    assert json.loads(json.dumps([0.1, 0.2], allow_nan=False)) == [0.1, 0.2]


@pytest.mark.anyio
async def test_docker_model_runner_reranker_uses_native_api() -> None:
    seen_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": seen_payload["model"],
                "results": [
                    {"index": 1, "relevance_score": 2.5},
                    {"index": 0, "relevance_score": -0.5},
                ],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://model-runner",
    )
    config = RerankerConfig(
        model="huggingface.co/example/reranker:Q8_0",
        base_url="http://unused",
    )
    reranker = DockerModelRunnerReranker(config, client=client)
    results = [
        SearchResult(content="first", score=0.9, metadata={"rank": 1}),
        SearchResult(content="second", score=0.2, metadata={"rank": 2}),
    ]

    reranked = await reranker.rerank("query", results, top_k=2)

    assert seen_payload == {
        "model": "huggingface.co/example/reranker:Q8_0",
        "query": "query",
        "documents": ["first", "second"],
        "top_n": 2,
    }
    assert [(result.content, result.score) for result in reranked] == [
        ("second", 2.5),
        ("first", -0.5),
    ]
    assert reranked[0].metadata == {"rank": 2}
    await client.aclose()
