# Spanlight documentation site

The public documentation, built with [Astro Starlight](https://starlight.astro.build) and served at `/docs/` by the `web` image (Caddy). It loads nothing from another origin.

```bash
cd docs-site
npm ci
npm run dev       # http://localhost:4321/docs/
npm run build     # sync, build, check links and anchors, write the CSP snippet
```

## Where the pages come from

| Pages | Source |
| --- | --- |
| Quickstart, Python SDK, OTLP, self-hosting, configuration (prose) | written by hand in `src/content/docs/` |
| Configuration tables | generated from `app.config.Settings` by `backend/scripts/gen_config_table.py` into `configuration.md`, between its markers |
| API reference and schemas | generated from `backend/openapi.json` |
| API conventions | `docs/api-deviations.md` |
| Runbooks | `docs/runbooks/*.md` |
| Security | `SECURITY.md` and `docs/security/*.md` |
| Railway guide | `docs/deploy/railway.md` |
| Changelog | `CHANGELOG.md` |
| Price reference | generated from `backend/app/pricing/prices.py` into `reference/prices.md` |

`scripts/sync-docs.mjs` copies the generated pages into `src/content/docs/` before every build, adds the front matter, and rewrites relative links: a link to another published page becomes a site path, and a link to any other file in the repository becomes a GitHub URL. A link to a file that does not exist fails the sync. The copies are git-ignored; edit the source documents, never the copies.

After you change a setting in `backend/app/config.py`, add its description to `backend/scripts/gen_config_table.py` and run:

```bash
cd backend && uv run python scripts/gen_config_table.py
```

After an API change, run `uv run python scripts/update_openapi.py` in `backend/` and the reference follows.

## Content-Security-Policy

The web server's policy is `default-src 'self'`. The Starlight pages carry a few inline scripts, so `npm run build` ends with `scripts/csp.mjs`, which hashes every inline script in the built HTML and writes `.build/docs-csp.caddy`. The Docker build installs that file as `/etc/caddy/docs-csp.caddy`, and `deploy/Caddyfile` imports it for `/docs/*`. The same step fails the build if any page references a script, stylesheet, image or font on another origin.
