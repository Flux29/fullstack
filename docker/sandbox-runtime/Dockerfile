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

# --- frontend toolchain + PowerShell (ADR-006 rule 7 note: the priced upgrade
# that makes frontend work bootstrappable inside a workspace) ----------------
#
# Everything below is copied out of digest-pinned donor images rather than
# apt-installed, so the versions are exact and the base image's /usr/local
# (which holds Python) is never overlaid wholesale.

# Node 22 (node + npm/npx + corepack): Next.js tooling and anything a
# repository's package.json scripts assume. Selective copies — bin/node plus
# lib/node_modules — then the same symlinks the donor image ships.
COPY --from=node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 \
    /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 \
    /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

# bun: this repository's registered frontend toolchain (frontend-test runs
# `bun run lint && bun run type-check && bun run test:coverage`); a single
# static-ish binary, same install shape as uv.
COPY --from=oven/bun:1-slim@sha256:e0ee68d16ccb9927bf02aa7dd8fd4bf3369ee6d46da04faa72b05ce8bfd135f6 \
    /usr/local/bin/bun /usr/local/bin/bun

# pwsh: the Make surface here is PowerShell-flavoured; with pwsh present an
# agent can run recipe bodies directly instead of reverse-engineering them.
# .NET needs libicu and libstdc++ from the host image (libicu76 — the pinned
# base is Debian 13/trixie); the donor's Ubuntu 22.04 userland is
# glibc-compatible with it.
COPY --from=mcr.microsoft.com/powershell:lts-ubuntu-22.04@sha256:663a172849444279d8e7b50e19977b6e42380eae6856bdc8931ca08e2e03e92a \
    /opt/microsoft/powershell/7-lts /opt/microsoft/powershell/7-lts
RUN apt-get update \
    && apt-get install -y --no-install-recommends libicu76 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /opt/microsoft/powershell/7-lts/pwsh \
    && ln -s /opt/microsoft/powershell/7-lts/pwsh /usr/local/bin/pwsh
