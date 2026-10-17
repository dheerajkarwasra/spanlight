# Contributing to Spanlight

Thanks for helping. This file covers how to set up, what a change has to pass, and how the maintainer cuts a release. By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems privately as described in [SECURITY.md](SECURITY.md), never in an issue.

## Before you start

- Search the existing issues. For anything bigger than a small fix, open an issue first (bug report or feature request) and agree on the approach before you write code.
- The [What's next](README.md#whats-next) section says what is planned. The [Architecture](README.md#architecture) section and the [decision records](docs/decisions/) say how the system fits together.

## Set up

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/) (the SDK supports 3.10+), Node 24 with npm, and Docker.

| Part       | Run it                                                                                                                                                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend    | `cd backend && cp .env.example .env && uv sync && uv run spanlight migrate && uv run uvicorn app.main:app --reload --port 8000` (needs Postgres 17 and an ordinary database role: see [`backend/README.md`](backend/README.md)) |
| Frontend   | `cd frontend && npm ci && npm run dev` (proxies `/api` to `:8000`)                                                                                                                                                              |
| Everything | `cp deploy/.env.example deploy/.env`, fill in the secrets, then `docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build`                                                                                    |

## Checks

Run the checks for what you touched before you open a pull request. CI runs all of them on every pull request and on pushes to `main`. The suite is being brought back to green after the 0.3 to 0.5 work; see the CI badge for the current state.

```bash
# Backend, which needs Postgres. See Test database below
cd backend && uv sync --frozen
uv run ruff check && uv run ruff format --check && uv run mypy app && uv run pytest

# Frontend
cd frontend && npm ci
npm run lint && npm run typecheck && npm test && npm run format:check
npm run build                         # CI builds too

# Python SDK
cd sdks/python && uv sync --frozen --all-extras
uv run ruff check && uv run ruff format --check && uv run mypy src && uv run pytest
```

**Test database.** Backend tests run against a real Postgres, not a mock. The default `TEST_DATABASE_URL` points at this container:

```bash
docker run -d --name spanlight-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=spanlight_test \
  -p 55432:5432 postgres:17
```

**API contract.** `backend/openapi.json` is the committed OpenAPI document, and `backend/tests/api/test_openapi_snapshot.py` fails when the app no longer matches it. After an intended API change, run `cd backend && uv run python scripts/update_openapi.py` and commit the updated file with the change.

**Compose smoke test.** `deploy/smoke.sh` starts the stack from `deploy/compose.yaml` on empty volumes, signs up, creates an API key, sends a span with the Python SDK, and tears everything down with `down -v`. It needs Docker with the compose plugin, `curl`, `jq` and `uv`, plus an env file (default `.local/compose-test.env`, or set `SMOKE_ENV_FILE`) with `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `SECRET_KEY`. The header of the script lists every option.

**End to end.** With the full stack running: `cd frontend && E2E_BASE_URL=http://localhost:5173 npx playwright test`.

**Repository files.** CI also checks the release workflow and that the community files exist. Both scripts need PyYAML, so CI runs them through uv:

```bash
uv run --no-project --with pyyaml python scripts/check-release-workflow.py
uv run --no-project --with pyyaml bash scripts/check-repo-files.sh
```

## Engineering standards

Reviewers check these. Modules already past a size limit are split when a change touches them; the layout below is the target for new code.

**Principles**

- One responsibility per file. Name a module for the one thing it does, and split it when its description needs a second noun.
- Size limits are smells, not laws. Split before you add to a backend module over 300 lines, a function over 40 lines, or a React component over 200 lines or with more than 5 props.
- Functional core, imperative shell. Pure logic (arithmetic, rules, formatters, permission checks) lives in modules with no I/O and gets unit tests. I/O (HTTP, the database, providers) lives in thin shells that are integration-tested.
- Explicit over clever. No compressed one-liners and no metaprogramming where a function would do. Comments say why; names say what.
- Typed everywhere. `mypy --strict` on the backend and the SDK, `tsc --strict` on the frontend. No `Any` outside serialization boundaries, and no `# type: ignore` without a reason on the same line.
- Fail loudly at the boundaries. Validate at the edge (pydantic, zod), raise domain exceptions inside, and map them to problem+json in one place (`backend/app/core/errors.py`). No bare `except`, and no catch-and-continue without a logged, named reason.
- Real data only. No mock data on the product path. Tests use fixtures shaped like real data.
- Unknown is never zero. Use null in the database, `null` in JSON and "—" with a tooltip in the UI. A model with no known price has no cost; it does not cost $0.
- YAGNI. Build what the tests need, and add an extension point when a second consumer exists.

