FROM node:26.10.0-bookworm-slim@sha256:662933cf47f013bc8e4beb31a6116448427a82057ba7c42c97e4c5ba766504c2

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
