import assert from 'node:assert/strict';
import { test } from 'node:test';

import { catalogueIdsFrom, catalogueRedirect } from './anchor-redirect.mjs';

const RETRY = 'retry_storm-identical-failing-requests-repeated';

test('sends a moved detector anchor to the catalogue', () => {
  assert.equal(catalogueRedirect(`#${RETRY}`, [RETRY]), `/docs/reference/detectors/#${RETRY}`);
});

test('leaves anchors that still exist on the Doctor page alone', () => {
  assert.equal(catalogueRedirect('#the-catalogue', [RETRY]), null);
});

test('ignores an empty hash', () => {
  assert.equal(catalogueRedirect('', [RETRY]), null);
  assert.equal(catalogueRedirect('#', [RETRY]), null);
});

test('decodes an encoded hash before comparing', () => {
  assert.equal(catalogueRedirect('#%72etry_storm-identical-failing-requests-repeated', [RETRY]), `/docs/reference/detectors/#${RETRY}`);
  assert.equal(catalogueRedirect('#%E0%A4%A', [RETRY]), null);
});

test('reads the heading ids of the catalogue the way Starlight slugs them', () => {
  const markdown = [
    '---',
    'title: x',
    '---',
    '## Traffic',
    '### `error_spike`: error rate jumped',
    '```bash',
    '# Override for one slow call',
    '```',
    '### `error_spike`: error rate jumped',
    '#### Not a detector: a **bold** [link](/x)',
  ].join('\n');
  assert.deepEqual(catalogueIdsFrom(markdown), [
    'traffic',
    'error_spike-error-rate-jumped',
    'error_spike-error-rate-jumped-1',
    'not-a-detector-a-bold-link',
  ]);
});
