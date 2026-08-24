# The workspace runtime sandboxd starts for each coding session: the library's
# CODING_TOOLS baseline plus make, repository-owned (instead of the library's
# built-from-latest "coding" runtime) so the base image and uv stay digest-
# pinned. Why it exists and what network it gets per stack: ADR-006 rule 7.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# The library's CODING_TOOLS set plus make. git is unavoidable and make is what
# a repository's documented entry points run through; ripgrep, fd, jq, less and
# procps cost ~4 MB between them and keep an agent's searches out of
# node_modules and .git. build-essential stays out: 94 MB to compile wheels
# that manylinux already ships built.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl fd-find git jq less make procps ripgrep \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# Same pinned uv as backend/Dockerfile: a single binary, so nothing lands in
# site-packages for a project's own dependencies to collide with.
COPY --from=ghcr.io/astral-sh/uv:0.8.3@sha256:ef11ed817e6a5385c02cd49fdcc99c23d02426088252a8eace6b6e6a2a511f36 /uv /usr/local/bin/uv
