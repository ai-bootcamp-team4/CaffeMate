FROM node:24-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03
ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node mcp/package.json mcp/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --chown=node:node docs/contracts ./docs/contracts
COPY --chown=node:node rag/src ./rag/src
COPY --chown=node:node mcp/src ./mcp/src
USER node
EXPOSE 8080
CMD ["node", "--import", "tsx", "mcp/src/runtime.ts"]