**Backend** (`backend/app`)

- Routers in `api/` parse the request, authorize, call a service and serialize the result. They hold no business logic and no SQL.
- Dependencies point one way: `api` to service to queries to `db`. Nothing imports from `api/`, and `core/` (security, permissions, errors, rate limits, redaction, logging, pagination) imports nothing else from the app. A domain calls another domain's service, never its queries or models. Jobs call services.
- A new feature gets a package per domain: `schemas.py` for the models it owns, `queries.py` for reads, `service.py` for writes, transactions and audit events, `jobs.py` for worker tasks, and pure modules for the logic.
- Every project-scoped table has a row-level security policy and a test in `backend/tests/rls/`. Every foreign key has an index on the child side. Money is `numeric` and times are `timestamptz`. There are no soft deletes. Migrations are expand-then-contract and have a working `downgrade()`.

**Frontend** (`frontend/src`)

- Pages compose, parts render, hooks fetch, logic modules compute. A feature in `features/<feature>/` has a route page (layout only), presentational parts that do no fetching, a queries file with its TanStack Query hooks, and pure logic with a colocated test. No feature imports another feature's internals.
- API client functions and types live in `lib/api/`, one file per domain. Primitives in `components/ui/` are Radix-based, one component per file, with variants through `cva`. Colours come from the tokens in `styles/globals.css`, never raw values.
- Every list has loading, empty and error states. Every value that can be unknown goes through `UnknownValue`. Keyboard and screen-reader use must work (WCAG 2.1 AA) down to 375 px wide, and `prefers-reduced-motion` is respected.

**SDK** (`sdks/python`)

- The public API is exported only from `spanlight/__init__.py`, and private modules are `_<name>.py`. Each provider has one module under `integrations/`, and its package is imported inside the wrapper, never at module top. `httpx` is the only hard dependency. The SDK never raises into user code.

**Tests**

- Every pure module has unit tests whose names read as sentences (`test_unknown_models_cost_null_never_zero`).
- Every router and job should have integration tests on the real Postgres fixture; the gateway, alerts, budgets, Doctor and evals packages do not yet, and tests for them are welcome. Each resource has one authorization-matrix test: every role, a non-member, and an API key where the route accepts one.
- Never mock the unit under test. Fake providers and third parties at the transport, for example with `httpx.MockTransport`.
- Frontend: Vitest for logic and components, Playwright with axe for each new user flow.

**Naming.** Python uses `snake_case` modules and functions and `PascalCase` classes: nouns for modules, verbs for functions. TypeScript uses kebab-case files, `PascalCase` components, `use-` hooks and colocated `*.test.ts(x)`. API routes use plural nouns, keyset pagination and problem+json errors with stable `code`s. Job kinds are `snake_case` verbs. Each setting is one `SCREAMING_SNAKE` environment variable, and blank means unset.

## Review gate

Every change is reviewed against this checklist. "The task" is the issue your pull request addresses, and "the approved frame" is the design agreed in that issue.

- Spec requirement named in the task is met
- Tests exist and were seen failing first
- Lint, format, types green
- File and function sizes within the limits above
- No business logic in routers or pages
- New tables have RLS, indexes, a migration with downgrade
- Secrets never logged
- Errors mapped, not swallowed
- Unknowns are null/"—"
- Copy and layout match the approved frame
- Docs page or runbook updated when the task adds user- or operator-facing surface

The [pull request template](.github/pull_request_template.md) carries the same list as checkboxes.

## Licensing of contributions

Contributions outside `ee` directories are accepted under the [MIT license](LICENSE). Code under a directory named `ee` is under the [Spanlight Enterprise License](backend/app/ee/LICENSE), so a pull request that touches an `ee` directory needs a signed contributor license agreement before it can be merged; the maintainer will send it on the pull request. Core code must not import `app.ee` (ruff enforces it): enterprise features plug in through `app/ee/hooks.py`. See [ADR 16](docs/decisions/0016-open-core-ee-directories.md).

## Commits and pull requests

