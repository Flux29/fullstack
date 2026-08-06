# repo-governance

The governance control plane for this repository, implemented per
`docs/Fullstack_Agentic_Governance_Blueprint.md`.

This is a **standalone uv project**. It has its own `pyproject.toml`, `uv.lock`, and
`.venv`, and it **never imports `app.*`**. That isolation is a hard requirement, not a
style choice: `backend/app/core/config.py` walks parent directories looking for `.env`, and
`backend/app/main.py` configures Logfire at module import scope, so importing application
modules from tooling can load real secrets and emit telemetry.

Anything that must execute application code (only the OpenAPI export) runs in an isolated
subprocess with a scrubbed environment and a working directory outside the repository tree.

## Invocation

Always through the Makefile, which stays the single operational entry point:

```bash
make governance-check
```

The underlying form is `uv run --project tools/repo_governance governance ...`.

## Determinism contract

Every generated file is written through `io_atomic.py` — the single writer — which
guarantees LF-only, UTF-8 without BOM, sorted JSON keys, forward-slash paths, and an
atomic `os.replace` swap. `.gitattributes` pins the same files to LF on checkout. Running
`sync` twice with no source change must produce zero bytes of diff; `tests/` enforces this.
