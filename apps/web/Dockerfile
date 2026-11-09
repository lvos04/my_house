# syntax=docker/dockerfile:1
# Build context: apps/web. Base images are pinned by tag; Dependabot (docker) proposes updates.
FROM node:22.23.4-alpine3.24 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Single version source (contract C9): CI passes the repository's VERSION file as APP_VERSION,
# Vite exposes it to the app as import.meta.env.VITE_APP_VERSION.
ARG APP_VERSION=0.0.0-dev
ENV VITE_APP_VERSION=${APP_VERSION}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24 AS runtime
ARG APP_VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="Smart Home Control web" \
      org.opencontainers.image.description="PWA en reverse proxy voor de Smart Home Control API" \
      org.opencontainers.image.version="${APP_VERSION}"
# TUNNEL_IP: the only peer trusted for CF-Connecting-IP / X-Forwarded-Proto (the cloudflared
# container's static address in infra/compose/compose.yaml; compose sets it explicitly).
ENV API_UPSTREAM=api:8000 \
    NGINX_RESOLVER=127.0.0.11 \
    TUNNEL_IP=172.30.250.2 \
    NGINX_ENVSUBST_FILTER="^(API_UPSTREAM|NGINX_RESOLVER|TUNNEL_IP)$"
USER root
# Security fixes released after the base image was built (the CI image scan fails on them).
RUN apk upgrade --no-cache \
    && rm -f /etc/nginx/conf.d/default.conf && chown 101:101 /etc/nginx/conf.d
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Site files stay owned by root: nginx (uid 101) can read but never change them.
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
# Does not depend on the API: the web container is healthy as soon as nginx serves.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
