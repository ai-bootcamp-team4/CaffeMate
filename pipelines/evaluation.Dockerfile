FROM node:24-bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"
WORKDIR /workspace

COPY package.json package-lock.json ./
RUN npm ci
COPY api/pyproject.toml api/uv.lock api/
RUN uv sync --project api --frozen
COPY agents agents
COPY api api
COPY docs/evaluation docs/evaluation
COPY mcp mcp
COPY rag rag
COPY scripts/evaluation scripts/evaluation
COPY worker worker

ENTRYPOINT ["node"]
