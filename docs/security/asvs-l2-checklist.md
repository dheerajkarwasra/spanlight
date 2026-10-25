# OWASP ASVS 4.0.3 Level 2 checklist

A self-assessment of Spanlight against the [OWASP Application Security Verification Standard 4.0.3](https://github.com/OWASP/ASVS/tree/v4.0.3), Level 2. It covers the Level 1 and Level 2 requirements of chapters V1 to V14 that apply to Spanlight's shape: a JSON API, a single-page dashboard, an ingestion endpoint and background workers. The [threat model](threat-model.md) explains the boundaries and lists the residual risks referred to below as R1 to R16.

How to read it:

- **Met:** the requirement holds in the code as shipped. The reference names the module that implements it.
- **Partial:** part of it holds, or it holds only with a deployment choice the operator makes. The note says which part is missing.
- **Not met:** not implemented.
- **N/A:** the requirement is about something Spanlight does not have, or is entirely the operator's.

Requirement texts are shortened; the standard has the full wording. A reference such as `core/security.py` is a path under `backend/app/`; a reference that starts with a top-level directory (`deploy/`, `frontend/`, `.github/`, `backend/tests/`) is a path from the repository root. Left out entirely: Level 3-only requirements, requirements the standard marks as deleted, and sections with no counterpart in Spanlight: V2.7 out-of-band verifiers (email is used for recovery only, never as an authenticator), V2.9 cryptographic verifiers, V5.4.1 and V5.4.2 (Python and TypeScript are memory-safe), V13.3 SOAP and V13.4 GraphQL.

Assessed in October 2026 against the code before the first `1.0` release.

## Summary

| Chapter | Met | Partial | Not met | N/A |
|---|---|---|---|---|
| V1 Architecture | 27 | 11 | 0 | 0 |
| V2 Authentication | 19 | 8 | 13 | 3 |
| V3 Session management | 12 | 3 | 2 | 1 |
| V4 Access control | 7 | 2 | 0 | 0 |
| V5 Validation, sanitization and encoding | 21 | 0 | 0 | 7 |
| V6 Stored cryptography | 6 | 3 | 1 | 3 |
| V7 Error handling and logging | 7 | 4 | 0 | 1 |
| V8 Data protection | 8 | 4 | 2 | 1 |
| V9 Communication | 2 | 3 | 0 | 3 |
| V10 Malicious code | 2 | 1 | 0 | 2 |
| V11 Business logic | 2 | 4 | 2 | 0 |
| V12 Files and resources | 7 | 3 | 0 | 5 |
| V13 API and web services | 5 | 4 | 0 | 0 |
| V14 Configuration | 16 | 5 | 1 | 1 |
| **Total** | **141** | **55** | **21** | **27** |

## V1 Architecture, design and threat modeling

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 1.1.1 | Secure development lifecycle | Met | Every change passes lint, type checks, tests against a real Postgres, CodeQL and the compose smoke test before release | `.github/workflows/ci.yml`, `.github/workflows/codeql.yml`, `CONTRIBUTING.md` |
| 1.1.2 | Threat modeling for design changes | Partial | A threat model exists; reviewing it on every design change is a stated rule, not yet an enforced step | [threat model](threat-model.md) |
| 1.1.3 | Security constraints in user stories | Partial | Security rules are written into the code's docstrings and the contribution rules, not into user stories | `CONTRIBUTING.md` |
| 1.1.4 | Trust boundaries, components and data flows documented | Met | | [threat model](threat-model.md) §1 and §4 |
| 1.1.5 | High-level architecture and remote services analysed | Met | | [threat model](threat-model.md) §4, TB5 and TB6 |
| 1.1.6 | Centralized, reusable security controls | Met | One credential module, one authorization dependency, one permission table, one redaction module | `core/security.py`, `api/deps.py`, `core/permissions.py`, `core/redact.py` |
| 1.1.7 | Secure coding checklist available | Partial | Contribution rules cover secrets, RLS and tests; there is no standalone checklist | `CONTRIBUTING.md` |
| 1.2.1 | Low-privilege OS accounts per component | Met | The backend image runs as user `app` and the web image runs Caddy as uid 10001 on port 8080 | `deploy/Dockerfile` |
| 1.2.2 | Communications between components authenticated | Partial | Postgres needs a password; `/metrics` needs a bearer token; Caddy to api relies on network isolation (R13, R14) | `deploy/compose.yaml`, `api/health.py` |
| 1.2.3 | Single vetted authentication mechanism | Met | Sessions and bearer credentials are resolved in one dependency, with one key format for every bearer kind | `api/deps.py` (`current_principal`), `core/security.py` |
| 1.2.4 | All authentication pathways equally strong | Met | Password and provider sign-ins both go through the second factor; tokens are refused where an organization requires two-factor authentication and the user lacks it | `api/v1/oauth.py` (`finish_sign_in`), `api/deps.py` (`_member_access`) |
| 1.4.1 | Access control enforced at trusted points | Met | Server-side dependency plus Postgres row-level security | `api/deps.py` (`require`), `db/rls.py` |
| 1.4.4 | Single vetted access control mechanism | Met | | `api/deps.py` (`require`), `core/permissions.py` |
| 1.4.5 | Attribute- or feature-based access control | Met | Handlers declare permissions, never compare roles; keys carry scopes; tokens carry a read or write scope | `core/permissions.py`, `core/scopes.py` |
| 1.5.1 | Input and output requirements defined | Partial | Every request and response has a typed schema; data classification is in the threat model only | `api/schemas/`, `ingest/schemas.py` |
| 1.5.2 | No serialization with untrusted clients | Met | JSON and OTLP protobuf only; no pickle or native object formats | `ingest/pipeline.py`, `ingest/otlp.py` |
| 1.5.3 | Input validation on a trusted service layer | Met | | `api/schemas/`, `ingest/schemas.py`, `ingest/normalize.py` |
| 1.5.4 | Output encoding close to the interpreter | Met | JSON serialization in the API; React escaping in the dashboard; HTML escaping in email templates | `email/templates.py` |
| 1.6.1 | Cryptographic key management policy | Partial | Rotation procedures are documented; no formal key-management policy | [rotate-secrets runbook](../runbooks/rotate-secrets.md), [ADR 0009](../decisions/0009-application-master-key-encryption.md) |
| 1.6.2 | Key material protected (vault or equivalent) | Partial | Keys come from the environment; a secret manager is the operator's choice | `config.py` |
| 1.6.3 | Keys and passwords replaceable | Met | `CREDENTIALS_KEYS` is a keyring with ids; every secret has a rotation procedure | `core/crypto.py`, [rotate-secrets runbook](../runbooks/rotate-secrets.md) |
| 1.6.4 | Client-side secrets not trusted | Met | The browser holds only its own session and CSRF values | `api/deps.py` |
| 1.7.1 | Common logging format | Met | structlog JSON for the app and for the stdlib loggers it routes | `core/logging.py` |
| 1.7.2 | Logs sent to a remote system | Partial | Logs go to stdout for the platform to collect; error reporting to Sentry is optional | `core/logging.py`, `core/sentry.py` |
| 1.8.1 | Sensitive data identified and classified | Met | | [threat model](threat-model.md) §2 |
| 1.8.2 | Protection requirements per protection level | Partial | Assets are listed with their protection, without formal levels | [threat model](threat-model.md) §2 |
| 1.9.1 | Encryption between components | Partial | Depends on the deployment (R14) | `deploy/compose.yaml` |
| 1.9.2 | Components verify each other's authenticity | Partial | Outbound TLS verifies certificates; internal hops rely on the private network (R14) | `storage/s3.py`, `auth/oauth_client.py` |
| 1.10.1 | Source control with change review | Met | Git, code owners, pull request template | `.github/CODEOWNERS`, `.github/pull_request_template.md` |
| 1.11.1 | Components documented by function | Met | | [threat model](threat-model.md) §1 |
| 1.11.2 | High-value flows are thread safe | Met | Row locks, conditional updates and advisory locks on sign-in, reset, tokens, invites, throttles and deletion | `services/credentials.py`, `auth/email_tokens.py`, `core/throttle.py`, `services/deletion.py` |
| 1.12.2 | User files served as downloads or from another origin | Met | Export files are downloaded from the object store's own origin through presigned links | `exports/service.py`, `storage/s3.py` |
| 1.14.1 | Segregation of components by trust | Met | Only the web port is published; the api and Postgres sit on the private network | `deploy/compose.yaml` |
| 1.14.2 | Signed binaries and verified deployment | Met | Release images are signed with cosign and shipped with signed SBOMs; operators verify the signature and pin the digest before they deploy | `.github/workflows/release.yml`, [Verify a release](../../docs-site/src/content/docs/self-hosting/upgrades.mdx#verify-a-release) |
| 1.14.3 | Build pipeline flags outdated or insecure components | Met | Trivy fails the release on fixable HIGH or CRITICAL findings; Dependabot opens updates weekly | `.github/workflows/release.yml`, `.github/dependabot.yml` |
| 1.14.4 | Build verifies a secure deployment | Met | CI runs the compose smoke test, which asserts the security headers and the CSP | `.github/workflows/ci.yml`, `deploy/smoke.sh` |
| 1.14.5 | Deployment sandboxed and isolated | Met | Containers with a private network | `deploy/compose.yaml`, `deploy/Dockerfile` |
| 1.14.6 | No deprecated client-side technology | Met | | `frontend/` |

## V2 Authentication

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 2.1.1 | Passwords at least 12 characters | Not met | The minimum is 10 (R16) | `api/schemas/auth.py` (`NewPassword`) |
| 2.1.2 | At least 64 characters allowed, over 128 denied | Partial | Up to 256 characters are accepted | `api/schemas/auth.py` |
| 2.1.3 | No truncation | Met | argon2id hashes the whole password | `core/security.py` |
| 2.1.4 | Any printable Unicode allowed | Met | No character restrictions | `api/schemas/auth.py` |
| 2.1.5 | Users can change their password | Not met | Only through the emailed reset or the admin CLI (R16) | `api/v1/auth.py`, `cli.py` (`reset-password`) |
| 2.1.6 | Change needs the current password | Not met | No change-password route | |
| 2.1.7 | Breached-password check | Not met | (R16) | |
| 2.1.8 | Password strength meter | Not met | | `frontend/src/features/auth/signup-page.tsx` |
| 2.1.9 | No composition rules | Met | Length only | `api/schemas/auth.py` |
| 2.1.10 | No forced rotation or history | Met | | |
| 2.1.11 | Paste and password managers allowed | Met | Standard inputs with `autocomplete` hints | `frontend/src/features/auth/login-page.tsx` |
| 2.1.12 | Option to show the masked password | Not met | | `frontend/src/features/auth/` |
| 2.2.1 | Anti-automation against brute force and stuffing | Partial | Login, two-factor, reset, verification and signup (10 per IP per hour) are throttled in Postgres, and hashing runs off the event loop; limits fail open under pool exhaustion (R6), and many-address floods are bounded only by the proxy (R5) | `api/v1/auth.py` (`signup`), `auth/login_throttle.py`, `auth/password_reset.py`, `core/throttle.py`, `core/security.py` |
| 2.2.2 | Weak authenticators only as secondary | Met | Email is used for verification and recovery only | `auth/verification.py`, `auth/password_reset.py` |
| 2.2.3 | Notification after authentication details change | Not met | (R10) | `email/templates.py` |
| 2.3.1 | Initial secrets random and expiring | Met | 32 random bytes; reset 1 hour, verification 24 hours, invites 7 days; single use | `auth/email_tokens.py`, `api/v1/orgs.py` (`create_invite`) |
| 2.3.2 | Hardware authenticators (FIDO) supported | Not met | (R10) | |
| 2.3.3 | Renewal notice for time-bound authenticators | Not met | API keys and tokens can expire without a reminder | `api/v1/keys.py`, `api/v1/tokens.py` |
| 2.4.1 | Passwords stored with an approved one-way KDF | Met | argon2id | `core/security.py` |
| 2.4.2 | Salt of at least 32 bits | Met | 16-byte random salt per hash | `core/security.py` |
| 2.4.3 | PBKDF2 iteration count | N/A | argon2id is used | |
| 2.4.4 | bcrypt work factor | N/A | argon2id is used | |
| 2.4.5 | Additional secret salt (pepper) | Not met | | `core/security.py` |
| 2.5.1 | Recovery secret not sent in clear text | Partial | Reset links go by email, which is the recovery channel; they are single use, short lived, carried in the URL fragment and stored hashed | `auth/password_reset.py` (`reset_url`) |
| 2.5.2 | No password hints or knowledge-based answers | Met | | |
| 2.5.3 | Recovery does not reveal the current password | Met | | `auth/password_reset.py` |
| 2.5.4 | No shared or default accounts | Partial | No default administrator; the demo is a shared, read-only viewer account that cannot sign in with a password | `services/demo.py`, `core/security.py` (`UNUSABLE_PASSWORD_HASH`) |
| 2.5.5 | Notification when a factor changes | Not met | Changes are audited, not emailed (R10) | `services/audit.py` |
| 2.5.6 | Secure recovery mechanism | Met | Single-use emailed token; recovery codes for the second factor | `auth/password_reset.py`, `auth/totp.py` |
| 2.5.7 | Identity proofing when a second factor is lost | Partial | An operator can reset two-factor authentication with the CLI; the proofing is the operator's process | `cli.py` (`reset-2fa`) |
| 2.6.1 | Lookup secrets single use | Met | Recovery codes are spent on use | `auth/totp_service.py` |
| 2.6.2 | Lookup secrets have enough entropy | Not met | 50 bits, below 112 bits, and not salted (R10) | `auth/totp.py` (`new_recovery_codes`, `hash_recovery_code`) |
| 2.6.3 | Lookup secrets resist offline attacks | Not met | Unsalted SHA-256 of a 50-bit value (R10) | `auth/totp.py` |
| 2.8.1 | Time-based OTPs have a defined lifetime | Met | 30-second steps, one step of drift either way | `auth/totp.py` |
| 2.8.2 | OTP seeds protected | Partial | AES-256-GCM with a key from the environment, not an HSM | `core/crypto.py`, `auth/totp_service.py` |
| 2.8.3 | Approved algorithms for OTPs | Met | RFC 6238 TOTP; codes compared in constant time | `auth/totp.py` (`match_step`) |
| 2.8.4 | A TOTP code is usable once | Met | The last accepted step is stored and must increase | `auth/totp.py` (`match_step`), `auth/totp_service.py` |
| 2.8.5 | Reused OTP logged and notified | Partial | Rejected and logged (`totp_code_rejected`), counted as a failed sign-in; no notification | `api/v1/totp.py` (`totp_verify`) |
| 2.8.6 | OTP generator can be revoked | Met | Turning two-factor off (with a valid code) or the admin reset deletes the seed and all recovery codes | `auth/totp_service.py` (`disable`, `reset`) |
| 2.10.1 | Service credentials are not unchanging shared secrets | Partial | Database, storage and provider credentials are static secrets from the environment, rotated by hand | [rotate-secrets runbook](../runbooks/rotate-secrets.md) |
| 2.10.2 | No default service credentials | Met | Compose refuses to start without `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `SECRET_KEY`; the development `SECRET_KEY` is refused over https | `deploy/compose.yaml`, `config.py` |
| 2.10.3 | Service passwords protected from offline recovery | N/A | Service passwords are supplied by the operator's environment and never stored by Spanlight | |
| 2.10.4 | Secrets not in source code | Met | Settings come from the environment; CI generates throwaway secrets per run | `config.py`, `.github/workflows/ci.yml` |

## V3 Session management

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 3.1.1 | Session tokens never in URLs | Met | Cookie only | `api/v1/auth.py` (`set_auth_cookies`) |
| 3.2.1 | New token at authentication | Met | | `services/sessions.py` (`create_session`) |
| 3.2.2 | At least 64 bits of entropy | Met | 256 bits | `core/security.py` (`new_token`) |
| 3.2.3 | Stored in the browser securely | Met | `HttpOnly` cookie | `api/v1/auth.py` |
| 3.2.4 | Generated with an approved CSPRNG | Met | `secrets.token_urlsafe` | `core/security.py` |
| 3.3.1 | Logout and expiry invalidate the token | Met | The session row is deleted; expired rows are refused and cleaned up | `services/sessions.py`, `jobs/tasks/cleanup.py` |
| 3.3.2 | Periodic re-authentication (12 h, or 30 min idle) | Not met | 7 days idle, 30 days absolute (R9) | `services/sessions.py` |
| 3.3.3 | End other sessions after a password change | Met | A reset ends every session; "sign out everywhere else" is available | `services/credentials.py`, `api/v1/auth.py` (`delete_other_sessions`) |
| 3.3.4 | Users can view and end active sessions | Partial | Listing and ending sessions does not ask for the password again | `api/v1/auth.py` (`list_sessions`, `delete_session`) |
| 3.4.1 | `Secure` cookie attribute | Met | Set whenever `APP_BASE_URL` is https | `api/v1/auth.py`, `config.py` (`secure_cookies`) |
| 3.4.2 | `HttpOnly` cookie attribute | Met | The CSRF cookie is readable by design: the SPA echoes it | `api/v1/auth.py` |
| 3.4.3 | `SameSite` cookie attribute | Met | `Lax` | `api/v1/auth.py` |
| 3.4.4 | `__Host-` cookie prefix | Not met | | `api/deps.py` (`SESSION_COOKIE`) |
| 3.4.5 | Most precise cookie path | N/A | Spanlight expects an origin of its own; the OAuth state cookie is scoped to the OAuth routes | `auth/oauth_state.py` |
| 3.5.1 | Users can revoke linked OAuth relationships | Met | Providers can be unlinked unless it is the last way to sign in; provider tokens are never stored | `auth/oauth_service.py` (`unlink_identity`) |
| 3.5.2 | Session tokens rather than static API secrets | Partial | Browsers use sessions; scripts and SDKs use API keys and tokens by design, which can expire and be revoked | `api/deps.py` |
| 3.5.3 | Stateless tokens protected against tampering | Met | HMAC-SHA256 with a per-purpose context and an expiry | `auth/signing.py`, `auth/oauth_state.py`, `auth/login_challenge.py` |
| 3.7.1 | Re-authentication before sensitive changes | Partial | Turning off two-factor needs a code; deleting an organization needs the slug typed; creating keys and tokens and turning two-factor on do not ask again (R10) | `api/v1/totp.py`, `api/v1/orgs.py` (`delete_org`) |

## V4 Access control

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 4.1.1 | Enforced on a trusted service layer | Met | | `api/deps.py` (`require`) |
| 4.1.2 | Access attributes not changeable by users | Met | Roles and project bindings are read from the database per request, never from the client | `api/deps.py` (`_member_access`, `_key_access`) |
| 4.1.3 | Least privilege | Met | Four cumulative roles; key scopes; read-only tokens; a database role without RLS bypass | `core/permissions.py`, `core/scopes.py`, `db/roles.py` |
| 4.1.5 | Fails securely | Met | Unbound transactions see no telemetry; an unclassified permission raises; unexpected errors become a generic 500 | `db/rls.py`, `core/permissions.py`, `core/errors.py` |
| 4.2.1 | Protection against IDOR | Met | Membership resolved from the path, non-members get 404, row-level security on telemetry, cross-tenant tests | `api/deps.py`, `backend/tests/rls/` |
| 4.2.2 | Anti-CSRF mechanism | Met | Origin allowlist plus a session-bound double-submit token | `core/middleware.py`, `api/deps.py` (`authenticate_session`) |
| 4.3.1 | Multi-factor authentication for administrative interfaces | Partial | TOTP is available and an owner can require it for the organization; it is not mandatory by default | `api/v1/orgs.py`, `api/deps.py` |
| 4.3.2 | No directory browsing or metadata files | Met | Caddy serves only the built bundle, without browsing; unknown assets are 404 | `deploy/Caddyfile` |
| 4.3.3 | Step-up or segregation for high-value actions | Partial | Deleting an organization and changing its security settings are owner-only and need confirmation; no step-up authentication | `core/permissions.py`, `api/v1/orgs.py` |

## V5 Validation, sanitization and encoding

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 5.1.1 | HTTP parameter pollution defended | Met | Every query parameter is declared with a type; list parameters are declared as lists | `api/v1/traces.py` |
| 5.1.2 | Mass assignment prevented | Met | Request schemas list their fields; rows are built field by field | `api/schemas/` |
| 5.1.3 | Positive (allow-list) validation | Met | Pydantic types, lengths, ranges and enums | `api/schemas/`, `ingest/schemas.py` |
| 5.1.4 | Structured data strongly typed | Met | | `api/schemas/`, `ingest/schemas.py` |
| 5.1.5 | Redirects limited to allowed destinations | Met | | `auth/oauth_state.py` (`safe_next`) |
| 5.2.1 | WYSIWYG HTML sanitized | N/A | No rich-text input | |
| 5.2.2 | Unstructured data sanitized | Met | Ingested payloads are stored as JSON, length limited and rendered as text | `ingest/normalize.py` |
| 5.2.3 | Mail injection prevented | Met | `EmailMessage` refuses line breaks in headers | `email/providers/smtp.py` |
| 5.2.4 | No `eval` or dynamic code execution | Met | | |
| 5.2.5 | Template injection prevented | Met | Email templates interpolate escaped values into fixed text; no user-supplied templates | `email/templates.py` |
| 5.2.6 | SSRF prevented | Met | No outbound request takes a user-supplied URL | [threat model](threat-model.md) TB6 |
| 5.2.7 | SVG scripting prevented | N/A | No uploads | |
| 5.2.8 | Markdown, CSS and similar sanitized | N/A | User content is not rendered as markup | |
| 5.3.1 | Context-specific output encoding | Met | JSON responses; React escaping | |
| 5.3.2 | Character set preserved | Met | UTF-8 throughout | |
| 5.3.3 | Protection against reflected, stored and DOM XSS | Met | React escaping, no raw HTML sinks, strict CSP | `deploy/Caddyfile` |
| 5.3.4 | Parameterized queries | Met | SQLAlchemy with bound parameters | `core/ratelimit.py`, `jobs/tasks/retention.py` |
| 5.3.5 | Encoding where parameterization is unavailable | Met | Role DDL quotes identifiers and literals with `psycopg.sql` | `db/roles.py` |
| 5.3.6 | JSON injection prevented | Met | | `core/errors.py` |
| 5.3.7 | LDAP injection | N/A | No LDAP | |
| 5.3.8 | OS command injection prevented | Met | `pg_dump` and `pg_restore` run with an argument list and environment variables, no shell | `backups/service.py`, `backups/restore.py` |
| 5.3.9 | Local and remote file inclusion | N/A | No user-supplied paths | |
| 5.3.10 | XPath and XML injection | N/A | No XML | |
| 5.4.3 | Integer overflow prevented | Met | Token counts and timestamps are range-checked before storage | `ingest/schemas.py` |
| 5.5.1 | Serialized objects integrity-checked | Met | Signed state and challenge tokens | `auth/signing.py` |
| 5.5.2 | XML parsers restricted (XXE) | N/A | No XML parser is used; OTLP is JSON or protobuf | `ingest/otlp.py` |
| 5.5.3 | Untrusted deserialization avoided | Met | | `ingest/pipeline.py` |
| 5.5.4 | `JSON.parse` in the browser | Met | | `frontend/src/lib/api/` |

## V6 Stored cryptography

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 6.1.1 | Regulated private data encrypted at rest | Partial | Telemetry is not encrypted by the application; it relies on volume or managed-database encryption (R8) | |
| 6.1.2 | Regulated health data encrypted at rest | N/A | Spanlight does not classify payload content; the operator decides what is sent | |
| 6.1.3 | Regulated financial data encrypted at rest | N/A | As above; card numbers are redacted on ingest | `core/redact.py` |
| 6.2.1 | Crypto fails securely | Met | AES-GCM authentication failures raise one generic error | `core/crypto.py` (`decrypt`) |
| 6.2.2 | Proven algorithms and libraries | Met | `cryptography` AES-256-GCM, `argon2-cffi`, HMAC-SHA256 | `core/crypto.py`, `core/security.py` |
| 6.2.3 | Secure modes and IVs | Met | 96-bit random nonce per seal; key id bound as associated data | `core/crypto.py` (`encrypt`) |
| 6.2.4 | Algorithms and keys can be swapped | Partial | Encryption keys rotate through the keyring; argon2 parameters are not upgraded on sign-in | `core/crypto.py`, `core/security.py` |
| 6.2.5 | No insecure modes or weak hashes | Met | SHA-1 appears only inside TOTP's HMAC, as RFC 6238 requires | `auth/totp.py` |
| 6.2.6 | Nonces never reused | Met | Random 96-bit nonces | `core/crypto.py` |
| 6.3.1 | CSPRNG for every unguessable value | Met | `secrets` and `os.urandom` | `core/security.py`, `core/crypto.py`, `auth/totp.py` |
| 6.3.2 | Random GUIDs from v4 and a CSPRNG | N/A | Row ids are UUIDv7 for ordering and are never used as secrets | `core/ids.py` |
| 6.4.1 | Secrets management solution | Partial | Environment variables; a secret manager is the operator's choice | `config.py` |
| 6.4.2 | Key material isolated from the application | Not met | `CREDENTIALS_KEYS` is held in process memory | `core/crypto.py` |

## V7 Error handling and logging

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 7.1.1 | No credentials or session tokens in logs | Met | Logged by id only; query strings are not logged; database URLs never reach a log line; `httpx` request lines (which hold alert channel URLs) are not logged | `core/middleware.py`, `api/v1/tokens.py`, `backups/libpq.py`, `core/logging.py` |
| 7.1.2 | No other sensitive data in logs | Met | Request bodies are not logged; ingestion logs counts and the project id | `api/ingest.py`, `core/sentry.py` |
| 7.1.3 | Security events logged | Partial | Every request is logged with status; failed sign-ins are recorded in `login_attempts` (kept 1 day); there is no dedicated security event stream | `core/middleware.py`, `auth/login_throttle.py` |
| 7.1.4 | Enough context for an investigation | Met | UTC timestamp, request id, route, status and duration on every line | `core/logging.py`, `core/middleware.py` |
| 7.2.1 | Authentication decisions logged | Partial | Success and failure are recorded per attempt in the database, and as status codes in the access log | `api/v1/auth.py` (`login`) |
| 7.2.2 | Access control failures logged | Partial | Visible as 403 and 404 in the access log, without the reason | `core/middleware.py` |
| 7.3.1 | Log injection prevented | Met | JSON rendering escapes every value | `core/logging.py` |
| 7.3.3 | Logs protected | Partial | Spanlight writes to stdout; retention and access are the platform's | |
| 7.3.4 | Time sources synchronized | N/A | Host clock; rate limits and throttles use the database clock so replicas agree | `core/ratelimit.py`, `core/throttle.py` |
| 7.4.1 | Generic error message with an id | Met | problem+json `INTERNAL_ERROR` with `request_id` | `core/errors.py` |
| 7.4.2 | Exception handling throughout | Met | | `core/errors.py` |
| 7.4.3 | Last-resort error handler | Met | | `core/errors.py` (`_handle_unexpected`) |

## V8 Data protection

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 8.1.1 | Sensitive data not cached by server components | Partial | No server-side cache; API responses carry no `Cache-Control` except on credential and export routes | `api/v1/tokens.py`, `api/v1/totp.py`, `api/v1/oauth.py` |
| 8.1.2 | Temporary copies protected or purged | Met | Export files expire after 7 days; stored idempotent responses after 24 hours; email bodies are reduced after delivery | `exports/expiry.py`, `core/idempotency.py`, `notifications/jobs.py` |
| 8.1.3 | Few parameters in requests | Met | | |
| 8.1.4 | Abnormal request volumes detected | Partial | Rate-limit rejections are counted in Prometheus metrics; no alert rules are shipped | `core/observability.py`, `core/ratelimit.py` |
| 8.2.1 | Anti-caching headers | Met | `no-store` and `Pragma: no-cache` on every response that shows a secret once: new API keys, invite links, new tokens, two-factor setup and recovery codes; also on OAuth redirects and the audit CSV | `core/security.py` (`mark_uncacheable`), `api/v1/keys.py`, `api/v1/tokens.py`, `api/v1/totp.py` |
| 8.2.2 | No sensitive data in browser storage | Met | Local storage holds the theme and the last project id only | `frontend/src/lib/last-project.ts` |
| 8.2.3 | Client data cleared after sign-out | Met | The query cache is cleared and the cookies deleted | `frontend/src/features/shell/use-sign-out.ts`, `api/v1/auth.py` (`logout`) |
| 8.3.1 | No sensitive data in query strings | Partial | Invite preview takes its token in the query (R1); export links carry their signature in the query by design | `api/v1/orgs.py` (`preview_invite`) |
| 8.3.2 | Users can export or remove their data | Partial | Traces can be exported; projects and organizations can be deleted; a user account cannot be deleted by its user | `api/v1/exports.py`, `api/v1/projects.py`, `api/v1/orgs.py` |
| 8.3.3 | Clear privacy information and consent | N/A | Self-hosted: the operator is the controller of the data | |
| 8.3.4 | Sensitive data identified with a handling policy | Met | | [threat model](threat-model.md) §2 |
| 8.3.5 | Access to sensitive data audited | Not met | Reading traces is not audited | |
| 8.3.6 | Sensitive data cleared from memory | Not met | Not controllable in Python | |
| 8.3.7 | Encryption with confidentiality and integrity | Met | Sealed values (two-factor seeds, provider credentials, alert channel secrets) use AES-256-GCM | `core/crypto.py` |
| 8.3.8 | Retention and automatic deletion | Met | Per-project retention; cleanup of sessions, tokens, attempts and jobs; backup pruning | `jobs/tasks/retention.py`, `jobs/tasks/cleanup.py`, `backups/retention.py` |

## V9 Communication

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 9.1.1 | TLS for all client connections | Partial | Terminated in front of Caddy; HSTS is always sent; the app refuses an insecure configuration over https but cannot see the client's scheme (R14) | `deploy/Caddyfile`, `config.py` |
| 9.1.2 | Strong cipher suites only | N/A | TLS is terminated by the operator's proxy | |
| 9.1.3 | TLS 1.2 or 1.3 only | N/A | As above | |
| 9.2.1 | Trusted certificates for outbound connections | Met | Default certificate verification in `httpx`, `boto3` and `smtplib` | `auth/oauth_client.py`, `storage/s3.py`, `email/providers/smtp.py` |
| 9.2.2 | TLS for every inbound and outbound connection | Partial | Internal hops are plain on the private network unless configured (R14) | `deploy/compose.yaml` |
| 9.2.3 | External connections authenticated | Met | Provider APIs over https with credentials; endpoints that the operator sets may be http only if they choose | `config.py` |
| 9.2.4 | Certificate revocation checked | N/A | Left to the TLS libraries | |
| 9.2.5 | Backend TLS failures logged | Partial | Provider and storage failures are logged by type; not all connection errors are | `api/v1/oauth.py`, `exports/jobs.py` |

## V10 Malicious code

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 10.2.1 | No unauthorized phone-home | Met | Nothing is sent anywhere unless the operator configures Sentry, OpenTelemetry, email or the demo | `config.py` |
| 10.2.2 | No unnecessary device permissions | Met | `Permissions-Policy` denies camera, microphone and geolocation | `deploy/Caddyfile` |
| 10.3.1 | Auto-updates signed and secure | N/A | No auto-update; prices sync from a snapshot bundled in the image | `pricing/cost.py` |
| 10.3.2 | Integrity protection; no code from untrusted sources | Partial | Images are signed; the API reference page loads Swagger UI from jsDelivr without SRI (R11) | `.github/workflows/release.yml`, `deploy/Caddyfile` |
| 10.3.3 | Subdomain takeover protection | N/A | DNS is the operator's | |

## V11 Business logic

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 11.1.1 | Flows run in order | Met | The second factor needs a signed challenge from the first step; reset needs a token from the email | `auth/login_challenge.py`, `auth/password_reset.py` |
| 11.1.2 | Realistic human timing | Not met | No timing checks | |
| 11.1.3 | Per-user business limits | Partial | Invite mail, verification mail and export size are limited; the number of exports, keys and projects is not | `services/invites.py`, `exports/service.py` (`MAX_EXPORT_TRACES`) |
| 11.1.4 | Anti-automation against excessive calls | Partial | Ingestion, bearer reads and demo sign-in are rate limited; signup is limited to 10 per IP per hour; signed-in dashboard reads are not (R5) | `core/ratelimit.py` |
| 11.1.5 | Limits based on the threat model | Partial | | [threat model](threat-model.md) |
| 11.1.6 | No time-of-check to time-of-use races | Met | Locked reads and conditional writes on sensitive paths | `services/credentials.py`, `auth/email_tokens.py`, `core/throttle.py`, `api/v1/orgs.py` (`accept_invite`) |
| 11.1.7 | Unusual activity monitored | Partial | Metrics and logs exist; no detection rules | `core/observability.py` |
| 11.1.8 | Alerting on automated attacks | Not met | Rate-limit rejection metrics can drive alerts; no alert rules are shipped | `core/observability.py` |

## V12 Files and resources

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 12.1.1 | Large files refused | Met | Ingestion is capped at 5 MB and 1000 spans; every other `/api/v1` body at 1 MiB (413) | `api/ingest.py` (`read_bounded_body`), `core/body_limit.py` |
| 12.1.2 | Decompression bombs checked | Met | The decompressed size has the same cap | `api/ingest.py` (`_decompress`) |
| 12.1.3 | Storage quota per user | Partial | Retention bounds age and the ingest limit bounds rate; there is no volume quota | `jobs/tasks/retention.py`, `core/ratelimit.py` |
| 12.2.1 | File type validated by content | N/A | No uploads | |
| 12.3.1 | User file names not used on the filesystem | N/A | No uploads | |
| 12.3.2 | User file metadata cannot touch local files | N/A | No uploads | |
| 12.3.3 | No remote file inclusion through file metadata | N/A | No uploads | |
| 12.3.4 | Reflective file download prevented | Met | Download names and storage keys are generated by the server | `exports/service.py` (`storage_key`), `api/v1/orgs.py` (`export_audit_csv`) |
| 12.3.5 | File metadata not passed to OS commands | Met | Backup keys are generated from timestamps | `backups/retention.py` |
| 12.3.6 | No functionality from untrusted sources | Partial | The API reference page loads Swagger UI from a CDN (R11) | `main.py` |
| 12.4.1 | Untrusted files stored outside the web root | Met | Exports live in object storage | `storage/s3.py` |
| 12.4.2 | Untrusted files scanned | N/A | No uploads | |
| 12.5.1 | Web tier serves only expected files | Met | Caddy serves the built dashboard bundle and the built documentation site only | `deploy/Caddyfile`, `deploy/Dockerfile` |
| 12.5.2 | Uploaded files never executed as HTML | Met | Export files are served from the object store's origin with a CSV or NDJSON content type | `exports/service.py` (`CONTENT_TYPES`) |
| 12.6.1 | Outbound destinations allow-listed | Partial | Destinations are fixed in code or set by the operator, except gateway base URLs and alert webhook URLs, which users supply and which pass the vetted-egress check (public addresses only, checked again on every connection, no redirects); no network egress policy is shipped | [threat model](threat-model.md) TB6 |

## V13 API and web services

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 13.1.1 | Same encodings and parsers everywhere | Met | One JSON parser per path; OTLP protobuf through the official definitions | `ingest/pipeline.py`, `ingest/otlp.py` |
| 13.1.3 | No secrets in API URLs | Partial | Invite preview takes its token in the query (R1) | `api/v1/orgs.py` (`preview_invite`) |
| 13.1.4 | Authorization at route and resource level | Met | `require()` on the route; every query scoped to the bound project or organization; RLS underneath | `api/deps.py`, `db/rls.py` |
| 13.1.5 | Unexpected content types rejected | Partial | OTLP refuses other types with 415; native ingestion parses the body as JSON whatever the declared type | `api/ingest.py` |
| 13.2.1 | HTTP methods restricted per action | Met | | `api/v1/` |
| 13.2.2 | JSON schema validation | Met | | `api/schemas/`, `ingest/schemas.py` |
| 13.2.3 | CSRF protection for cookie-authenticated REST | Met | | `core/middleware.py`, `api/deps.py` |
| 13.2.5 | Content-Type checked | Partial | As 13.1.5 | `api/ingest.py` |
| 13.2.6 | Messages protected in transit | Partial | Depends on TLS in front of the deployment (R14) | |

## V14 Configuration

| ID | Requirement | Status | Note | Reference |
|---|---|---|---|---|
| 14.1.1 | Secure, repeatable builds and deployments | Met | Multi-stage Dockerfile, Compose and Railway definitions, CI | `deploy/Dockerfile`, `deploy/compose.yaml`, `deploy/railway/railway.ts` |
| 14.1.2 | Compiler hardening flags | N/A | No compiled code of Spanlight's own | |
| 14.1.3 | Server configuration hardened | Met | Caddy admin API off, `Server` header removed, non-root backend, `pip` removed from the runtime image | `deploy/Caddyfile`, `deploy/Dockerfile` |
| 14.1.4 | Redeploy or restore quickly from automation | Met | | `deploy/compose.yaml`, [restore runbook](../runbooks/restore.md), [rollback runbook](../runbooks/rollback.md) |
| 14.2.1 | Components up to date | Met | Dependabot weekly; Trivy on release | `.github/dependabot.yml`, `.github/workflows/release.yml` |
| 14.2.2 | Unneeded features removed | Partial | The interactive API reference and the OpenAPI document are served in production (R11) | `main.py` |
| 14.2.3 | SRI for CDN-hosted assets | Partial | The dashboard loads nothing from a CDN; the API reference page does, without SRI (R11) | `main.py` |
| 14.2.4 | Components from trusted repositories | Met | PyPI and npm through lockfiles; pinned scanner images | `backend/uv.lock`, `frontend/package-lock.json` |
| 14.2.5 | Software bill of materials | Met | CycloneDX SBOMs per release image, signed | `.github/workflows/release.yml` |
| 14.2.6 | Third-party libraries sandboxed | Partial | Container isolation only | `deploy/Dockerfile` |
| 14.3.2 | Debug modes off in production | Met | No `--reload`; FastAPI debug off | `deploy/Dockerfile` |
| 14.3.3 | No version details in responses | Met | `Server` removed by Caddy | `deploy/Caddyfile` |
| 14.4.1 | `Content-Type` with a safe charset | Met | JSON and problem+json responses; CSV with `charset=utf-8` | `core/errors.py`, `api/v1/orgs.py` |
| 14.4.2 | `Content-Disposition` on API responses | Not met | Set only on file downloads | `api/v1/orgs.py` (`export_audit_csv`) |
| 14.4.3 | Content-Security-Policy | Met | Strict policy everywhere except `/api/docs`; asserted by the smoke test | `deploy/Caddyfile`, `deploy/smoke.sh` |
| 14.4.4 | `X-Content-Type-Options: nosniff` | Met | | `deploy/Caddyfile` |
| 14.4.5 | `Strict-Transport-Security` | Met | One year, `includeSubDomains` | `deploy/Caddyfile` |
| 14.4.6 | `Referrer-Policy` | Met | `strict-origin-when-cross-origin` | `deploy/Caddyfile` |
| 14.4.7 | Framing restricted | Met | `frame-ancestors 'none'` and `X-Frame-Options: DENY` | `deploy/Caddyfile` |
| 14.5.1 | Only used HTTP methods accepted, others logged | Partial | Unknown methods get 405 and appear in the access log; no alerting | `core/middleware.py` |
| 14.5.2 | `Origin` not used for authentication | Met | Used only as a CSRF layer, never to authenticate | `core/middleware.py` (`OriginCheckMiddleware`) |
| 14.5.3 | CORS allow-list | Met | No CORS headers are sent: the API is same-origin only | `main.py` |
| 14.5.4 | Proxy-added headers authenticated | Partial | Client-IP headers are trusted from configured proxies at Caddy and from any peer at the api (R13) | `deploy/Caddyfile`, `deploy/Dockerfile` |
