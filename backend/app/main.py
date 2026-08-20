# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""FastAPI application entry point."""

import logging
import inspect
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import TypedDict

from fastapi import FastAPI
from fastapi_pagination import add_pagination
import fastapi_pagination.api as pagination_api
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.db.session import close_db
from app.core.logfire_setup import instrument_app, setup_logfire
from app.core.logfire_setup import instrument_asyncpg
from app.core.logfire_setup import instrument_redis
from app.core.logfire_setup import instrument_httpx
from app.core.logfire_setup import instrument_pydantic_ai
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.db.todo_pool import close_todo_pool, init_todo_pool
from app.core.cache import setup_cache
from app.clients.redis import RedisClient
from app.services.rag import (
    BaseVectorStore,
    EmbeddingService,
    PgVectorStore,
    RerankService,
    close_embedding_service,
    close_rerank_service,
    get_embedding_service,
    get_rerank_service,
)
from app.services.file_upload import close_chat_docling_parser
from app.admin import setup_admin

logger = logging.getLogger(__name__)


class LifespanState(TypedDict, total=False):
    """Lifespan state - resources available via request.state."""

    redis: RedisClient
    embedding_service: EmbeddingService
    vector_store: BaseVectorStore
    rerank_service: RerankService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[LifespanState, None]:
    """Application lifespan - startup and shutdown events.

    Resources yielded here are available via request.state in route handlers.
    See: https://asgi.readthedocs.io/en/latest/specs/lifespan.html#lifespan-state
    """
    state: LifespanState = {}
    setup_logfire()
    instrument_asyncpg()
    instrument_redis()
    instrument_httpx()
    instrument_pydantic_ai()
    redis_client = RedisClient()
    await redis_client.connect()
    state["redis"] = redis_client

    if settings.ENABLE_DEEP_RESEARCH:
        await init_todo_pool()
    setup_cache(redis_client)
    embedder: EmbeddingService | None = None
    try:
        embedder = get_embedding_service(settings.rag)
        await embedder.warmup()
        state["embedding_service"] = embedder
    except Exception as e:
        logger.error("Embedding service warmup failed: %s. RAG will not be available.", e)
        with suppress(Exception):
            await close_embedding_service()
        embedder = None
    # Initialize and warm up the Docker Model Runner reranker.
    try:
        rerank_service = get_rerank_service(settings.rag)
        await rerank_service.warmup()
        state["rerank_service"] = rerank_service
    except Exception as e:
        logger.warning("Reranker warmup failed: %s. Reranking will be disabled.", e)
    if embedder is not None:
        try:
            vector_store = PgVectorStore(settings=settings.rag, embedding_service=embedder)
            state["vector_store"] = vector_store
        except Exception as e:
            logger.error("pgvector connection failed: %s. Vector store will not be available.", e)
    yield state
    if "vector_store" in state:
        with suppress(Exception):
            await state["vector_store"].aclose()  # type: ignore[attr-defined]
    with suppress(Exception):
        await close_embedding_service()
    with suppress(Exception):
        await close_rerank_service()
    with suppress(Exception):
        await close_chat_docling_parser()
    if settings.ENABLE_DEEP_RESEARCH:
        await close_todo_pool()
    if "redis" in state:
        await state["redis"].close()

    await close_db()


SHOW_DOCS_ENVIRONMENTS = ("local", "staging", "development")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    show_docs = settings.ENVIRONMENT in SHOW_DOCS_ENVIRONMENTS
    openapi_url = f"{settings.API_V1_STR}/openapi.json" if show_docs else None
    docs_url = "/docs" if show_docs else None
    redoc_url = "/redoc" if show_docs else None

    openapi_tags = [
        {
            "name": "health",
            "description": "Health check endpoints for monitoring and Kubernetes probes",
        },
        {
            "name": "auth",
            "description": "Authentication endpoints - login, register, token refresh",
        },
        {
            "name": "users",
            "description": "User management endpoints",
        },
        {
            "name": "oauth",
            "description": "OAuth2 social login endpoints (Google, etc.)",
        },
        {
            "name": "sessions",
            "description": "Session management - view and manage active login sessions",
        },
        {
            "name": "conversations",
            "description": "AI conversation persistence - manage chat history",
        },
        {
            "name": "webhooks",
            "description": "Webhook management - subscribe to events and manage deliveries",
        },
        {
            "name": "agent",
            "description": "AI agent WebSocket endpoint for real-time chat",
        },
        {
            "name": "rag",
            "description": "Retrieval Augmented Generation endpoints",
        },
    ]

    setup_logging()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        summary="FastAPI application with Logfire observability",
        description="""
Harness

## Features
- **Authentication**: JWT-based authentication with refresh tokens
- **API Key**: Header-based API key authentication
- **Database**: Async database operations
- **Redis**: Caching and session storage
- **AI Agent**: PydanticAI-powered conversational assistant
- **Observability**: Logfire integration for tracing and monitoring
- **RAG**: Retrieval Augmented Generation with Milvus and LangChain

## Documentation

- [Swagger UI](/docs) - Interactive API documentation
- [ReDoc](/redoc) - Alternative documentation view
        """.strip(),
        version="0.1.0",
        openapi_url=openapi_url,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_tags=openapi_tags,
        contact={
            "name": "Steven",
            "email": "stepoldev@gmail.com",
        },
        license_info={
            "name": "MIT",
            "identifier": "MIT",
        },
        lifespan=lifespan,
    )
    # setup_logfire() is also called from the lifespan for the runtime app, but
    # we call it here too so that import-time test clients (which never run
    # lifespan) silence the "configure first" warning. setup_logfire() is
    # idempotent via a module-level guard in logfire_setup.py.
    setup_logfire()
    instrument_app(app)

    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS,
    )

    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

    # Added last so it wraps outermost: every response — including CORS
    # short-circuits and exception-handler responses — carries the headers.
    # Routes that set their own (e.g. the file preview endpoint) win via
    # setdefault. HSTS only in production, where TLS terminates at the edge.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.ENVIRONMENT == "production")

    ADMIN_ALLOWED_ENVIRONMENTS = ["development", "local", "staging"]

    if settings.ENVIRONMENT in ADMIN_ALLOWED_ENVIRONMENTS:
        setup_admin(app)

    app.include_router(api_router, prefix=settings.API_V1_STR)

    # fastapi-pagination 0.15.16 detects FastAPI's private body helper as the
    # future body_params signature even on 0.135/0.136, where it still accepts
    # flat_dependant. Select the installed signature instead of version guessing.
    if "body_params" not in inspect.signature(pagination_api.get_body_field).parameters:
        pagination_api._get_body_field_new_signature = False  # type: ignore[invalid-assignment]
    add_pagination(app)

    return app


app = create_app()
