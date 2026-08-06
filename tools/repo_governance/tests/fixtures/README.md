# Fixtures

Inputs that the real repository must never contain: malformed environment templates,
duplicate keys, concatenated lines, unparseable route handlers, branched Alembic chains.

Tests that only need a minimal valid tree build one in a temp directory instead — see the
`minimal_repo` fixture in `conftest.py`. Files land here when the *content* is the point
and constructing it inline would obscure the test.

Populated from Phase 3 onward, when the extractors that consume these shapes exist.
