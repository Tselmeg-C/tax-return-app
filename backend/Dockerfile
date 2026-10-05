# api image for Railway: FastAPI (uvicorn) + worker, run together by honcho (Procfile).
# Migrations are NOT run here; Railway runs `alembic upgrade head` as the pre-deploy step.
# No secrets in ARG/ENV: DATABASE_URL, OTEL_* etc. come from Railway variables at runtime.

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-does-not-exist-slim

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini Procfile ./
COPY app ./app
RUN uv sync --locked --no-dev --no-editable

RUN groupadd --system --gid 10001 belegbot \
    && useradd --system --uid 10001 --gid belegbot --home-dir /app --no-create-home belegbot \
    && chown -R belegbot:belegbot /app
USER belegbot

# Run from the venv directly (not `uv run`), so nothing syncs at start.
ENV PATH="/app/.venv/bin:${PATH}" \
    PORT=8000
EXPOSE 8000

# --no-prefix keeps each api/worker stdout line a plain JSON object.
CMD ["honcho", "start", "--no-prefix", "-f", "Procfile"]
