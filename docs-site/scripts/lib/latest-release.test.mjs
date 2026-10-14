import assert from 'node:assert/strict';
import { test } from 'node:test';

import { latestRelease, releaseLabel } from './latest-release.mjs';

test('reads the newest version from the changelog', () => {
  const changelog = '# Changelog\n\nIntro.\n\n## [0.6.0] - Unreleased\n\nStuff.\n\n## [0.5.0] - Unreleased\n';
  assert.deepEqual(latestRelease(changelog), {
    version: '0.6.0',
    status: 'Unreleased',
    anchor: '060---unreleased',
  });
});

test('returns null when the changelog has no release heading', () => {
  assert.equal(latestRelease('# Changelog\n\nNothing yet.\n'), null);
});

test('names an unreleased entry as the next version', () => {
  assert.deepEqual(releaseLabel({ version: '0.6.0', status: 'Unreleased', anchor: 'x' }), {
    badge: 'Next',
    text: 'Next version 0.6.0 (unreleased)',
  });
});

test('names a released entry as the latest release', () => {
  assert.deepEqual(releaseLabel({ version: '0.5.0', status: '2026-09-01', anchor: 'x' }), {
    badge: 'Latest',
    text: 'Latest release 0.5.0',
  });
});
