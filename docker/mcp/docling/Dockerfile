FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.lock .
RUN pip install --no-cache-dir --requirement requirements.lock \
    && useradd --create-home --uid 10001 mcp \
    && mkdir -p /cache \
    && chown -R mcp:mcp /srv /cache

USER mcp
EXPOSE 8000

CMD ["docling-mcp-server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
