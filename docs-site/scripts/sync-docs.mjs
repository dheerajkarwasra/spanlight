// Builds the generated part of the docs site from the repository's own documents, so each fact
// has one source and the site never holds a hand-made copy:
//
//   docs/runbooks/*.md        -> src/content/docs/runbooks/
//   SECURITY.md, docs/security/*.md -> src/content/docs/security/
//   docs/deploy/railway.md    -> src/content/docs/deploy/railway.md
//   docs/api-deviations.md    -> src/content/docs/api/conventions.md
//   CHANGELOG.md              -> src/content/docs/changelog.md
//   backend/openapi.json      -> src/content/docs/api/*.md and public/openapi.json
//
// Every output is git-ignored. Relative links are rewritten so they resolve on the site (see
// lib/links.mjs). Run by `npm run build` and `npm run dev`; exits non-zero on a broken link.
// `--lenient` only warns instead, for drafting while a linked document is still being written;
// CI and the Docker build never pass it.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { rewriteLinks } from './lib/links.mjs';
import { frontmatter, renderOpenApi } from './lib/openapi.mjs';

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO = path.resolve(SITE, '..');
const DOCS = path.join(SITE, 'src/content/docs');
const BASE = '/docs';
const GITHUB = 'https://github.com/dheerajkarwasra/spanlight';

const GENERATED = ['runbooks', 'security', 'deploy', 'api', 'changelog.md'];

const RUNBOOK_ORDER = [
  'deploy',
  'rollback',
  'restore',
  'rotate-secrets',
  'revoke-key',
  'scale',
  'ingestion-spike',
  'slo',
  'gateway',
  'alerts',
  'detectors',
];

const errors = [];
const read = (repoPath) => fs.readFileSync(path.join(REPO, repoPath), 'utf8');
const exists = (repoPath) => fs.existsSync(path.join(REPO, repoPath));
const listMarkdown = (dir) =>
  exists(dir)
    ? fs
        .readdirSync(path.join(REPO, dir))
        .filter((name) => name.endsWith('.md'))
        .sort()
    : [];

/** One entry per page to publish: where it comes from and where it goes. */
function collectPages() {
  const pages = [];
  const add = (source, target, options = {}) => pages.push({ source, target, ...options });

  for (const name of listMarkdown('docs/runbooks')) {
    const stem = name.replace(/\.md$/, '');
    if (stem === 'README') {
      add('docs/runbooks/README.md', 'runbooks/index.md', { order: 0, label: 'Overview' });
    } else {
      const position = RUNBOOK_ORDER.indexOf(stem);
      add(`docs/runbooks/${name}`, `runbooks/${stem}.md`, {
        order: position === -1 ? 50 : position + 1,
      });
    }
  }

  add('SECURITY.md', 'security/policy.md', { order: 1 });
  for (const name of listMarkdown('docs/security')) {
    const stem = name.replace(/\.md$/, '');
    if (stem === 'README') {
      add('docs/security/README.md', 'security/index.md', { order: 0, label: 'Overview' });
    } else {
      add(`docs/security/${name}`, `security/${stem}.md`, { order: 10 });
    }
  }

  add('docs/deploy/railway.md', 'deploy/railway.md', { order: 30 });
  add('docs/api-deviations.md', 'api/conventions.md', {
    title: 'API conventions and notes',
    description:
      'Errors, authentication, CSRF, pagination, idempotency keys and the other rules the API follows.',
    order: 1,
  });
  add('CHANGELOG.md', 'changelog.md', {});
  return pages;
}

/** The site path of an output file: runbooks/index.md -> /docs/runbooks/. */
function sitePath(target) {
  const withoutExt = target.replace(/\.md$/, '').replace(/(^|\/)index$/, '');
  return `${BASE}/${withoutExt ? `${withoutExt}/` : ''}`;
}

function titleOf(markdown) {
  const match = markdown.match(/^# +(.+)$/m);
  return match ? match[1].trim() : null;
}

function stripTitle(markdown) {
  return markdown.replace(/^# +.+\n+/m, '');
}

function writeFile(relative, content) {
  const file = path.join(DOCS, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function main() {
  for (const name of GENERATED) fs.rmSync(path.join(DOCS, name), { recursive: true, force: true });
  fs.rmSync(path.join(SITE, 'public/openapi.json'), { force: true });

  const pages = collectPages();
  const published = new Map(pages.map((page) => [page.source, sitePath(page.target)]));
  published.set('docs/runbooks', `${BASE}/runbooks/`);
  if (exists('docs/security')) published.set('docs/security', `${BASE}/security/`);

  const context = {
    repoRoot: REPO,
    githubBlob: `${GITHUB}/blob/main`,
    githubTree: `${GITHUB}/tree/main`,
    published,
    // The Docker build sets DOCS_SYNC_SKIP_TARGET_CHECK=1: it holds only the published documents.
    checkTargets: process.env.DOCS_SYNC_SKIP_TARGET_CHECK !== '1',
  };

  for (const page of pages) {
    const raw = read(page.source);
    const title = page.title ?? titleOf(raw) ?? path.basename(page.source, '.md');
    const body = rewriteLinks(stripTitle(raw), page.source, context, errors).trimEnd();
    const sourceNote = `---\n\nThis page is generated from [\`${page.source}\`](${GITHUB}/blob/main/${page.source}) in the repository.`;
    writeFile(
      page.target,
      `${frontmatter({ title, description: page.description, order: page.order, label: page.label })}${body}\n\n${sourceNote}\n`,
    );
  }

  if (!pages.some((page) => page.target === 'security/index.md')) {
    const links = pages
      .filter((page) => page.target.startsWith('security/'))
      .map((page) => {
        const raw = read(page.source);
        return `- [${page.title ?? titleOf(raw)}](${sitePath(page.target)})`;
      });
    writeFile(
      'security/index.md',
      `${frontmatter({ title: 'Security', description: 'How to report a vulnerability and how Spanlight protects your data.', order: 0, label: 'Overview' })}Spanlight stores prompts, completions and API keys for the people who run it. These pages describe how to report a problem and how the product is protected.\n\n${links.join('\n')}\n`,
    );
  }

  const openapi = JSON.parse(read('backend/openapi.json'));
  for (const { file, content } of renderOpenApi(openapi)) writeFile(file, content);
  fs.mkdirSync(path.join(SITE, 'public'), { recursive: true });
  fs.writeFileSync(path.join(SITE, 'public/openapi.json'), `${JSON.stringify(openapi, null, 2)}\n`);

  if (errors.length > 0) {
    console.error(`sync-docs: ${errors.length} broken link(s) in the source documents:`);
    for (const error of errors) console.error(`  ${error}`);
    if (!process.argv.includes('--lenient')) process.exit(1);
  }
  console.log(`sync-docs: ${pages.length} pages and ${openapi.info.title} ${openapi.info.version} synced`);
}

main();
