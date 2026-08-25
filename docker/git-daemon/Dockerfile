# Dev-only (docker-compose.dev.yml, profile coding): exports the host's
# repositories over git:// so a workspace sandbox can clone and push through
# the Docker Desktop host gateway with no GitHub credential anywhere in the
# product — the local bootstrap loop of ADR-006 rule 7 / open question 1.
#
# Same digest-pinned base as docker/sandbox-runtime/Dockerfile so the layer is
# already on the machine; Debian's git package ships `git daemon` (Alpine
# splits it into a separate git-daemon package, which is why this is not
# alpine/git).
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# The daemon is anonymous and host.docker.internal is resolvable from every
# container on the machine, not just the workspace sandbox — so the write
# surface the bootstrap loop claims (new sandbox/<topic> branches) is enforced
# here rather than assumed. receive-pack runs in this container and reads this
# system config; the update hook rejects every ref outside refs/heads/sandbox/*,
# and core.hooksPath also shadows any per-repo hooks a served repository might
# carry, so a push can never execute repository-owned code in this container.
# denyDeletes/denyNonFastForwards close ref deletion and history rewind for the
# sandbox refs themselves.
RUN git config --system receive.denyDeletes true \
    && git config --system receive.denyNonFastForwards true \
    && git config --system core.hooksPath /etc/git-hooks \
    && mkdir -p /etc/git-hooks \
    && printf '%s\n' '#!/bin/sh' \
        '# args: refname sha-old sha-new' \
        'case "$1" in' \
        '  refs/heads/sandbox/*) exit 0 ;;' \
        '  *) echo "git-local: only refs/heads/sandbox/* may be pushed" >&2; exit 1 ;;' \
        'esac' > /etc/git-hooks/update \
    && chmod +x /etc/git-hooks/update

EXPOSE 9418

# --export-all: every repository under the bind mount is served without
# per-repo git-daemon-export-ok marker files — the whole-root anonymous *read*
# is the accepted dev posture recorded in ADR-006's rule 7 note.
# --enable=receive-pack: anonymous push, constrained to sandbox/* by the
# update hook above; the compose service publishes to 127.0.0.1 only.
ENTRYPOINT ["git", "daemon", "--export-all", "--enable=receive-pack", \
    "--base-path=/repos", "--reuseaddr", "--informative-errors", "--verbose"]
