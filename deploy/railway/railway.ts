// Railway project for the Spanlight public demo: Railway Postgres plus
// three services built from deploy/Dockerfile.
//
//   api     FastAPI. Pre-deploy runs migrations as the Postgres owner, then the
//           app connects as the non-superuser spanlight_app role so RLS applies.
//   worker  Same image, `python -m app.jobs.worker`. No HTTP, no healthcheck.
//   web     Caddy: serves the SPA and proxies /api, /v1, /health and /gw (the LLM
//           gateway, embedded in api) to api over the private network. The only
//           service with a public domain.
//
// This is a Railway Infrastructure as Code file. Config as Code (railway.toml,
// railway.json) is deprecated and new services can't use it. Apply from the
// repository root:
//
//   npm install --prefix deploy/railway           # once: the `railway` SDK
//   railway config plan  --file deploy/railway/railway.ts
//   railway config apply --file deploy/railway/railway.ts
//
// Step by step, including secrets, domain and verification: docs/deploy/railway.md
//
// "${{service.VAR}}" strings are Railway reference variables, resolved by Railway
// when it deploys. Secrets never live in this file: preserve() keeps the value set
// with `railway variable set NAME --stdin`. A variable that exists in Railway but
// not here is deleted on the next apply, so declare new ones here too.
//
// preserve() expects the variable to exist: an apply stops on one that has no value yet (see
// docs/deploy/railway.md). So the optional settings of later features are commented-out opt-in
// blocks below. To enable a feature, set its variables with `railway variable set NAME --stdin
// --service <service> --skip-deploys`, uncomment its block on each service named, then apply.

import { defineRailway, postgres, preserve, project, service } from "railway/iac";

const DOCKERFILE = "deploy/Dockerfile";

