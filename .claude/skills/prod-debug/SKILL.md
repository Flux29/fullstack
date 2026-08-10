---
name: prod-debug
description: Debug a live symptom by querying Logfire telemetry before reading code — traces, exceptions, slow spans, agent runs. Use when something misbehaves in a running deployment, when an error is reported without a reproduction, or when you need to know what actually happened rather than what the code suggests could happen. Routes the confirmed cause into fix-issue under the gov-change envelope.
---

# Production debugging (Logfire first)

The backend is fully instrumented — `backend/app/core/logfire_setup.py` wires FastAPI,
asyncpg, Redis, httpx, and PydanticAI into Logfire, gated by `LOGFIRE_TOKEN`
(`send_to_logfire="if-token-present"`). For a live symptom, the telemetry already knows
which request failed, what SQL it ran, and which agent tool call blew up. **Query it
before reading code** — grep answers "what could happen"; traces answer "what did".

## 1. Query — Logfire MCP, read-only

The Logfire MCP server is connected machine-side (tools load via ToolSearch). In order:

1. `query_schema_reference` first — get the records schema and query handbook before
   writing SQL.
2. Exceptions by fingerprint: the issues tools (`issue_list`, `issue_get`) group
   exceptions; `query_find_exceptions_in_file` maps a file to its live exceptions.
3. Targeted SQL via `query_run`: filter by `service_name`, `environment` (the
   `LOGFIRE_SERVICE_NAME` / `LOGFIRE_ENVIRONMENT` settings), route, time window.
   Instrumented span sources worth filtering on: FastAPI request spans, asyncpg
   queries, Redis commands, outgoing httpx calls, PydanticAI agent runs and tool calls.

For RAG-shaped symptoms, the postgres MCP (`mcp__postgres__query`, read-only) can
inspect `embedding_cache` and pgvector collections directly instead of inferring state
from CLI output.

This phase is **read-only toward production**: query telemetry and databases; never
mutate live state to "test a theory".

## 2. Diagnose

Run `engineering:debug` with the trace evidence as its input — reproduce locally,
isolate, and name the root cause. A trace narrows *where*; the debug pass proves *why*.
If the local reproduction contradicts the telemetry, believe neither yet — say so.

## 3. Fix — governed

Route the confirmed cause into `fix-issue`: gov-change envelope, regression test first,
validators from `make governance-impact`. The record's reason quotes the evidence
("p99 on /api/v1/rag/search regressed to 4s after the reranker change; trace shows the
rerank span serializing 200 candidates"), not the hunch.

## Rules

- Telemetry before code: a symptom with a live trace never starts at grep.
- Read-only toward production; every mutation goes through the governed fix loop.
- Quote query results in the report — span names, counts, timings — not summaries of them.
- No token, no telemetry: if `LOGFIRE_TOKEN` is unset in the target environment, say
  that plainly and fall back to `engineering:debug` — do not present code reading as
  trace evidence.
