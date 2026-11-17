FROM node:26.13.1-bookworm-slim@sha256:5df79989ed685e09f0a98019db4cebfb22fb9df1a53c0161664a011f7ff6b0f1

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