export default defineRailway(() => {
  // Railway's managed Postgres. Its superuser is PGUSER (postgres).
  const db = postgres("postgres");

  // host:port/database of Postgres on the private network, for both URLs below.
  const pgEndpoint = "${{postgres.PGHOST}}:${{postgres.PGPORT}}/${{postgres.PGDATABASE}}";

  const api = service("api", {
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: DOCKERFILE,
      // Only used once the service deploys from GitHub; `railway up` always builds.
      watchPatterns: ["/backend/**", "/deploy/Dockerfile"],
    },
    // Uses the image CMD: uvicorn on $PORT with proxy headers from Caddy. The CMD unsets
    // MIGRATION_DATABASE_URL first, so the owner credentials never reach the app process.
    // Before each deploy, as the Postgres owner: migrate, then create or refresh
    // the spanlight_app role (password from APP_DB_PASSWORD). A failure stops the deploy.
    preDeploy:
      "sh -c 'export DATABASE_URL=\"$MIGRATION_DATABASE_URL\" && spanlight migrate && spanlight ensure-app-role --role spanlight_app'",
    // Liveness only. /health/ready also needs a worker heartbeat, and the worker is deployed
    // after the api, so a readiness gate here would never go green on a fresh install. Point an
    // external uptime monitor at /health/ready instead.
    healthcheck: "/health/live",
    healthcheckTimeout: 120,
    deploy: {
      preDeployTimeoutSeconds: 300,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      sleepApplication: false,
      drainingSeconds: 15,
    },
    env: {
      SPANLIGHT_TARGET: "backend", // build arg: which deploy/Dockerfile stage to ship
      PORT: "8000",
      // With a custom domain, replace this with "https://<your domain>" (docs/deploy/railway.md, "Custom domain").
      APP_BASE_URL: "https://${{web.RAILWAY_PUBLIC_DOMAIN}}",
      DATABASE_URL: "postgresql+psycopg://spanlight_app:${{APP_DB_PASSWORD}}@" + pgEndpoint,
      // Owner connection, read only by the pre-deploy command above.
      MIGRATION_DATABASE_URL:
        "postgresql+psycopg://${{postgres.PGUSER}}:${{postgres.PGPASSWORD}}@" + pgEndpoint,
      // The api only starts demo sessions; the worker holds the Anthropic key.
      DEMO_ENABLED: "true",
      SECRET_KEY: preserve(),
      APP_DB_PASSWORD: preserve(),
      // Bearer token for /metrics. Caddy doesn't proxy that path, so only something on the
      // private network can scrape it; without a token /metrics answers 404.
      METRICS_TOKEN: preserve(),
      // Master keys for application-level encryption: "<id>:<base64 of 32 bytes>[,...]". The
      // worker must hold the same value, because it opens what the api seals.
      CREDENTIALS_KEYS: preserve(),
      // The LLM gateway runs inside this service (/gw/*, routed by Caddy in web). The public demo
      // is open to every signed-up user, so one organization may send at most this many gateway
      // requests a minute across all its keys. Raise or remove it on a private deployment.
      GATEWAY_ORG_RPM_CEILING: "60",
      // Sentry DSN, set in step 3 of docs/deploy/railway.md. Blank turns error reporting off.
      SENTRY_DSN: preserve(),
      // Object storage (exports and backups): one feature, uncommented on the api AND the worker
      // together, with the same values on both. The api accepts exports and signs their download
      // links; the worker writes the export files, deletes a deleted project's files and runs the
      // backups. With this block on the api alone, every export is accepted and then fails.
      // Set with railway variable set ... first. All of S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY,
      // or none (the app refuses a partial set).
      // S3_ENDPOINT: preserve(),
      // S3_PUBLIC_ENDPOINT: preserve(),
      // S3_BUCKET: preserve(),
      // S3_REGION: preserve(),
      // S3_ACCESS_KEY: preserve(),
      // S3_SECRET_KEY: preserve(),
      // S3_FORCE_PATH_STYLE: preserve(),
      // Self-tracing: uncomment and set with railway variable set ... to enable.
      // OTEL_EXPORTER_OTLP_ENDPOINT: preserve(),
      // OTEL_SERVICE_NAME: preserve(),
      // Api without a worker: uncomment and set to false with railway variable set ... to let
      // /health/ready pass without a worker heartbeat.
      // WORKER_REQUIRED: preserve(),
    },
  });

  const worker = service("worker", {
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: DOCKERFILE,
      watchPatterns: ["/backend/**", "/deploy/Dockerfile"],
    },
    start: "python -m app.jobs.worker",
    deploy: {
      // Crashes until the api's first pre-deploy has created spanlight_app; restarts cover that.
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      // Polls Postgres every 2 s, so it never sleeps anyway.
      sleepApplication: false,
      // A job runs for at most 50 s; let the current one finish on redeploy.
      drainingSeconds: 60,
    },
    env: {
      SPANLIGHT_TARGET: "backend",
      DATABASE_URL: "postgresql+psycopg://spanlight_app:${{api.APP_DB_PASSWORD}}@" + pgEndpoint,
      // Hard monthly cap on live demo traffic to Claude Haiku.
      DEMO_MONTHLY_BUDGET_USD: "1.00",
      ANTHROPIC_API_KEY: preserve(),
      // Identical to the api's; see there.
      CREDENTIALS_KEYS: preserve(),
      // Same project as the api's, so failed jobs show up next to request errors.
      SENTRY_DSN: preserve(),
      // Object storage (exports and backups): uncomment together with the api's block of the same
      // name, with the same values (see there). All of S3_BUCKET, S3_ACCESS_KEY and S3_SECRET_KEY,
      // or none.
      // S3_ENDPOINT: preserve(),
      // S3_PUBLIC_ENDPOINT: preserve(),
      // S3_BUCKET: preserve(),
      // S3_REGION: preserve(),
      // S3_ACCESS_KEY: preserve(),
      // S3_SECRET_KEY: preserve(),
      // S3_FORCE_PATH_STYLE: preserve(),
      // Nightly backups (needs the object storage above): uncomment and set with railway variable
      // set ... to enable. BACKUP_DATABASE_URL must be a role that bypasses row-level security (the
      // Postgres owner); BACKUPS_ENABLED=false turns the job off.
      // BACKUPS_ENABLED: preserve(),
      // BACKUP_DATABASE_URL: preserve(),
      // Self-tracing: uncomment and set with railway variable set ... to enable.
      // OTEL_EXPORTER_OTLP_ENDPOINT: preserve(),
      // OTEL_SERVICE_NAME: preserve(),
      // Worker /metrics (needs a scraper in the project): uncomment and set with railway variable
      // set ... to enable. The endpoint answers 404 without METRICS_TOKEN; use the api's value.
      // METRICS_TOKEN: preserve(),
      // WORKER_METRICS_PORT: preserve(),
    },
  });

  const web = service("web", {
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: DOCKERFILE,
      // The web image also builds the documentation site from these inputs.
      watchPatterns: [
        "/frontend/**",
        "/docs-site/**",
        "/docs/**",
        "/CHANGELOG.md",
        "/SECURITY.md",
        "/backend/openapi.json",
        "/deploy/Caddyfile",
        "/deploy/Dockerfile",
      ],
    },
    // Served by Caddy itself, so web can go live before api.
    healthcheck: "/",
    healthcheckTimeout: 60,
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      // Sleeping saves cents and makes a visitor's first request slow or a 502.
      sleepApplication: false,
      drainingSeconds: 10,
    },
    env: {
      SPANLIGHT_TARGET: "web",
      PORT: "8080",
      API_UPSTREAM: "${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}",
      // /gw/* (the LLM gateway, embedded in api) goes to the same private address. Without it
      // Caddy falls back to api:8000, which does not resolve on Railway, and every call is a 502.
      GATEWAY_UPSTREAM: "${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}",
      // Railway's edge is the only way in and sets X-Real-IP to the client address.
      // Lets Caddy pass it to the api for rate limits and session records.
      TRUSTED_PROXIES: "0.0.0.0/0 ::/0",
    },
  });

  return project("spanlight", { resources: [db, api, worker, web] });
});
