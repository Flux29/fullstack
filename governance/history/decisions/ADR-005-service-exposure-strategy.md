---
id: ADR-005
title: Exposure posture is per-stack, and the edge is the only public surface
status: accepted
date: 2026-08-06
components:
  - traefik
  - backend-api
  - postgres-pgvector
---

# ADR-005 — Exposure posture is per-stack, and the edge is the only public surface

## Status

Accepted, 2026-08-06.

## Context

There are four Compose files combined into canonical stacks by the Makefile, crossed with
the `frontend`, `mcp`, `db-ui`, and `edge` profiles. Exposure differs sharply per stack, and
the difference *is* the security posture:

| Stack | Host ports published |
| --- | --- |
| base (staging-like) | **none** — every service uses `expose` and internal networks |
| dev | every service, bound to `127.0.0.1` only |
| production | Traefik on 80 and 443, plus a loopback-bound dashboard |

Additionally, `data-internal` is declared `internal: true`, so the database, Redis, and
object storage have no egress path at all.

A generic "detect duplicate host ports" check is nearly vacuous against this. The base file
publishes nothing, so there is nothing to collide. The valuable invariant is the posture
itself.

## Decision

**Encode exposure as a per-stack invariant and check that, not port collisions.**

- The base stack publishes **zero** host ports. A base-stack service that needs host access
  is solved with an override file, never by editing the base.
- The dev stack binds only to `127.0.0.1`. A dev port published on `0.0.0.0` exposes a
  developer's database to their network, which is the actual risk a port check should catch.
- Production publishes only the Traefik entrypoints, plus the dashboard on loopback.
- `data-internal` stays `internal: true`. Making it routable would silently give the
  database egress.
- Third-party images stay pinned by digest. Every one already is; the policy prevents
  regression rather than requesting a change.
- The Traefik HTTP port is configurable rather than hard-coded, because this host already
  runs IIS on port 80. `make preflight-edge-ports` refuses to start the edge when 80, 443,
  or 8080 are already listening.

## Consequences

- `make compose-check` remains the authority for whether the matrix resolves; governance
  wraps it and adds the posture assertions on top rather than reimplementing validation.
- Compose extraction models the full file-stack by profile matrix, because a service's
  exposure is a property of the combination, not of the service.
- Docker Model Runner is modelled as a host runtime with no Compose declaration at all.
  Without it the embedding and reranker dependency edges vanish from the graph, and the
  system would appear to have no external model dependency.
