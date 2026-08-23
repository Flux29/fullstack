# sandboxd — the sandbox service coding workspaces talk to (ADR-006).
#
# Built here rather than pulled: the upstream image is not publicly available,
# and building it keeps the base image pinned by digest like every other image
# in this repository.
#
# This container holds the Docker socket so the API container never has to. It
# creates and drives the per-workspace sandbox containers; the application
# reaches it over HTTP with a token and never touches Docker itself.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SANDBOXD_HOST=0.0.0.0 \
    SANDBOXD_PORT=8080

# Pinned to the same release the backend depends on, so the wire protocol the
# RemoteSandbox client speaks cannot drift from the server that answers it.
RUN pip install --no-cache-dir "pydantic-ai-backend[server]==0.2.29"

# The workspace root sandboxd hands to each session. Bind-mounted from a named
# volume so a workspace survives a container restart.
RUN mkdir -p /workspaces
VOLUME ["/workspaces"]

EXPOSE 8080

# Runs as root: this process manages the Docker socket, which is root-equivalent
# by construction. That is precisely why it is a separate container from the API
# and why it sits on an internal network with no route to the database.
CMD ["python", "-m", "pydantic_ai_backends.remote.server"]
