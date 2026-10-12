---
title: API keys, tokens and administration
description: Scoped project API keys, personal access tokens for scripts, and renaming or deleting organizations and projects.
sidebar:
  order: 2
---

Spanlight has two kinds of credential for programs. A **project API key** belongs to one project and is what your application uses to send traces. A **personal access token** belongs to a person and is what a script uses to call the dashboard API as that person. The routes, error codes and edge cases are in the [API conventions](/docs/api/conventions/).

| | Project API key | Personal access token |
| --- | --- | --- |
| Looks like | `spl_live_…` | `spl_pat_…` |
| Belongs to | One project | One person |
| Created in | **Settings, Project, API keys** | **Settings, Personal, Tokens** |
| Limited by | Scopes | A `read` or `write` scope, plus the person's roles |
| Sent as | `Authorization: Bearer <secret>` | `Authorization: Bearer <secret>` |

Both are shown **once**, when you create them. Spanlight stores only a hash, so a lost secret cannot be recovered; create another and revoke the old one.

## API key scopes and expiry

When you create a key you choose its scopes and, optionally, an expiry.

| Scope | Allows |
| --- | --- |
| `ingest:write` | Sending traces to `POST /v1/traces` and `POST /v1/otlp/traces`. This is the default. |
| `traces:read` | Reading the project's traces, sessions, filters and metrics through the dashboard API. |
| `scores:write` | Sending [scores](/docs/scores/) to `POST /v1/scores`, for example with `spanlight.score()`. |
| `prompts:read` | Reading [prompts](/docs/prompts/) from `GET /v1/prompts/{name}`, for example with `spanlight.prompts.get()`. |

Give an application the narrowest scopes it needs: an SDK that only sends traces needs only `ingest:write`, a reporting job needs only `traces:read`, and an application that fetches prompts or records scores adds `prompts:read` or `scores:write`. **A key keeps the scopes it was created with**, so a key made before scores and prompts existed cannot use them: create a new key that has the scope, and revoke the old one when nothing uses it. Tick them in the key dialog under **Settings, Project, API keys**, where all four are offered, or send them in the `scopes` list of `POST /api/v1/projects/{project_id}/keys` (see the [API conventions](/docs/api/conventions/)). A key used outside its scopes gets `403 KEY_SCOPE`. A key past its expiry gets `401 KEY_EXPIRED`, on ingestion and on reads. Keys created before scopes existed keep `ingest:write` and never expire.

A key can read only its own project. Any other project answers `404`, exactly like a project that does not exist.

Revoke a key at any time from the key list. See [Revoke a leaked key](/docs/runbooks/revoke-key/) for what to do when one has been exposed.

## Personal access tokens

A token acts as you. It has no permissions of its own: on every request Spanlight looks up your current role in the organization or project in the path, so removing you from an organization, or lowering your role, takes effect on the next call.

- A **read** token can only read. Any change is refused with `403 TOKEN_SCOPE`.
- A **write** token can do whatever your role allows.
- A token can have an expiry. After it, calls get `401 TOKEN_EXPIRED`.
- Organizations that require two-factor authentication apply the rule to your tokens as well.

Tokens work on routes that name an organization or project, on `GET /api/v1/auth/me` and on `GET /api/v1/prices`. They cannot manage sessions, two-factor authentication, linked accounts or other tokens: those need a signed-in browser. A token is not an API key, so it cannot send traces.

Creating and revoking tokens is recorded in the structured logs, never with the secret.

```bash
curl -H "Authorization: Bearer $SPANLIGHT_TOKEN" \
  https://spanlight.example.com/api/v1/auth/me
```

## Rate limits for credentials

Reads with a token or a key are limited to 20 requests a second per credential, with bursts of 40. Ingestion is limited to 50 requests a second per key, with bursts of 100. Over the limit the answer is `429 RATE_LIMITED` with a `Retry-After` header in seconds. Browser sessions and writes made with a token are not limited per credential. The limits are kept in Postgres and are shared by every API replica.

## Organization and project administration

Owners and admins manage the organization from **Settings, Organization** and projects from **Settings, Project**.

| Action | Who | Notes |
| --- | --- | --- |
| Rename an organization | Admin, owner | The slug does not change. |
| Require two-factor authentication | Owner | See [Accounts and sign-in](/docs/guides/accounts/). |
| Delete an organization | Owner | Deletes its projects, traces, spans, keys, members, invitations and audit log. |
| Rename a project | Admin, owner | |
| Delete a project | Admin, owner | Deletes its traces, spans, keys and export files. The audit log keeps a `project.delete` event. |

Deleting needs a typed confirmation: you type the **slug** of the organization or project, exactly, before the button works. Deleting cannot be undone, so export what you need first (see [Data, limits and operations](/docs/guides/data/)). The shared demo organization cannot be changed or deleted.

Organization admins can read the **audit log** in the same section, filter it by action, person and date, and download it as CSV. Renames, the two-factor requirement and project deletion are recorded there; deleting an organization removes its audit log with it, and the deletion is noted only in the server log.
