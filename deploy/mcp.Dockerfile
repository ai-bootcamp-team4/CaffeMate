FROM node:24-slim
ENV NODE_ENV=production
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY docs/contracts ./docs/contracts
COPY mcp/src ./mcp/src
USER node
EXPOSE 8080
CMD ["node", "--import", "tsx", "mcp/src/runtime.ts"]
