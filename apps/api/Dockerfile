FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /usr/local/bin/
WORKDIR /app/apps/api
COPY apps/api/pyproject.toml apps/api/uv.lock ./
ENV UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
RUN find .venv -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf -- {} +

FROM python:3.12-slim AS runtime
RUN groupadd --gid 10001 tracerisk && useradd --uid 10001 --gid 10001 --no-create-home tracerisk
WORKDIR /app
COPY --from=builder /app/apps/api/.venv /app/apps/api/.venv
COPY apps/api/pyproject.toml apps/api/alembic.ini /app/apps/api/
COPY apps/api/src /app/apps/api/src
COPY apps/api/migrations /app/apps/api/migrations
COPY scripts/__init__.py scripts/database_lifecycle.py /app/scripts/
ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/api/src:/app" \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "tracerisk.main:app", "--host", "0.0.0.0", "--port", "8000"]
