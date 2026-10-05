# API image: FastAPI + the built web pages (DEC-41). The crawler runs from this same image
# (`python -m app.ingest.run`) -- a separate Dockerfile.crawler only once Crawl4AI is added (DEC-36).
# Config comes only from env vars (no .env in the image, see .dockerignore).

# --- 1. web pages: React + TS -> web/dist ---
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- 2. Python app ---
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY app/ app/
COPY --from=web /web/dist web/dist

RUN useradd --create-home --uid 1000 app
USER app

# Cloud Run sets PORT (default 8080); locally Compose maps 8000.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
