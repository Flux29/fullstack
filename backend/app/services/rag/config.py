"""RAG configuration."""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DocumentExtensions(StrEnum):
    """Extensions supported by the RAG ingestion pipeline."""

    PDF = ".pdf"
    DOCX = ".docx"
    PPTX = ".pptx"
    XLSX = ".xlsx"
    JPG = ".jpg"
    JPEG = ".jpeg"
    PNG = ".png"
    TIF = ".tif"
    TIFF = ".tiff"
    BMP = ".bmp"
    WEBP = ".webp"
    MD = ".md"
    TXT = ".txt"


PYMUPDF_FORMATS: set[str] = {".pdf", ".docx", ".txt", ".md"}

DOCLING_FORMATS: set[str] = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

LLAMAPARSE_FORMATS: set[str] = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".rtf",
    ".txt",
    ".md",
    ".epub",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".xlsx",
    ".xls",
    ".csv",
    ".tsv",
    ".ods",
    ".mp3",
    ".mp4",
    ".wav",
    ".m4a",
    ".webm",
    ".html",
    ".htm",
    ".xml",
}

PARSER_FORMATS: dict[str, set[str]] = {
    "pymupdf": PYMUPDF_FORMATS,
    "docling": DOCLING_FORMATS,
    "llamaparse": LLAMAPARSE_FORMATS,
}


def get_supported_formats(parser_name: str = "pymupdf") -> set[str]:
    """Get supported file formats for a given parser."""
    return PARSER_FORMATS.get(parser_name, PYMUPDF_FORMATS)


# Known embedding models and their output dimensions.
# Used to auto-set vector store dimension from model name.
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-code-3": 1024,
    "gemini-embedding-exp-03-07": 3072,
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "bge-small-en-v1.5": 384,
    "bge-base-en-v1.5": 768,
    "bge-large-en-v1.5": 1024,
}


class EmbeddingsConfig(BaseModel):
    """Embedding provider, fingerprint, dimensionality, and cache configuration."""

    model: str = "docker.io/ai/qwen3-embedding:latest"
    model_version: str = "4B-Q4_K_M"
    model_revision: str = ""
    dim: int | None = 1024
    base_url: str = "http://localhost:12434/engines/v1"
    api_key: str = "local-docker-model-runner"
    query_instruction: str = ""
    document_instruction: str = ""
    cache_url: str = "redis://localhost:6379/3"
    cache_ttl_seconds: int = 604800
    cache_lock_seconds: int = 60
    batch_size: int = 64

    @model_validator(mode="after")
    def set_dim_from_model(self) -> "EmbeddingsConfig":
        if self.dim is None and self.model in EMBEDDING_DIMENSIONS:
            self.dim = EMBEDDING_DIMENSIONS[self.model]
        if self.dim is None:
            raise ValueError("Embedding dimension must be configured for an unknown model")
        if self.dim <= 0:
            raise ValueError("Embedding dimension must be greater than zero")
        return self


class RerankerConfig(BaseModel):
    """Reranker configuration."""

    provider: str = "docker_model_runner"
    model: str = "huggingface.co/keisuke-miyako/gte-reranker-modernbert-base-gguf-q8_0:Q8_0"
    base_url: str = "http://localhost:12434"
    timeout_seconds: float = 120.0
    max_retries: int = 3

    @model_validator(mode="after")
    def validate_reranker(self) -> "RerankerConfig":
        if self.provider not in {"docker_model_runner", "disabled"}:
            raise ValueError("Reranker provider must be docker_model_runner or disabled")
        if self.provider != "disabled" and not self.model.strip():
            raise ValueError("Reranker model is required when reranking is enabled")
        if self.timeout_seconds <= 0:
            raise ValueError("Reranker timeout must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("Reranker max retries cannot be negative")
        self.base_url = self.base_url.rstrip("/")
        return self


class DocumentParser(BaseModel):
    """Document parsing settings (non-PDF files)."""

    method: str = "python_native"


class PdfParser(BaseModel):
    """PDF parsing settings."""

    method: str = "docling"
    api_key: str = ""
    tier: str = "agentic"
    docling_serve_url: str = "http://localhost:5001"
    docling_timeout_seconds: float = 600.0
    docling_max_retries: int = 2


class RAGSettings(BaseModel):
    """RAG pipeline configuration."""

    collection_name: str = "documents"

    allowed_extensions: list[DocumentExtensions] = Field(
        default_factory=lambda: list(DocumentExtensions)
    )

    chunk_size: int = 512
    chunk_overlap: int = 50
    chunking_strategy: str = "recursive"
    enable_hybrid_search: bool = False
    enable_ocr: bool = False

    embeddings_config: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    reranker_config: RerankerConfig = Field(default_factory=RerankerConfig)

    document_parser: DocumentParser = Field(default_factory=DocumentParser)
    pdf_parser: PdfParser = Field(default_factory=PdfParser)
    enable_image_description: bool = True
    image_description_model: str = ""
    gdrive_ingestion: bool = True
