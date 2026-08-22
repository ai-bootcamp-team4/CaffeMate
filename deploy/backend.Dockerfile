# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.28

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

COPY --from=uv /uv /uvx /bin/

ENV HOME=/home/caffemate \
    PATH=/srv/api/.venv/bin:${PATH} \
    PORT=8080 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv/api:/srv \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /srv

RUN groupadd --gid 65532 caffemate \
    && useradd --uid 65532 --gid 65532 --create-home --home-dir /home/caffemate caffemate

COPY api/pyproject.toml api/uv.lock ./api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project api --locked --no-install-project

COPY api/app ./api/app
COPY api/migrations ./api/migrations
COPY agents/release-manifest.json ./agents/release-manifest.json
COPY agents/fixtures ./agents/fixtures
COPY docs/contracts ./docs/contracts
COPY worker ./worker

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project api --locked --no-dev \
    && chown -R caffemate:caffemate /home/caffemate

USER 65532:65532

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8080') + '/health', timeout=2)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir api --host 0.0.0.0 --port ${PORT:-8080}"]
