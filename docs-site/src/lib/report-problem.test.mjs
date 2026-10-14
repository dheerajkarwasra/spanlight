import assert from 'node:assert/strict';
import { test } from 'node:test';

import { pageAddress, reportProblemUrl } from './report-problem.mjs';

const repoUrl = 'https://github.com/dheerajkarwasra/spanlight';

test('builds a prefilled GitHub issue link', () => {
  const url = new URL(
    reportProblemUrl({ repoUrl, title: 'Quickstart', pageUrl: 'https://spanlight.dev/docs/quickstart/' }),
  );
  assert.equal(url.origin + url.pathname, `${repoUrl}/issues/new`);
  assert.equal(url.searchParams.get('title'), 'Docs: Quickstart');
  assert.ok(url.searchParams.get('body').startsWith('Page: https://spanlight.dev/docs/quickstart/'));
});

test('encodes titles with special characters', () => {
  const url = new URL(reportProblemUrl({ repoUrl, title: 'Gateway & routing / errors', pageUrl: '/docs/x/' }));
  assert.equal(url.searchParams.get('title'), 'Docs: Gateway & routing / errors');
});

test('uses only the path when the public origin is unknown', () => {
  assert.equal(pageAddress('/docs/quickstart/', undefined), '/docs/quickstart/');
});

test('uses the full address when the public origin is known', () => {
  assert.equal(
    pageAddress('/docs/quickstart/', new URL('https://spanlight.dev')),
    'https://spanlight.dev/docs/quickstart/',
  );
});
