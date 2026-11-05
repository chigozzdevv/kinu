FROM python:3.12.13-slim-bookworm AS dependencies
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.3
COPY pyproject.toml uv.lock ./
COPY server/pyproject.toml server/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY packages/policy/pyproject.toml packages/policy/pyproject.toml
COPY packages/telemetry/pyproject.toml packages/telemetry/pyproject.toml
COPY packages/testkit/pyproject.toml packages/testkit/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --all-extras --package kinu-server

FROM dependencies AS browser-deps
RUN --mount=type=cache,target=/root/.cache/ms-playwright \
    playwright install --with-deps --no-shell chromium \
    && cp -a /root/.cache/ms-playwright /opt/browsers \
    && chmod -R a+rX /opt/browsers

FROM dependencies AS api
COPY packages ./packages
COPY server ./server
RUN uv sync --frozen --no-dev --no-editable --all-extras --package kinu-server
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM browser-deps AS executor
COPY packages ./packages
COPY server ./server
RUN uv sync --frozen --no-dev --no-editable --all-extras --package kinu-server
RUN install -d -m 0700 -o 65532 -g 65532 /home/kinu /home/kinu/.config /home/kinu/.cache
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers HOME=/home/kinu XDG_CONFIG_HOME=/home/kinu/.config XDG_CACHE_HOME=/home/kinu/.cache
USER 65532:65532
EXPOSE 8002
CMD ["uvicorn", "executor.app:app", "--host", "0.0.0.0", "--port", "8002", "--no-access-log"]

FROM node:24-bookworm-slim AS agent-build
WORKDIR /app
COPY agent/package.json agent/package-lock.json ./
RUN npm ci
COPY agent/ ./
RUN npm run build

FROM node:24-bookworm-slim AS agent
WORKDIR /app
ENV NODE_ENV=production NITRO_HOST=0.0.0.0 NITRO_PORT=8001
COPY --from=agent-build --chown=node:node /app/.output ./.output
USER node
EXPOSE 8001
CMD ["node", ".output/server/index.mjs"]

FROM node:24-bookworm-slim AS client-build
WORKDIR /app
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client/ ./
RUN npm run build

FROM caddy:2-alpine AS gateway
COPY deploy/Caddy-vps /etc/caddy/Caddyfile
EXPOSE 80

FROM caddy:2-alpine AS web
COPY --from=client-build /app/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
