FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS base
ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node mcp/package.json mcp/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

FROM base AS runtime
COPY --chown=node:node docs/contracts ./docs/contracts
COPY --chown=node:node rag/src ./rag/src
COPY --chown=node:node mcp/src ./mcp/src
COPY --chown=node:node deploy/runtime-iam-smoke.mjs ./deploy/runtime-iam-smoke.mjs
USER node
EXPOSE 8080
CMD ["node", "--import", "tsx", "mcp/src/runtime.ts"]

FROM base AS release-preflight
COPY --chown=node:node docs/contracts ./docs/contracts
COPY --chown=node:node agents/release-manifest.json ./agents/release-manifest.json
COPY --chown=node:node agents/fixtures ./agents/fixtures
COPY --chown=node:node agents/src ./agents/src
COPY --chown=node:node rag/src ./rag/src
USER node
CMD ["node", "--import", "tsx", "agents/src/control-cli.ts", "gcp-preflight", "--json"]
