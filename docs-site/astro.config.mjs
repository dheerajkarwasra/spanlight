import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightLinksValidator from 'starlight-links-validator';

// The site is served by the web image (Caddy) at /docs/, next to the dashboard. Nothing is
// loaded from a third-party origin: fonts are the system fonts, search is built at build time
// and served from this origin, and the page scripts are hashed into the Content-Security-Policy
// (see scripts/csp.mjs).
export default defineConfig({
  base: '/docs',
  // Set DOCS_SITE_URL to the public origin to get absolute canonical URLs and a sitemap.
  site: process.env.DOCS_SITE_URL || undefined,
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    starlight({
      title: 'Spanlight',
      description: 'Documentation for Spanlight, open-source observability for LLM applications.',
      favicon: '/favicon.svg',
      logo: { src: './src/assets/logo.svg', alt: 'Spanlight' },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/dheerajkarwasra/spanlight' },
      ],
      customCss: ['./src/styles/custom.css'],
      // The runbooks include PromQL queries; the bundled highlighter has no grammar for it.
      expressiveCode: { shiki: { langAlias: { promql: 'txt' } } },
      sidebar: [
        { label: 'Start here', items: ['quickstart', 'python-sdk', 'otlp'] },
        {
          label: 'Guides',
          items: ['guides/accounts', 'guides/access', 'guides/data'],
        },
        { label: 'Alerts', items: ['alerts', 'budgets', 'webhooks'] },
        { label: 'Doctor', items: ['doctor', 'releases-and-users'] },
        {
          label: 'Evals',
          items: [
            'prompts',
            'playground',
            'datasets',
            'scores',
            'evaluators',
            'experiments',
            'eval-gate',
          ],
        },
        {
          label: 'Gateway',
          items: [
            'gateway/quickstart',
            'gateway/routing',
            'gateway/cache',
            'gateway/lab',
            'gateway/credentials',
            'gateway/errors',
          ],
        },
        { label: 'Self-hosting', items: ['self-hosting', 'configuration', 'deploy/railway'] },
        { label: 'API', items: [{ autogenerate: { directory: 'api' } }] },
        { label: 'Security', items: [{ autogenerate: { directory: 'security' } }] },
        { label: 'Runbooks', items: [{ autogenerate: { directory: 'runbooks' } }] },
        'changelog',
      ],
      plugins: [
        starlightLinksValidator({
          // The site is built with a base path, so links in the generated pages are absolute.
          errorOnRelativeLinks: false,
          errorOnInvalidHashes: true,
          errorOnLocalLinks: true,
          errorOnInconsistentLocale: false,
        }),
      ],
    }),
  ],
});
