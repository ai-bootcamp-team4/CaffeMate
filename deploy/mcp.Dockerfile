FROM node:24-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --chown=node:node docs/contracts ./docs/contracts
COPY --chown=node:node agents/release-manifest.json ./agents/release-manifest.json
COPY --chown=node:node agents/fixtures ./agents/fixtures
COPY --chown=node:node agents/src ./agents/src
COPY --chown=node:node rag/src ./rag/src
COPY --chown=node:node mcp/src ./mcp/src
USER node
EXPOSE 8080
CMD ["node", "--import", "tsx", "mcp/src/runtime.ts"]
