# Single-stage build for fast deploys
# One image, two entrypoints (api + worker) per spec

FROM python:3.12-slim-bookworm

# Non-root user (uid 10001 per spec)
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /bin/false --create-home app

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir . uvicorn

# Copy application code
COPY alembic/ alembic/
COPY alembic.ini .
COPY src/ src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /tmp/sandbox && chown app:app /tmp/sandbox

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/readiness')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
