# syntax=docker/dockerfile:1.10
#
# FinPilot web image. The build context is the repository root, because npm workspaces lock
# dependencies there:
#
#     docker build -f apps/web/Dockerfile -t finpilot-web:<revision> .
#
# At runtime it is an unprivileged user (10202: Node's own user is 1000, which on the home server
# is the administrator) on a read-only root filesystem, serving the
# self-contained Next.js server. It holds no secrets: the browser reaches the API through the
# reverse proxy, on the same origin.

ARG NODE_IMAGE=node:24.12.0-trixie-slim

FROM ${NODE_IMAGE} AS deps
WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
RUN npm ci --no-audit --no-fund

FROM deps AS build
COPY apps/web apps/web
# Only used by the web server's own /api/v1 rewrite. In production the proxy sends /api/v1
# straight to the API, so this is a fallback that's never reached.
ARG API_PROXY_TARGET=http://api:8000
ENV FINPILOT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    API_PROXY_TARGET=${API_PROXY_TARGET}
RUN npm run build -w @finpilot/web

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# Copied as root, so the app user can't change its own code.
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /repo/apps/web/public ./apps/web/public
RUN groupadd --system --gid 10202 finpilot-web \
    && useradd --system --uid 10202 --gid finpilot-web --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin finpilot-web
USER 10202:10202
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/en').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
CMD ["node", "apps/web/server.js"]
