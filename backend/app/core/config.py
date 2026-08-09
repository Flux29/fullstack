"""Application configuration using Pydantic BaseSettings."""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationInfo, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, Field


# Same slug rule as a user connection (app/schemas/mcp_connection.py). The name
# becomes the server's tool prefix in the agent, so an unconstrained name could
# collapse two servers onto one prefix — and the second would then be dropped
# from every chat turn. Reject it at startup instead.
MCP_SERVER_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
GITHUB_MCP_READ_ONLY_TOOLS = frozenset(
    {
        "get_file_contents",
        "issue_read",
        "list_commits",
        "pull_request_read",
        "search_code",
        "search_issues",
    }
)


class McpServerConfig(BaseModel):
    """One deployment-managed MCP server (see MCP_SERVERS below)."""

    name: str = Field(pattern=MCP_SERVER_NAME_PATTERN)
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    auth: Literal["github"] | None = None
    # None = expose every tool the server offers.
    allowed_tools: list[str] | None = None


def find_env_file() -> Path | None:
    """Find .env file in current or parent directories."""
    current = Path.cwd()
    for path in [current, current.parent]:
        env_file = path / ".env"
        if env_file.exists():
            return env_file
    return None


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=find_env_file(),
        env_ignore_empty=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "fullstack"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    DB_ECHO: bool = (
        False  # Set DB_ECHO=true to log SQL queries (latency + log-noise drain by default)
    )
    ENVIRONMENT: Literal["development", "local", "staging", "production"] = "local"
    TIMEZONE: str = "EDT"  # IANA timezone (e.g. "UTC", "Europe/Warsaw", "America/New_York")
    MODELS_CACHE_DIR: Path = Path("./models_cache")
    MEDIA_DIR: Path = Path("./media")
    MAX_UPLOAD_SIZE_MB: int = 50  # Max file upload size in MB
    # Soft per-org storage cap surfaced on /billing — not enforced yet (5 GB).
    STORAGE_SOFT_LIMIT_BYTES: int = 5 * 1024 * 1024 * 1024

    LOGFIRE_TOKEN: str | None = None
    LOGFIRE_SERVICE_NAME: str = "fullstack"
    LOGFIRE_ENVIRONMENT: str = "development"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "fullstack"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        """Build async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Build sync PostgreSQL connection URL (for Alembic)."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: ValidationInfo) -> str:
        """Validate SECRET_KEY is secure in production."""
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if v == "change-me-in-production-use-openssl-rand-hex-32" and env == "production":
            raise ValueError(
                "SECRET_KEY must be changed in production! "
                "Generate a secure key with: openssl rand -hex 32"
            )
        return v

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 minutes
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ALGORITHM: str = "HS256"

    # Public URL of the frontend; used to build OAuth redirect targets and
    # Stripe checkout/portal return URLs. Always declared (not gated) because
    # the billing model_validator references it unconditionally.
    FRONTEND_URL: str = "http://localhost:3000"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8100/api/v1/oauth/google/callback"

    # Static Web OAuth client used for direct Gmail/Calendar integrations.
    # Tokens remain per-user and encrypted in PostgreSQL; no refresh token
    # belongs in the process environment.
    GOOGLE_API_CLIENT_ID: str = ""
    GOOGLE_API_CLIENT_SECRET: SecretStr = SecretStr("")
    # Compatibility with the preview-MCP variable names used by earlier builds.
    GOOGLE_WORKSPACE_MCP_CLIENT_ID: str = ""
    GOOGLE_WORKSPACE_MCP_CLIENT_SECRET: SecretStr = SecretStr("")
    # Compatibility with names shipped in earlier environment examples.
    # Prefer GOOGLE_API_* for new installations.
    GOOGLE_DRIVE_CLIENT_ID: str = ""
    GOOGLE_DRIVE_CLIENT_SECRET: SecretStr = SecretStr("")

    API_KEY: str = "change-me-in-production"
    API_KEY_HEADER: str = "X-API-Key"

    @field_validator("API_KEY")
    @classmethod
    def validate_api_key(cls, v: str, info: ValidationInfo) -> str:
        """Validate API_KEY is set in production."""
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if v == "change-me-in-production" and env == "production":
            raise ValueError(
                "API_KEY must be changed in production! "
                "Generate a secure key with: openssl rand -hex 32"
            )
        return v

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0

    @field_validator("REDIS_PASSWORD")
    @classmethod
    def validate_redis_password(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Require a non-placeholder Redis credential in production."""
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if env == "production" and (not v or v == "change-me-in-production"):
            raise ValueError("REDIS_PASSWORD must be set to a strong production secret")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_URL(self) -> str:
        """Build Redis connection URL."""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    TASKIQ_BROKER_URL: str = "redis://localhost:6379/1"
    TASKIQ_RESULT_BACKEND: str = "redis://localhost:6379/2"
    TASKIQ_MAX_ASYNC_TASKS: int = 1

    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET: str = "fullstack"
    S3_REGION: str = "us-east-1"
    OPENROUTER_API_KEY: str = ""
    AI_MODEL: str = "anthropic/claude-opus-4-7"
    AI_TEMPERATURE: float = 0.7
    AI_THINKING_ENABLED: bool = False
    AI_THINKING_EFFORT: str = "medium"  # "low", "medium", "high"
    AI_AVAILABLE_MODELS: list[str] = [
        "anthropic/claude-opus-4-7",
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-5.5",
        "google/gemini-2.5-flash",
        "deepseek/deepseek-r1",
    ]
    AI_FRAMEWORK: str = "pydantic_ai"
    LLM_PROVIDER: str = "openrouter"

    TAVILY_API_KEY: str = ""

    CODE_EXECUTION_TIMEOUT_SECS: float = 10.0
    CODE_EXECUTION_MAX_MEMORY_MB: int = 256

    ENABLE_DEEP_RESEARCH: bool = False
    DEEP_RESEARCH_MAX_TOKENS: int = 120_000
    DEEP_RESEARCH_COMPRESS_THRESHOLD: float = 0.8

    # Deployment-managed MCP servers, always attached to the agent (on top of
    # the per-user connections configured in Settings → Integrations).
    # JSON list, e.g.:
    #   MCP_SERVERS='[{"name":"github-internal","url":"https://api.githubcopilot.com/mcp/",
    #                  "headers":{"Authorization":"Bearer ..."},
    #                  "allowed_tools":["search_issues"]}]'
    GITHUB_MCP_TOKEN: SecretStr = SecretStr("")
    BROWSERLESS_TOKEN: SecretStr = SecretStr("")
    MCP_SERVERS: list[McpServerConfig] = Field(default_factory=list)
    # Per-server budget for the pre-flight tools/list ping; unreachable servers
    # are skipped for the turn instead of failing the chat.
    MCP_CONNECT_TIMEOUT_SECS: float = 3.0

    @field_validator("MCP_SERVERS")
    @classmethod
    def validate_mcp_server_names(
        cls, v: list[McpServerConfig], info: ValidationInfo
    ) -> list[McpServerConfig]:
        """Reject duplicate names: they share a tool prefix, and the agent can
        only attach one server per prefix — the rest would vanish silently."""
        names = [server.name for server in v]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"MCP_SERVERS has duplicate server names: {', '.join(duplicates)}")
        github_token = info.data.get("GITHUB_MCP_TOKEN") if info.data else None
        if any(server.auth == "github" for server in v) and (
            not github_token or not github_token.get_secret_value()
        ):
            raise ValueError("GITHUB_MCP_TOKEN is required by an MCP server using github auth")
        for server in v:
            if server.auth != "github":
                continue
            if not server.allowed_tools:
                raise ValueError("GitHub MCP requires an explicit read-only allowed_tools list")
            unsafe = sorted(set(server.allowed_tools) - GITHUB_MCP_READ_ONLY_TOOLS)
            if unsafe:
                raise ValueError(
                    "GitHub MCP mutation tools are forbidden; tools are not in the reviewed "
                    "read-only allowlist: "
                    f"{', '.join(unsafe)}"
                )
        return v

    # Vector Database (pgvector) — uses existing PostgreSQL
    EMBEDDING_BASE_URL: str = "http://localhost:12434/engines/v1"
    EMBEDDING_API_KEY: SecretStr = SecretStr("local-docker-model-runner")
    EMBEDDING_MODEL: str = "docker.io/ai/qwen3-embedding:latest"
    EMBEDDING_MODEL_VERSION: str = "4B-Q4_K_M"
    EMBEDDING_MODEL_REVISION: str = "731f733db2ef"
    EMBEDDING_DIMENSION: int = 1024
    EMBEDDING_QUERY_INSTRUCTION: str = (
        "Given a user query, retrieve relevant passages, documentation, or code that answer "
        "the query."
    )
    EMBEDDING_DOCUMENT_INSTRUCTION: str = ""
    EMBEDDING_CACHE_URL: str = "redis://localhost:6379/3"
    EMBEDDING_CACHE_TTL_SECONDS: int = 604800
    EMBEDDING_CACHE_LOCK_SECONDS: int = 60
    EMBEDDING_BATCH_SIZE: int = 64

    @field_validator(
        "TASKIQ_MAX_ASYNC_TASKS",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_CACHE_TTL_SECONDS",
        "EMBEDDING_CACHE_LOCK_SECONDS",
        "EMBEDDING_BATCH_SIZE",
    )
    @classmethod
    def validate_positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than zero")
        return value

    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 50

    RAG_DEFAULT_COLLECTION: str = "documents"
    RAG_TOP_K: int = 10
    RAG_CHUNKING_STRATEGY: str = "recursive"  # recursive, markdown, or fixed
    RAG_HYBRID_SEARCH: bool = False  # Enable BM25 + vector hybrid search
    RAG_ENABLE_OCR: bool = True
    HF_TOKEN: str = ""
    RERANKER_PROVIDER: Literal["docker_model_runner", "disabled"] = "docker_model_runner"
    CROSS_ENCODER_MODEL: str = (
        "huggingface.co/keisuke-miyako/gte-reranker-modernbert-base-gguf-q8_0:Q8_0"
    )
    RERANKER_BASE_URL: str = "http://localhost:12434"
    RERANKER_TIMEOUT_SECONDS: float = 120.0
    RERANKER_MAX_RETRIES: int = 3
    PDF_PARSER: Literal["pymupdf", "llamaparse", "docling"] = "docling"
    CHAT_PDF_PARSER: Literal["pymupdf", "llamaparse", "docling"] = "docling"
    LLAMAPARSE_API_KEY: str = ""
    LLAMAPARSE_TIER: str = "agentic"  # fast, cost_effective, agentic, agentic_plus
    DOCLING_SERVE_URL: str = "http://localhost:5001"
    DOCLING_SERVE_TIMEOUT_SECONDS: float = 600.0
    DOCLING_SERVE_MAX_RETRIES: int = 2
    RAG_ENABLE_IMAGE_DESCRIPTION: bool = True  # set to false to disable LLM image description
    RAG_IMAGE_DESCRIPTION_MODEL: str = ""  # empty = use AI_MODEL
    GOOGLE_DRIVE_CREDENTIALS_FILE: str = "credentials/google-drive-sa.json"
    S3_RAG_ENDPOINT: str | None = None
    S3_RAG_ACCESS_KEY: str = ""
    S3_RAG_SECRET_KEY: str = ""
    S3_RAG_BUCKET: str = "fullstack-rag"
    S3_RAG_REGION: str = "us-east-1"

    EMAIL_PROVIDER: str = "log"
    EMAIL_FROM: str = "noreply@fullstack.com"
    EMAIL_FROM_NAME: str = "fullstack"
    EMAIL_REPLY_TO: str | None = None
    LOG_PROVIDER_WRITE_TO_DISK: bool = False

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:8080",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """Warn if CORS_ORIGINS is too permissive in production."""
        env = info.data.get("ENVIRONMENT", "local") if info.data else "local"
        if "*" in v and env == "production":
            raise ValueError(
                "CORS_ORIGINS cannot contain '*' in production! Specify explicit allowed origins."
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rag(self) -> "RAGSettings":
        """Build RAG-specific settings."""
        pdf_parser = PdfParser(
            method=self.PDF_PARSER,
            api_key=self.LLAMAPARSE_API_KEY,
            tier=self.LLAMAPARSE_TIER,
            docling_serve_url=self.DOCLING_SERVE_URL,
            docling_timeout_seconds=self.DOCLING_SERVE_TIMEOUT_SECONDS,
            docling_max_retries=self.DOCLING_SERVE_MAX_RETRIES,
        )

        return RAGSettings(
            collection_name=self.RAG_DEFAULT_COLLECTION,
            chunk_size=self.RAG_CHUNK_SIZE,
            chunk_overlap=self.RAG_CHUNK_OVERLAP,
            chunking_strategy=self.RAG_CHUNKING_STRATEGY,
            enable_hybrid_search=self.RAG_HYBRID_SEARCH,
            enable_ocr=self.RAG_ENABLE_OCR,
            embeddings_config=EmbeddingsConfig(
                model=self.EMBEDDING_MODEL,
                model_version=self.EMBEDDING_MODEL_VERSION,
                model_revision=self.EMBEDDING_MODEL_REVISION,
                dim=self.EMBEDDING_DIMENSION,
                base_url=self.EMBEDDING_BASE_URL,
                api_key=self.EMBEDDING_API_KEY.get_secret_value(),
                query_instruction=self.EMBEDDING_QUERY_INSTRUCTION,
                document_instruction=self.EMBEDDING_DOCUMENT_INSTRUCTION,
                cache_url=self.EMBEDDING_CACHE_URL,
                cache_ttl_seconds=self.EMBEDDING_CACHE_TTL_SECONDS,
                cache_lock_seconds=self.EMBEDDING_CACHE_LOCK_SECONDS,
                batch_size=self.EMBEDDING_BATCH_SIZE,
            ),
            reranker_config=RerankerConfig(
                provider=self.RERANKER_PROVIDER,
                model=self.CROSS_ENCODER_MODEL,
                base_url=self.RERANKER_BASE_URL,
                timeout_seconds=self.RERANKER_TIMEOUT_SECONDS,
                max_retries=self.RERANKER_MAX_RETRIES,
            ),
            document_parser=DocumentParser(),
            pdf_parser=pdf_parser,
            enable_image_description=self.RAG_ENABLE_IMAGE_DESCRIPTION,
            image_description_model=self.RAG_IMAGE_DESCRIPTION_MODEL,
        )


# Rebuild Settings to resolve RAGSettings forward reference
from app.services.rag.config import (
    DocumentParser,
    EmbeddingsConfig,
    PdfParser,
    RAGSettings,
    RerankerConfig,
)

Settings.model_rebuild()


settings = Settings()
