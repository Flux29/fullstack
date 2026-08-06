"""Extractors.

Each reads an authoritative source and returns normalized facts. None of them imports
application code: AST parsing is the primary method, and the one thing that must execute
the application — the OpenAPI export — runs in a scrubbed subprocess.

Every extractor reports what it could not parse as an explicit unknown. A parser failure
that returns an empty list is indistinguishable from a source that genuinely contains
nothing, and the second is a much rarer condition than the first.
"""

from __future__ import annotations

__all__: list[str] = []