Commit messages and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`, in the imperative, with the types `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci` and `chore`.

```
feat(ingest): accept gzip-compressed OTLP requests
fix(sdk): flush queued spans before the process exits
docs(deploy): explain the Railway pre-deploy step
refactor(web): split the trace waterfall into parts
feat(api)!: rename the trace list cursor parameter
```

A `!` after the type or scope marks a breaking change; explain it in a `BREAKING CHANGE:` footer. Keep each pull request to one logical change.

## Releasing

For the maintainer. A release is a pushed tag. `.github/workflows/release.yml` runs on two tag patterns, and each starts only its own job (`scripts/check-release-workflow.py` enforces this in CI).

| Tag                                                     | Publishes                                                                                                                                 |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `v<semver>`, for example `v0.2.0` or `v0.2.0-rc.1`      | Server images `ghcr.io/dheerajkarwasra/spanlight:<version>` and `ghcr.io/dheerajkarwasra/spanlight-web:<version>`, and a GitHub Release |
| `sdk-python-v<semver>`, for example `sdk-python-v0.1.0` | The `spanlight` package on PyPI                                                                                                           |

**Server images.** Image tags have no `v`: tag `v0.2.0` gives `:0.2.0`. The job builds both images, scans them, pushes the version tag, signs the image digests with cosign (keyless), moves `latest`, then attaches signed CycloneDX SBOMs to a GitHub Release with generated notes. `latest` moves only for a non-pre-release (a version with no `-`), and only after signing. A tag that is not `v<semver>` fails at the first step.

**Python SDK.** Raise `version` in `sdks/python/pyproject.toml` and `__version__` in `sdks/python/src/spanlight/_version.py`, merge, then tag `sdk-python-v<that version>`. The job fails if the tag and either file disagree, and it runs the SDK tests before it builds or uploads anything. PyPI uploads are permanent and a version can never be reused, so check the number before you push the tag.

**Action pins.** Every action in `release.yml` is pinned to a full commit SHA with a trailing `# vX.Y.Z` comment, because these jobs hold `id-token`, `packages` and `contents: write` and a tag such as `@v3` can be moved after review. `scripts/check-release-workflow.py` fails CI on a tag pin or a missing comment. Dependabot's `github-actions` updates raise the SHA and the comment together. `ci.yml` and `codeql.yml` hold no publish rights and stay on major-version tags. To pin by hand, find the commit with `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>*'` and take the `^{}` line when the tag is annotated.

**One-time owner setup.**

1. Create a GitHub environment named `pypi` (Settings, Environments).
2. On PyPI, add a pending trusted publisher: project `spanlight`, owner `dheerajkarwasra`, repository `spanlight`, workflow `release.yml`, environment `pypi`. No PyPI token exists anywhere.
3. After the first server release, GHCR creates `spanlight` and `spanlight-web` as private packages. Make both public (package page, Package settings, Change visibility), or nobody else can pull them.
4. Enable private vulnerability reporting (Settings → Security → Private vulnerability reporting). Without it, the report links in `SECURITY.md` and the issue chooser return 404.

**Vulnerability gate.** Trivy scans both images before anything is pushed and fails the release on a fixable CRITICAL or HIGH finding. It runs with `--ignore-unfixed`, so a vulnerability with no available fix does not block a release. That is a deliberate posture, and the maintainer owns it: unfixed findings are not gated, so watch Dependabot and advisories for them. The base images float (`python:3.13-slim`, `caddy:2.11-alpine`), so a clean scan last week says little about tag day. Rebuild and scan right before you tag. Run this from the repository root; it reads the pinned Trivy image from `release.yml`, so it cannot drift from the release. A failing image prints `SCAN FAILED: <image>` and the block ends with a non-zero status. It avoids `set -e` on purpose, because in an interactive shell that would close the terminal on the first failure:

```bash
TRIVY=$(grep -o 'aquasec/trivy:[^"]*' .github/workflows/release.yml)
docker build --pull -f deploy/Dockerfile --target backend -t spanlight:scan .
docker build --pull -f deploy/Dockerfile --target web -t spanlight-web:scan .
failed=0
for image in spanlight:scan spanlight-web:scan; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$TRIVY" image \
    --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed "$image" \
    || { echo "SCAN FAILED: $image"; failed=1; }
done
if [ "$failed" = 1 ]; then false; else echo "SCAN PASSED: both images"; fi
```

**Verify a signature.** Anyone can check that an image was built by this workflow:

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/dheerajkarwasra/spanlight/\.github/workflows/release\.yml@refs/tags/v' \
  ghcr.io/dheerajkarwasra/spanlight:0.2.0
```

Each SBOM on the GitHub Release has a Sigstore bundle: `cosign verify-blob --bundle sbom-spanlight.cdx.json.sigstore.json` with the same two certificate flags, then `sbom-spanlight.cdx.json`.
