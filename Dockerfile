# Single-stage build for fast deploys
# Frontend is pre-built (frontend/dist/) before docker build

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

# Copy pre-built frontend
COPY frontend/dist/ static/

# Ensure app user can read all files
RUN chmod -R a+rX /app

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /tmp/sandbox /tmp/uploads && chown app:app /tmp/sandbox /tmp/uploads

ENV UPLOAD_DIR=/tmp/uploads

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/readiness')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
