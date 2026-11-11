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
# The legal pages (/privacy, /terms, ...) name the spanlight.dev operator. Set to 1 only for
# spanlight.dev; leave it empty for a self-hosted build so they are left out.
ARG LEGAL_PAGES=
ENV VITE_LEGAL_PAGES=$LEGAL_PAGES
RUN npm run build
# Licence notices for the production npm packages, served at /third-party-notices.txt. The run
# fails if a package declares no licence.
COPY scripts/licenses/npm_notices.mjs /src/scripts/licenses/
RUN node /src/scripts/licenses/npm_notices.mjs --output dist/third-party-notices.txt

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
# Same flag as the frontend: 1 only for spanlight.dev, whose operator the legal pages name.
ARG LEGAL_PAGES=
ENV LEGAL_PAGES=$LEGAL_PAGES
WORKDIR /src/docs-site
COPY docs-site/package.json docs-site/package-lock.json ./
# npm skips an optional dependency it fails to download, so Pagefind's binary for this platform
# can be silently missing and the docs build fails much later (seen in CI on 2026-10-20). Check it
# right after the install and retry once.
RUN npm ci \
    && PAGEFIND_BIN="@pagefind/$(node -p 'process.platform + "-" + process.arch')/package.json" \
    && (node -e "require.resolve('$PAGEFIND_BIN')" 2>/dev/null \
        || (echo "Pagefind binary missing after npm ci; retrying once" && rm -rf node_modules && npm ci)) \
    && node -e "require.resolve('$PAGEFIND_BIN')"
COPY CHANGELOG.md SECURITY.md /src/
COPY backend/openapi.json /src/backend/openapi.json
COPY backend/app/pricing/prices.py /src/backend/app/pricing/prices.py
COPY docs/runbooks /src/docs/runbooks
COPY docs/security /src/docs/security
COPY docs/deploy /src/docs/deploy
COPY docs/api-deviations.md /src/docs/api-deviations.md
COPY docs-site/ ./
RUN npm run build
# Licence notices for the docs site's production npm packages, served at /docs/third-party-notices.txt.
COPY scripts/licenses/npm_notices.mjs /src/scripts/licenses/
RUN node /src/scripts/licenses/npm_notices.mjs --output dist/third-party-notices.txt

# ---------- Python dependencies ----------
FROM python:3.13-slim AS backend-deps
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Licence notices for every Python package in the runtime venv, written before the project
# itself is installed so only third-party packages are listed. The run fails if a package
# declares no licence.
COPY scripts/licenses/python_notices.py /tmp/python_notices.py
RUN /opt/venv/bin/python /tmp/python_notices.py --output /app/THIRD_PARTY_NOTICES.txt
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
# `apt-get upgrade` takes Debian security fixes the base image has not been rebuilt with yet
# (zlib1g, CVE-2026-85091), which the release scan rejects.
RUN set -eux; \
    apt-get update; \
    apt-get upgrade -y; \
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
# and exec makes the shared launcher PID 1 so it gets SIGTERM and shuts down gracefully.
CMD ["sh", "-c", "unset MIGRATION_DATABASE_URL; exec python -m app.server"]

# ---------- Caddy binary ----------
# The official caddy:2.11-alpine image (Caddy 2.11.7) ships a binary built with Go 1.26.8 and
# golang.org/x/net 0.59.0, which failed the 0.6.0 release scan (CVE-2026-78667, CVE-2026-78669,
# CVE-2026-97031). The same Caddy release is rebuilt here with a patched toolchain, x/net and
# go-jose; the 0.8.0 scan raised all three again (CVE-2026-111943, CVE-2026-78661, CVE-2026-116587).
# shortcut: pinned by hand, drop this stage once the official image ships Go >= 1.27.3.
FROM golang:1.27.3-alpine AS caddy-build
WORKDIR /src
RUN printf '%s\n' 'package main' \
      'import (' \
      '	caddycmd "github.com/caddyserver/caddy/v2/cmd"' \
      '	_ "github.com/caddyserver/caddy/v2/modules/standard"' \
      ')' \
      'func main() { caddycmd.Main() }' > main.go \
    && go mod init spanlight/caddy \
    && go get github.com/caddyserver/caddy/v2@v2.11.7 golang.org/x/net@v0.62.0 \
       github.com/go-jose/go-jose/v4@v4.1.6 \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /caddy .
# Licence notices for what the binary statically links: the Go standard library and every module
# `go version -m` lists, with each module's LICENSE / COPYING / NOTICE files from the module
# cache. A module without a licence file fails the build.
RUN set -eu -o pipefail; \
    rule() { printf '%80s\n' '' | tr ' ' "$1"; }; \
    out=/third-party-notices-caddy.txt; \
    { echo 'Third-party software compiled into the Caddy web server binary of this image, with'; \
      echo 'their licences: Caddy and the Go modules it is built from, and the Go standard library.'; \
      echo; rule '='; echo "Go standard library $(go env GOVERSION)"; rule '-'; \
      cat "$(go env GOROOT)/LICENSE"; } > "$out"; \
    go list -m -f '{{.Path}} {{.Version}} {{.Dir}}' $(go version -m /caddy | awk '$1 == "dep" {print $2}') \
    | while read -r path version dir; do \
        files=$(find "$dir" -maxdepth 1 -type f \( -iname 'licen[cs]e*' -o -iname 'copying*' -o -iname 'notice*' \) | sort); \
        [ -n "$files" ] || { echo "Go module without a licence file: $path $version" >&2; exit 1; }; \
        { echo; rule '='; echo "$path $version"; rule '-'; for f in $files; do cat "$f"; echo; done; } >> "$out"; \
    done

# ---------- Web (Caddy serving the SPA, proxying API) ----------
FROM caddy:2.11-alpine AS web
COPY --from=caddy-build /caddy /usr/bin/caddy
COPY --from=caddy-build /third-party-notices-caddy.txt /srv/third-party-notices-caddy.txt
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
