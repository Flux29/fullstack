"""The joined repository graph: assembly, queries, and the SQLite cache.

The three extraction layers (Python imports, TypeScript imports, relations) stay
independent and honest about their own uncertainty; this package joins them into one
typed node/edge set conforming to the committed vocabulary in ``governance/graph/``.
The committed reports are computed from sources in-memory — never from the SQLite
cache, which is a derived convenience for queries and is gitignored.
"""
