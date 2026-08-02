FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

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
