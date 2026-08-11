"""RAG module facade — the package root is the public surface.

Callers import from ``app.services.rag``; sub-modules are package-internal.
Exports resolve lazily (PEP 562) so importing the facade has no import-time
side effects — the app, worker, and CLI each load only the sub-modules
behind the names they actually use.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.rag.config import (
        DocumentExtensions,
        DocumentParser,
        EmbeddingsConfig,
        PdfParser,
        RAGSettings,
        RerankerConfig,
        get_supported_formats,
    )
    from app.services.rag.connectors import CONNECTOR_REGISTRY
    from app.services.rag.documents import DoclingServeParser, DocumentProcessor
    from app.services.rag.embeddings import (
        EmbeddingService,
        close_embedding_service,
        get_embedding_service,
    )
    from app.services.rag.ingestion import IngestionService
    from app.services.rag.reranker import (
        RerankService,
        close_rerank_service,
        get_rerank_service,
    )
    from app.services.rag.retrieval import BaseRetrievalService, RetrievalService
    from app.services.rag.sources import GoogleDriveSource, S3Source
    from app.services.rag.vectorstore import BaseVectorStore, PgVectorStore

__all__ = [
    "CONNECTOR_REGISTRY",
    "BaseRetrievalService",
    "BaseVectorStore",
    "DoclingServeParser",
    "DocumentExtensions",
    "DocumentParser",
    "DocumentProcessor",
    "EmbeddingService",
    "EmbeddingsConfig",
    "GoogleDriveSource",
    "IngestionService",
    "PdfParser",
    "PgVectorStore",
    "RAGSettings",
    "RerankService",
    "RerankerConfig",
    "RetrievalService",
    "S3Source",
    "close_embedding_service",
    "close_rerank_service",
    "get_embedding_service",
    "get_rerank_service",
    "get_supported_formats",
]

_EXPORTS = {
    "CONNECTOR_REGISTRY": "connectors",
    "BaseRetrievalService": "retrieval",
    "BaseVectorStore": "vectorstore",
    "DoclingServeParser": "documents",
    "DocumentExtensions": "config",
    "DocumentParser": "config",
    "DocumentProcessor": "documents",
    "EmbeddingService": "embeddings",
    "EmbeddingsConfig": "config",
    "GoogleDriveSource": "sources",
    "IngestionService": "ingestion",
    "PdfParser": "config",
    "PgVectorStore": "vectorstore",
    "RAGSettings": "config",
    "RerankService": "reranker",
    "RerankerConfig": "config",
    "RetrievalService": "retrieval",
    "S3Source": "sources",
    "close_embedding_service": "embeddings",
    "close_rerank_service": "reranker",
    "get_embedding_service": "embeddings",
    "get_rerank_service": "reranker",
    "get_supported_formats": "config",
}


def __getattr__(name: str) -> Any:
    try:
        submodule = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return getattr(import_module(f"{__name__}.{submodule}"), name)
