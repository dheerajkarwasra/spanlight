# Spanlight — frontend

The web app for Spanlight: traces, sessions, cost/latency/error metrics, onboarding and
project settings. Built against the API described in
[`docs/api-deviations.md`](../docs/api-deviations.md) and the backend's OpenAPI document (`/api/docs` on a running server).

**Stack:** Vite · React 19 · TypeScript (strict) · TanStack Router (code-based routes) + Query ·
Tailwind CSS v4 · Radix primitives (shadcn-style components in `src/components/ui`) · Recharts ·
react-hook-form + zod · cmdk · sonner · Vitest + Testing Library · Playwright + axe.

## Develop

```bash
npm install
npm run dev          # http://localhost:5173, proxies /api, /v1, /health → http://localhost:8000
```

Point the proxy elsewhere with `VITE_BACKEND_URL=http://host:port npm run dev`.

| Command             | What it does                                                              |
| ------------------- | ------------------------------------------------------------------------- |
| `npm run lint`      | ESLint (typescript-eslint strict type-checked, react-hooks)               |
| `npm run typecheck` | `tsc -b --noEmit`                                                         |
| `npm test`          | Vitest unit and component tests                                           |
| `npm run build`     | Type-check and build to `dist/` (served by Caddy in production)           |
| `npm run format`    | Prettier (with Tailwind class sorting)                                    |
| `npm run e2e`       | Playwright against a running stack — skipped unless `E2E_BASE_URL` is set |

```bash
E2E_BASE_URL=http://localhost:5173 npx playwright test
```

## Layout

```
src/
  app/            router (all routes + search-param schemas), providers, query client
  components/ui/  design-system primitives (Radix + Tailwind tokens)
  components/     shared app components: UnknownValue, EmptyState, ErrorState, JsonViewer, CopyButton…
  features/       one folder per area: landing, auth, onboarding, shell, overview, traces, sessions, doctor,
                  alerts, budgets, gateway, prompts, datasets, evaluators, experiments, review, settings, …
  lib/api/        typed API client (CSRF, problem+json → ApiError, 401 → /login), endpoints, query keys
  lib/            formatting, time ranges, permissions, theme
  styles/         globals.css — the single source of design tokens (light + dark)
e2e/              Playwright specs
```

## Conventions

- **Design tokens** live only in `src/styles/globals.css` as CSS variables, mapped to Tailwind via
  `@theme inline`. Components use semantic classes (`bg-surface`, `text-muted-foreground`,
  `bg-kind-llm`), never raw colours.
- **Unknown is not zero.** Null metrics render `—` with a tooltip explaining why
  (`<ValueOrUnknown reason="No price for this model" />`).
- **URL is state.** Time range, environment and trace filters live in search params, so every view
  is shareable.
- Every data view has a loading skeleton, an empty state and an error state with retry.
- No mock data in the product. Tests stub `fetch`.
