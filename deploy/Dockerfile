# One Dockerfile, three roles: api and worker (the backend target) and web (static frontend +
# reverse proxy). Without a target or SPANLIGHT_TARGET it builds web.
#   docker build -f deploy/Dockerfile --target backend -t spanlight .
#   docker build -f deploy/Dockerfile --target web     -t spanlight-web .
# Builders that cannot pass --target (Railway) set the SPANLIGHT_TARGET build arg instead.
ARG SPANLIGHT_TARGET=web

# ---------- Frontend build ----------
FROM node:24-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Documentation site ----------
# Built from docs-site/ plus the repository documents it publishes (runbooks, security pages,
# the deploy guide, the API notes, the changelog, SECURITY.md, the OpenAPI snapshot and the price
# table backend/app/pricing/prices.py), copied to the paths docs-site/scripts/sync-docs.mjs reads
# them from. The build also writes the Content-Security-Policy snippet for /docs/
# (docs-site/scripts/csp.mjs).
FROM node:24-alpine AS docs-build
# The public origin (for example https://spanlight.dev) gives the docs canonical URLs, a sitemap
# and llms.txt. Leave it empty for a self-hosted build: those three are then skipped.
ARG DOCS_SITE_URL=
ENV ASTRO_TELEMETRY_DISABLED=1 DOCS_SYNC_SKIP_TARGET_CHECK=1 DOCS_SITE_URL=$DOCS_SITE_URL
WORKDIR /src/docs-site
COPY docs-site/package.json docs-site/package-lock.json ./
RUN npm ci
COPY CHANGELOG.md SECURITY.md /src/
COPY backend/openapi.json /src/backend/openapi.json
COPY backend/app/pricing/prices.py /src/backend/app/pricing/prices.py
COPY docs/runbooks /src/docs/runbooks
COPY docs/security /src/docs/security
COPY docs/deploy /src/docs/deploy
COPY docs/api-deviations.md /src/docs/api-deviations.md
COPY docs-site/ ./
RUN npm run build

# ---------- Python dependencies ----------
FROM python:3.13-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

# ---------- Backend runtime (api / worker) ----------
FROM python:3.13-slim AS backend
# The app runs from /opt/venv and never calls pip, so the runtime image does without it. Its
# vendored urllib3, msgpack and setuptools carry fixable HIGH CVEs (CVE-2025-47273,
# CVE-2026-97687, CVE-2026-97689, GHSA-6v7p-g79w-8964) that the release scan rejects, and pip
# 26.2.1, the newest release, still vendors the affected versions.
RUN /usr/local/bin/python -m pip uninstall --yes pip
# pg_dump and pg_restore for the nightly backup job and `spanlight restore`. The major version is
# pinned to the server's (a dump from a newer client cannot be restored by an older one, and
# pg_dump refuses a server newer than itself). The PGDG key is checked against its published
# fingerprint before apt trusts it; the tools used to fetch it are removed again.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    gpg --show-keys --with-colons /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        | grep -q '^fpr:::::::::B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8:'; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-17; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*
RUN groupadd --system app && useradd --system --gid app --home /app app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=backend-deps /opt/venv /opt/venv
COPY --from=backend-deps /app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"
# Railway / compose override the command for the worker: python -m app.jobs.worker
# The owner URL a platform hands to a pre-deploy migration step never reaches the app process,
# and exec makes uvicorn PID 1 so it gets SIGTERM and shuts down gracefully.
CMD ["sh", "-c", "unset MIGRATION_DATABASE_URL; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

# ---------- Web (Caddy serving the SPA, proxying API) ----------
# 2.11 is built with a patched Go toolchain (1.26.x) and patched quic-go, x/net, x/crypto and
# grpc modules; 2.10.2 (Go 1.25.0) failed the release scan with 59 fixable HIGH/CRITICAL findings.
FROM caddy:2.11-alpine AS web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend-build /src/frontend/dist /srv
COPY --from=docs-build /src/docs-site/dist /srv/docs
COPY --from=docs-build /src/docs-site/.build/docs-csp.caddy /etc/caddy/docs-csp.caddy
# Caddy runs unprivileged (numeric ids, so orchestrators can verify "non-root"). It listens on
# 8080 and only reads /srv and /etc/caddy; /data and /config are where it keeps state. The base
# image's caddy binary carries the file capability cap_net_bind_service, which port 8080 does not
# need: a fresh copy of the binary has no file capabilities (cp does not preserve extended
# attributes), so the process starts with an empty capability set. PORT must stay above 1023.
RUN addgroup -S -g 10001 spanlight \
    && adduser -S -u 10001 -G spanlight -h /nonexistent -H spanlight \
    && chown -R spanlight:spanlight /data /config \
    && cp /usr/bin/caddy /tmp/caddy && mv -f /tmp/caddy /usr/bin/caddy && chmod 0755 /usr/bin/caddy
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD wget -q --spider "http://127.0.0.1:${PORT:-8080}/" || exit 1

# ---------- Default stage: whichever target SPANLIGHT_TARGET names ----------
FROM ${SPANLIGHT_TARGET}
