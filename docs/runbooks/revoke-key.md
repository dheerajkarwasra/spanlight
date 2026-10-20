# Revoke an API key or access token, and recover a lost authenticator

## Purpose and when to use

- A project API key (`spl_live_...`) leaked, for example in a public repository or a log.
- A personal access token (`spl_pat_...`) leaked, or its owner left the team.
- A user's session may be stolen, or someone has to be signed out everywhere.
- A user lost the phone with their authenticator app and the recovery codes.

A revoked access token or session is refused on its next request: they are read from the database every time. A revoked API key takes effect within `API_KEY_CACHE_TTL_SECONDS` (10 seconds by default) on each api process, because each process caches the key rows it has looked up for that long. The process that served the dashboard's revoke drops its copy at once; the others, and every process after a SQL revocation, refuse the key once their copy is older than the TTL. Set `API_KEY_CACHE_TTL_SECONDS=0` if revocation must be immediate everywhere.

## Prerequisites

- To use the dashboard: an organization role that may revoke the key. A member can revoke the keys they created; an admin or owner can revoke any key of the project.
- To use SQL: shell access to the host and the database owner (`postgres` in the Compose stack). This path skips the audit event, so use it only when the dashboard is not available.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

## Revoke a project API key

A key looks like `spl_live_` followed by 12 characters, an underscore and 32 more. The first 21 characters (`spl_live_` plus the 12) are the key's **prefix**: it identifies the key and is safe to write down. The rest is the secret; never paste a full key into a ticket or chat.

### Steps (dashboard, preferred)

1. Sign in, open the project that owns the key, and go to **API keys**. Find the key by its name or prefix.
2. Revoke it. Revoked keys stay listed with a revoked time, so the audit trail keeps them.
3. Create a replacement key on the same page. The secret is shown once, at creation.
4. Put the new key into every sender that used the old one, as `SPANLIGHT_API_KEY` for the Python SDK or as the bearer token of your OTLP exporter. The SDK never raises into your application, so a sender with a revoked key does not crash; its batches are rejected with 401 and, unless the application has logging enabled, nobody notices. Watch the dashboard for a project that goes quiet to find senders you forgot.

Only a signed-in session creates keys. A key cannot create or revoke keys, not even itself.

### Steps (SQL, when the dashboard is unavailable)

1. Identify the key from its prefix (the first 21 characters of what leaked):

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT id, name, prefix, project_id, scopes, created_by, last_used_at, revoked_at
        FROM api_keys WHERE prefix = 'spl_live_XXXXXXXXXXXX'"
   ```

   Expect exactly one row. `last_used_at` is updated at most once a minute while the key is in use.

2. Revoke it:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "UPDATE api_keys SET revoked_at = now()
       WHERE prefix = 'spl_live_XXXXXXXXXXXX' AND revoked_at IS NULL"
   ```

   Expected: `UPDATE 1`. `UPDATE 0` means the key was already revoked or the prefix is wrong.

3. Create the replacement in the dashboard as above, and record in your incident notes that this revocation has no `key.revoke` audit event.

### Verify

Wait `API_KEY_CACHE_TTL_SECONDS` (10 seconds by default) after revoking, then send one request with the old key. Read it without echoing it:

```bash
read -rs OLD_KEY; echo
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/v1/traces" \
  -H "Authorization: Bearer $OLD_KEY" -H 'Content-Type: application/json' -d '{"spans":[]}'
unset OLD_KEY
```

Expected: `401`. (The body is problem+json with code `UNAUTHORIZED` and the detail `Missing, invalid or revoked API key.`) Then confirm the replacement works: its `last_used_at` moves, and new traces arrive in the dashboard.

If the key had the `traces:read` scope, the leak also exposed the project's traces to whoever held it. Treat the project's prompts and completions as disclosed to that party, and read the key's `last_used_at` and your access logs to bound the exposure. The scopes are in the `scopes` column and in the dashboard's key list.

### Roll back

A revoked key cannot be revived from the dashboard. To undo a mistaken SQL revocation, `UPDATE api_keys SET revoked_at = NULL WHERE id = '<id>'`. For a leaked key, never do this.

## Revoke a personal access token

