# 16. Open core: enterprise code in `ee/` directories, unlocked by offline license keys

Date: 2026-10-15 · Status: accepted

## Context

Spanlight is MIT, and self-hosting it for free, commercially included, has to stay a complete product. The enterprise features planned next (SCIM provisioning, enforced single sign-on, audit streaming, IP allowlists) are needed mainly by large organizations, and building and supporting them has to be paid for somehow. Three questions had to be answered. **Where does paid code live** without making the free product feel crippled or the license unclear? **How does an instance know it may run that code**, given that many buyers run air-gapped and refuse software that calls home? **How does the MIT core stay independent** of code it cannot ship to everyone under MIT?

## Decision

Code under any directory named `ee` is under the Spanlight Enterprise License (`backend/app/ee/LICENSE`); everything else stays MIT. The root `LICENSE` says so.

- **Scaffold first, nothing moved.** No feature that is MIT today moves into `ee/`. Features are built there from the start, beginning with the Phase 7 enterprise set.
- **Offline signed keys.** A license key is `spl_lic_<payload>.<signature>`: a JSON payload (`v`, `kid`, `license_id`, `licensee`, `issued_at`, `expires_at`, `features`) and an Ed25519 signature over the payload segment exactly as transmitted, both unpadded base64url. The public keys ship in the code, keyed by `kid`; there is no setting that adds one. The private key stays with the maintainer, and `backend/scripts/ee_license.py` mints keys. A key lists its features by name, with no wildcard, so a key never gains a feature built after it was issued.
- **One setting, verified at startup.** `EE_LICENSE_KEY` unset is the community edition. A key that does not verify (malformed, unknown version or `kid`, bad signature) stops the process, as a malformed `CREDENTIALS_KEYS` does, and the message never quotes the key. Expiry is not checked at startup.
- **Expiry judged at each gate.** An expired license loads. Each gate compares `expires_at` with the caller's clock, so features turn off when the key expires, without a restart. Each process logs its edition at startup and warns from 30 days before expiry.
- **403, not 404.** A route of an unlicensed feature answers `403 EE_LICENSE_REQUIRED`, naming the feature. ee routers are always mounted. 404 stays reserved for hiding another tenant's resources. A worker task of an unlicensed feature ends `skipped`.
- **Dependencies point one way.** `app.ee` may import core; core may not import `app.ee` (ruff `TID251`). Only the composition roots may: `app.main` mounts `hooks.routers()` and keeps the license on `app.state.ee_license`, the worker merges `hooks.tasks()`, `app.config` verifies the key and `app.cli` reports it (`spanlight license-status`). A hook for periodic jobs is added with the first ee periodic job.
- **Shared plumbing stays in core.** Migrations, the permission table, row-level security policies and ingest hooks stay MIT, even when only an ee feature uses them. `ee/` holds the gated logic, so the schema of a community instance and an enterprise one never differ.

## Consequences

- **The community edition is the whole product minus enterprise administration.** Removing every `ee` directory leaves a working application, and the import rule keeps it that way.
- **No call home, no revocation.** Each replica verifies its own environment, and nothing is stored in the database. A leaked key works until it expires, so keys are issued for a year or less. Revocation would need a key server, which was rejected for air-gapped buyers.
- **The license can be broken by editing the source.** The check is in readable code and anyone can delete it. The license forbids that, and the buyers who need these features are the ones who honour licenses. The check exists so honest operators know where they stand, not to stop determined ones.
- **Rotation is a release.** A new signing key means a release that adds its public key; licenses signed by the old one keep working while it stays listed.
- **Contributions to `ee/` need a CLA,** because code under the Enterprise License cannot be relicensed without its authors' agreement. Contributions elsewhere stay plain MIT.
- **GitHub no longer labels the repository simply "MIT"**, since the root `LICENSE` has a preamble. The README states the split.
