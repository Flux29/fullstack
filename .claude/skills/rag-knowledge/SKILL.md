---
name: rag-knowledge
description: Work with the RAG knowledge base — ingest documents, run semantic search, manage collections, or add a sync source/connector (Google Drive, S3). Use when populating or debugging the knowledge base, tuning retrieval, or adding a new document source. This project uses pgvector, Docker Model Runner, Docling Serve, and a durable embedding cache.
---

# RAG Knowledge Base (pgvector)

The RAG stack lives in `backend/app/services/rag/` (ingestion, vectorstore, embeddings, connectors). Retrieval is exposed to the agent as the `search_knowledge_base` tool, and to operators via the CLI and the dashboard.

## CLI (run from `backend/`)

```bash
uv run fullstack cmd rag-ingest ./docs/ --collection docs --recursive   # ingest files/folder
uv run fullstack cmd rag-search "your question" --collection docs        # semantic search
uv run fullstack cmd rag-collections                                     # list collections
uv run fullstack cmd rag-stats                                           # chunk/vector counts
uv run fullstack cmd rag-drop <collection> --yes                         # delete a collection
```

Ingestion = parse → chunk → embed → upsert into pgvector. Re-ingesting the same source updates it (use `--no-replace` / `--sync-mode` to control dedupe).

## Sync sources (connectors)

Connectors keep a collection in sync with an external source (Google Drive, S3/MinIO) on a schedule, and can be managed per-organization from the dashboard (`/orgs/[id]/integrations`) or via CLI:

```bash
uv run fullstack cmd rag-sources                  # list
uv run fullstack cmd rag-source-add               # add (interactive)
uv run fullstack cmd rag-source-sync --all        # trigger a sync
```

Connector credentials are encrypted at rest with `CHANNEL_ENCRYPTION_KEY` (Fernet).

## Adding a new connector type

Implement a connector in `backend/app/services/rag/connectors/` following the existing Google Drive / S3 connectors, register it in the connector registry, and expose its config fields. See `docs/howto/add-sync-connector.md` and `docs/howto/configure-sync-sources.md`.

## Tuning retrieval

- Chunk size/overlap and parser (Docling Serve by default; PyMuPDF/LlamaParse opt-in) are configured via env — see `docs/configuration.md`.
- Reranking uses Docker Model Runner's native `/rerank` endpoint with the configured GGUF reranker and improves result ordering when enabled.
- If search returns poor results: confirm the collection is populated (`rag-stats`), check the active collection in the chat's KB selector, and verify the embedding provider/key.

## Rules

- The embedding contract — model, dimension, normalization, instructions, reranker, and
  the collection fingerprint that binds them — is declared in
  `governance/policies/compatibility.json`. That file is the single statement of the
  contract; `rag-change` §4 is the procedure for changing it. A fingerprint mismatch is
  an intentional hard failure: delete/re-create and re-ingest the collection explicitly.
- Do not download or load a SentenceTransformers cross-encoder inside the backend;
  reranking goes through Model Runner's native `/rerank`.
- Embedding results use Redis DB 3 as L1 and PostgreSQL `embedding_cache` as durable L2.
  Query and document keys remain distinct.
- PDF/DOCX/PPTX/XLSX/images go through the shared Docling Serve container; TXT/Markdown remain local. Do not add an in-process Docling model.
- Heavy ingestion runs as a background job, not inline in a request.
- See `docs/file-processing.md` for the full pipeline reference (ingestion flow, parser
  selection, chunking, embedding, reranking) and `docs/configuration.md` for the env surface.