A personal access token (`spl_pat_<12 characters>_<32 characters>`) acts as its owner on the dashboard API, with the owner's current role. It has a `read` or `write` scope. Removing the owner from an organization ends the token's access to that organization at once, because its reach is computed from the owner's memberships on every request. Tokens are managed only with a signed-in session: a token cannot list, create or revoke tokens.

### Steps (the owner, with a session)

```
GET    /api/v1/auth/tokens            lists the caller's usable tokens (never a secret)
DELETE /api/v1/auth/tokens/{token_id} revokes one; repeating it is harmless; 204 No Content
```

These require the session cookie and the CSRF header, like any write from the dashboard.

### Steps (operator, SQL)

1. Find the token. Its prefix is the first 20 characters (`spl_pat_` plus 12), or find all tokens of a person:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "SELECT t.id, t.name, t.prefix, t.scope, t.last_used_at, t.expires_at, t.revoked_at
        FROM personal_access_tokens t JOIN users u ON u.id = t.user_id
       WHERE u.email = 'ada@example.com'"
   ```

2. Revoke one token, or all of a person's:

   ```bash
   spl exec postgres psql -U postgres -d spanlight -c \
     "UPDATE personal_access_tokens SET revoked_at = now()
       WHERE prefix = 'spl_pat_XXXXXXXXXXXX' AND revoked_at IS NULL"

   spl exec postgres psql -U postgres -d spanlight -c \
     "UPDATE personal_access_tokens SET revoked_at = now()
       WHERE revoked_at IS NULL
         AND user_id = (SELECT id FROM users WHERE email = 'ada@example.com')"
   ```

   Expected: `UPDATE <n>`.

### Verify

A request with the revoked token is `401` with code `UNAUTHORIZED` (`Missing, invalid or revoked access token.`):

```bash
read -rs OLD_PAT; echo
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $OLD_PAT" "$BASE/api/v1/auth/me"
unset OLD_PAT
```

Expected: `401`.

## End every session of a user

Use it when a password, a laptop or a browser session is compromised, or when someone leaves.

```bash
spl exec worker spanlight reset-password --email ada@example.com
```

The command prompts twice for a new password (at least the minimum length; nothing is echoed). Expected: `Password reset for <ada@example.com>; all sessions revoked.` The user is signed out on every device and has to sign in with the new password. It also records a `user.password_reset` audit event in each of the user's organizations. Give the new password to the user over a channel you trust, and tell them to change it.

Without changing the password (to sign out a user who only uses a provider sign-in, for example), delete the sessions directly. Their cookies stop working on the next request:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE email = 'ada@example.com')"
```

Expected: `DELETE <n>`.

To sign out everyone in an emergency, `DELETE FROM sessions` with no condition does it. Every user then has to sign in again, and rotating `SECRET_KEY` is not needed for this: see [rotate-secrets.md](rotate-secrets.md#secret_key) for what it does and does not do.

To remove a person's access altogether, remove them from the organization (the dashboard's member list, or `DELETE /api/v1/orgs/{org_id}/members/{user_id}`), revoke their tokens as above, and end their sessions.

## Recover a lost authenticator (two-factor reset)

### When to use

A user turned on two-factor authentication and lost both the authenticator app and the recovery codes. Anyone who still has a recovery code can turn it off themselves in their account settings; use this only when they cannot.

### Steps

1. Confirm the person is who they say they are, by a channel that does not depend on the lost device. Spanlight cannot do this for you. Anyone who can ask you for this reset can take over the account if you skip this step.
2. Turn two-factor authentication off:

   ```bash
   spl exec worker spanlight reset-2fa --email ada@example.com
   ```

   Expected: `Two-factor authentication turned off for <ada@example.com>; recovery codes deleted.` If it was not on: `Two-factor authentication is not turned on for <ada@example.com>; nothing changed.` An unknown address fails with `no user with email 'ada@example.com'`.

   The command deletes the stored seed and the recovery codes and records a `user.totp_disable` audit event (with `via: cli`) in each of the user's organizations. The password and the sessions stay as they are.
3. The user signs in with their password alone, then turns two-factor on again in their account settings and stores the new recovery codes. If an organization requires two-factor authentication, the dashboard sends them to set it up before it lets them continue.
4. If the lost device may be in someone else's hands, also end their sessions and change their password (previous section).

### Verify

The user signs in without a code prompt, and the account settings show two-factor as off until they enrol again. The audit log of the organization shows the reset.
