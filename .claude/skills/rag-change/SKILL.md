---
name: rag-change
description: Change the RAG subsystem under governance — tune retrieval, add a sync connector, alter chunking or parsing, or change the embedding model, dimension, or reranker. Use when the change touches backend/app/services/rag/, pgvector collections, the embedding cache, or Docker Model Runner. Enforces the embedding-dimension compatibility contract that silently corrupts retrieval when skipped.
---

# Governed RAG change

RAG is a thick subpackage (`backend/app/services/rag/`) sitting on four pieces of
infrastructure — pgvector, Docker Model Runner, Docling Serve, and a two-level embedding
cache. Its failure mode is not a crash. It is **plausible answers computed over stale or
mismatched vectors**, which no unit test catches. Classify the change first; the gates differ
sharply.

Use `rag-knowledge` for the operational surface (CLI, ingestion, search, connector
management). Use this skill when the change is governed — i.e. it edits code or contracts.

## 1. Classify

| Class | Examples | Gate |
| --- | --- | --- |
| **A — Retrieval tuning** | chunk size/overlap, top-k, reranking on/off, prompt instructions | `rag-docker-integration` |
| **B — Pipeline / parsing** | parser selection, new file type, ingestion job shape | `rag-docker-integration` + `backend-unit` |
| **C — Connector** | new sync source, connector config fields | + `backend-unit`, credential encryption |
| **D — Embedding contract** | model, dimension, normalization, instructions, reranker | **Compatibility contract — §4** |

## 2. Open

Follow `gov-change` GOV-OPEN with
`PATHS="backend/app/services/rag"` (add `backend/app/db/models` for a schema-touching change).

Then confirm the infrastructure is actually up before you trust any result:

```bash
make preflight-model      # embedding model at the contracted dimension + reranker
make preflight-volumes    # docling-models, redis-data preservation volumes
```

`preflight-model` is the health check for the model-runner-host component. If it fails, stop.
Every retrieval result you gather afterwards is meaningless.

## 3. Change

Class A/B — edit inside `backend/app/services/rag/`. Callers import through the package
facade (`__init__.py`, lazy PEP 562 re-exports): `main.py`, `deps.py`, the worker, and the
CLI all import from `app.services.rag`, and the `thick-domains-expose-a-facade` policy rule
tracks any deep import as an advisory finding. A new public name is added to the facade's
`__all__` and export map in the same change; the facade must stay free of import-time side
effects — never give it a module-scope import of a pipeline sub-module.

Class C — implement in `backend/app/services/rag/connectors/` following the existing Google
Drive and S3 connectors, register it in the connector registry, and expose its config fields.
Credentials are encrypted at rest with `CHANNEL_ENCRYPTION_KEY` (Fernet) — a connector that
stores a plaintext credential is a policy violation, not a TODO. Scheduled sync runs as a
background job; chain `background-task` if you are adding the schedule.

Heavy ingestion always runs as a background job, never inline in a request.

## 4. Class D — the embedding compatibility contract

**Read this before changing a model id, dimension, normalization, or instruction string.**

Collection fingerprints include the exact model request ID, artifact revision, dimensions,
normalization version, and the query/document instructions. A mismatch is an **intentional
hard failure**, not a bug to route around. The contract is declared in
`governance/policies/compatibility.json`; check it before editing, and never weaken it to
make a change pass.

Required sequence:

1. Confirm the intended change against `compatibility.json`.
2. If the dimension changes, the pgvector column changes — chain `alembic-migration`.
   Existing vectors are **not** convertible. Plan the re-embed, do not hope for one.
3. Invalidate both cache levels. Redis DB 3 is L1; PostgreSQL `embedding_cache` is durable L2.
   Query keys and document keys remain distinct — invalidating one is a half-migration that
   yields query/document vector mismatch.
4. Delete and re-create affected collections explicitly, then re-ingest. Do not attempt an
   in-place upgrade.
5. Run `rag-docker-integration` — it is the dedicated test for the embedding dimension guard
   and the cache-key fingerprint.

The contract itself — the five points that must agree (settings, the vectorstore guard
and DDL, the migration CHECK constraint, the per-collection fingerprint, the cache key
version) — is `embedding-dimension-contract` in `governance/policies/compatibility.json`.
The current values (model, dimension, normalization, reranker) live in
`backend/app/services/rag/config.py` and settings. Read both rather than any prose copy,
including this skill's earlier revisions.

Never load a SentenceTransformers cross-encoder inside the backend, and never add an
in-process Docling model — PDF/DOCX/PPTX/XLSX/images go through the shared Docling Serve
container; TXT and Markdown stay local.

## 5. Verify retrieval actually improved

Automated validators prove the pipeline runs. They do not prove retrieval got better. For
class A and D, run a before/after comparison on real queries:

```bash
uv run fullstack cmd rag-stats                                    # confirm the collection is populated
uv run fullstack cmd rag-search "<query>" --collection <name>     # same queries, before and after
```

Report the comparison. "Tests pass" is not evidence that retrieval improved, and claiming it
is misrepresents the change.

## 6. Close

Follow `gov-change` GOV-CLOSE. Expect `governance-impact` to select
`rag-docker-integration`, plus `migrations` for class D and `backend-unit` for B/C. New env
vars are picked up by `governance-sync`, which regenerates `ENV_VARS.md` — never hand-edit it.

## Rules

- A fingerprint mismatch is the system working. Delete and re-ingest; never relax the guard.
- Invalidate L1 **and** L2, query keys **and** document keys.
- No plaintext connector credentials.
- No in-process Docling model, no in-backend cross-encoder.
- Retrieval quality claims need query-level evidence, not a green test run.
