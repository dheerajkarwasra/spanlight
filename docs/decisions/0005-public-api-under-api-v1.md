# 5. The dashboard API lives under /api/v1

Date: 2026-10-08 · Status: accepted

## Context

Ingestion was versioned from the start (`/v1/traces`, `/v1/otlp/traces`) because SDKs and collectors in the field cannot be upgraded in step with the server. The dashboard API was not: its routes were served from unversioned `/api/<resource>` paths.

That was acceptable while its only client was the web app, which is built from the same repository and deployed together with the server. It stops being acceptable once scripts, integrations and scoped API tokens call it. Paths that clients have already hard-coded cannot be renamed without breaking them, so the prefix has to be fixed before there are external consumers, not after.

## Decision

Serve every dashboard route under `/api/v1`: `/api/auth/me` becomes `/api/v1/auth/me`, `/api/projects/{id}/traces` becomes `/api/v1/projects/{id}/traces`, and so on. The old paths are removed, not redirected, because no external client exists yet and the web app moves in the same release.

These stay where they are, because they are not part of the dashboard API surface:

- `/v1/traces` and `/v1/otlp/traces`, which carry their own version.
- `/health/live`, `/health/ready` and `/metrics`, which belong to operators.
- `/api/openapi.json` and `/api/docs`, which describe the versioned API and are not part of it.

The Origin allowlist and CSRF checks apply to every state-changing request under `/api/`, so a future `/api/v2` is covered without further work. The reverse proxy already forwards `/api/*`, so deployments need no proxy change. The web app builds all of its API paths from one prefix constant.

## Consequences

- Breaking changes (removed or renamed fields, changed semantics, new required parameters) ship as `/api/v2`. Additive changes stay in `/api/v1`.
- Once `/api/v2` exists, `/api/v1` keeps working for at least 12 months, and its responses carry a `Deprecation` header from that point on.
- Anything written against the unversioned paths breaks and must switch to `/api/v1`. Self-hosters who alert on the `route` label of `spanlight_http_requests_total` or `spanlight_http_request_duration_seconds` need to update their queries, because the label now carries the prefix (`/api/v1/projects/{project_id}/traces`).
- Running two versions side by side means maintaining two sets of routes and schemas for the overlap. That cost is accepted; it is paid only when a breaking change is actually needed.

## Exception: bounded span content (2026-11-02)

`GET /api/v1/projects/{project_id}/traces/{trace_id}` and `GET …/sessions/{session_id}/turns` bound the stored content they return (attributes, input and output together) to 8 MiB per response. A request that would need to leave attributes out is refused with `422 SPAN_CONTENT_TOO_LARGE` unless it asks for the bounded representation with `span_content=bounded`, in which case every span is returned and the withheld fields are flagged (`attributes_omitted`, `payload_omitted`); `GET …/spans/{span_id}` returns any one span whole. Unqualified reads whose content fits keep complete attributes and every existing field meaning. This is a narrow resource-limit exception to the promise above, effective from the release that carries this note: reads that previously succeeded can now fail explicitly when a trace's stored content is that large, so that no client ever receives withheld attributes as an empty object. Clients add the selector and read both flags to migrate. Everything else about `/api/v1` is unchanged.
