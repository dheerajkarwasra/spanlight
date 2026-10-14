import assert from 'node:assert/strict';
import { test } from 'node:test';

import { editUrlFor } from './edit-url.mjs';

const GITHUB = 'https://github.com/dheerajkarwasra/spanlight';

test('points a runbook at its file in docs/runbooks', () => {
  assert.equal(
    editUrlFor(GITHUB, 'docs/runbooks/deploy.md'),
    'https://github.com/dheerajkarwasra/spanlight/edit/main/docs/runbooks/deploy.md',
  );
});

test('points the changelog at CHANGELOG.md', () => {
  assert.equal(editUrlFor(GITHUB, 'CHANGELOG.md'), `${GITHUB}/edit/main/CHANGELOG.md`);
});

test('gives generated API pages no edit link', () => {
  assert.equal(editUrlFor(GITHUB, null), false);
});

test('strips leading slashes from the source path', () => {
  assert.equal(editUrlFor(GITHUB, '/SECURITY.md'), `${GITHUB}/edit/main/SECURITY.md`);
});
