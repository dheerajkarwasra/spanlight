import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import starlightLinksValidator from 'starlight-links-validator';
import starlightLlmsTxt from 'starlight-llms-txt';
import starlightSidebarTopics from 'starlight-sidebar-topics';

import { TOPICS } from './src/topics.mjs';

// The site is served by the web image (Caddy) at /docs/, next to the dashboard. Nothing is
// loaded from a third-party origin: fonts are self-hosted from @fontsource packages, search is built at build time
// and served from this origin, and the page scripts are hashed into the Content-Security-Policy
// (see scripts/csp.mjs).
// llms.txt needs absolute URLs, so it is only built when the public origin is known. Self-hosted
// builds (no DOCS_SITE_URL) skip it.
const llmsTxt = process.env.DOCS_SITE_URL ? [starlightLlmsTxt({ projectName: 'Spanlight' })] : [];

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
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
        alt: 'Spanlight',
        replacesTitle: true,
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/dheerajkarwasra/spanlight' },
      ],
      customCss: ['./src/styles/fonts.css', './src/styles/custom.css'],
      editLink: { baseUrl: 'https://github.com/dheerajkarwasra/spanlight/edit/main/docs-site/' },
      lastUpdated: true,
      components: { Footer: './src/components/Footer.astro' },
      pagination: true,
      // The runbooks include PromQL queries; the bundled highlighter has no grammar for it.
      expressiveCode: { shiki: { langAlias: { promql: 'txt' } } },
      plugins: [
        starlightSidebarTopics(TOPICS),
        ...llmsTxt,
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
