FROM node:26.7.0-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341

ENV NODE_ENV=production \
    CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS=1 \
    CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS=1

WORKDIR /srv

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts \
    && chown -R node:node /srv

USER node
EXPOSE 8000

CMD ["sh", "-c", "exec ./node_modules/.bin/supergateway --stdio \"./node_modules/.bin/chrome-devtools-mcp --ws-endpoint=ws://browserless:3000/?token=$BROWSERLESS_TOKEN --no-usage-statistics --no-performance-crux\" --outputTransport streamableHttp --stateful --sessionTimeout 600000 --streamableHttpPath /mcp --healthEndpoint /healthz --port 8000"]
