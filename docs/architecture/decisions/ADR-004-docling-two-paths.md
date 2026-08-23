---
id: ADR-004
title: Docling has two execution paths against one service
status: accepted
date: 2026-08-06
components:
  - rag
  - docling-serve
  - docling-mcp
---

# ADR-004 — Docling has two execution paths against one service

## Status

Accepted, 2026-08-06.

## Context

Document conversion is reached two different ways, and it is easy to assume there is only
one:

1. **Ingestion and chat uploads** use a direct HTTP client to Docling Serve. This is the
   path that produces the corpus: retry budget, upload size cap, and page-break sentinel
   contract all belong to it.
2. **The `docling-mcp` sidecar** is an *agent tool source*, available under the `mcp`
   Compose profile. It runs in remote conversion mode against the same Docling Serve
   instance rather than converting locally.

Both terminate at one GPU-backed service with an external model cache. The sidecar is not a
second converter; it is a second door to the first one.

## Decision

**Policy covers both paths, and the invariant is that they agree.**

- Both must resolve to the same `DOCLING_SERVE_URL` with compatible timeout and retry
  budgets. If they diverge, the agent and the ingestion pipeline can give different answers
  about the same document, which is the kind of inconsistency that gets diagnosed as a model
  problem for a long time before anyone checks the endpoints.
- The sidecar keeps local fallback conversion **disabled**. A silent fallback would move GPU
  work into a container sized for none, turning a capacity problem into a mysterious latency
  problem.
- The model cache is an external volume mounted read-only, shared and preserved. It is
  pre-existing state that governance never recreates or normalizes.
- Models load lazily rather than at boot: cold start is fast, first conversion is slow, and
  that trade is deliberate.
- After any configuration change touching either path, conversion health and a harmless
  conversion test are required — a URL that resolves is not evidence that conversion works.

## Consequences

- The Docling policy has two subjects, and a check that only looked at the ingestion client
  would miss half the surface.
- GPU behaviour, worker count, and out-of-memory expectations stay explicit in the services
  manifest rather than being discovered under load.
- `docker/mcp/docling/` carries a directory annotation because it has a build context; the
  Docling Serve service itself is declared in the curated manifest, because an image-only
  service has no directory to annotate.
