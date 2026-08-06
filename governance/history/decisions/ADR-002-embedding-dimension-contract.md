---
id: ADR-002
title: The embedding dimension is a five-point contract
status: accepted
date: 2026-08-06
components:
  - rag
  - postgres-pgvector
  - model-runner-host
---

# ADR-002 — The embedding dimension is a five-point contract

## Status

Accepted, 2026-08-06. Records a decision already implemented in code; written now so it
survives the changes that will eventually try to break it.

## Context

The RAG pipeline embeds with a Qwen3 model at **1024 dimensions**. That number is not
stored in one place. It is asserted independently at five points, each of which will happily
keep working while disagreeing with the others:

1. **Settings** — `EMBEDDING_DIMENSION` in `backend/app/core/config.py`.
2. **The vectorstore guard and DDL** — `backend/app/services/rag/vectorstore.py` refuses a
   mismatched vector and creates `vector(1024)` columns with a matching HNSW index.
3. **The migration CHECK constraint** — revision `0027_embedding_cache` constrains the
   stored dimension at the database level.
4. **The per-collection fingerprint** — each collection persists an `embedding_fingerprint`
   validated when the collection is opened.
5. **The embedding cache key version** — `embedding_cache.py` hashes dimensions, input kind,
   instruction, normalized input, model ID, and model version into the cache key.

The failure this creates is quiet. Change the setting alone and ingestion writes vectors the
database rejects — loud, fine. Change the setting and the DDL but not the fingerprint, and
existing collections keep serving results computed under the old model while new ones use
the new one. Nothing errors. Retrieval quality just degrades, in a way that looks like a
prompt problem.

## Decision

**The dimension is a contract across all five points, and governance checks agreement
between them rather than checking each against settings.**

Concretely:

- The expected value stays `1024` unless an explicit migration changes the complete
  contract — all five points, in one change, with a data migration for existing vectors.
- Drift between **any two** points is the finding. There is no privileged point that the
  others are compared against; asking "does the DDL match settings?" would miss a
  fingerprint that matches neither.
- The cache key version is part of the contract, not an implementation detail. Changing what
  the key hashes invalidates both cache levels, and a cache that survives a model change is
  a correctness bug rather than a performance win.
- `make preflight-model` verifies the host runtime actually serves the contracted dimension
  before a stack starts, because the fifth way to break this is for the model endpoint to
  quietly return something else.

## Consequences

- Changing the embedding model is a deliberate, multi-file operation with a migration. That
  is the intended cost.
- The two-level cache — Redis level one, a durable PostgreSQL pgvector table level two —
  is part of the data-store manifest rather than an invisible optimization, because its
  keys encode the contract.
- The dedicated test for this invariant is `backend/tests/test_rag_docker_integration.py`,
  registered as the `rag-docker-integration` validator. Line coverage says nothing useful
  here; what matters is that this specific agreement has a test.
