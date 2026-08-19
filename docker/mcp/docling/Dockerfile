FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

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
