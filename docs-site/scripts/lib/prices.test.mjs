import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { parsePrices, renderPricesPage } from './prices.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../../..');
const GITHUB = 'https://github.com/dheerajkarwasra/spanlight';

const FIXTURE = `
PRICING_VERSION = "2030-01-02"

SEED_PRICES = (
    # A comment.
    _price("anthropic", "model-a", "1.00", "5.00", "0.10"),
    _price("openai", "model-b", "0.15", "0.60", None),
)
`;

test('parses version and entries', () => {
  const { version, prices } = parsePrices(FIXTURE);
  assert.equal(version, '2030-01-02');
  assert.deepEqual(prices, [
    { provider: 'anthropic', modelPattern: 'model-a', input: '1.00', output: '5.00', cachedInput: '0.10' },
    { provider: 'openai', modelPattern: 'model-b', input: '0.15', output: '0.60', cachedInput: null },
  ]);
});

test('parses a call that spans several lines', () => {
  const source = 'PRICING_VERSION = "2030-01-02"\n_price(\n    "openai",\n    "model-c",\n    "1",\n    "2",\n    None,\n)\n';
  assert.equal(parsePrices(source).prices[0].modelPattern, 'model-c');
});

test('throws when nothing parses', () => {
  assert.throws(() => parsePrices('PRICING_VERSION = "2030-01-02"\n'), /no prices/i);
  assert.throws(() => parsePrices('_price("openai", "m", "1", "2", None)\n'), /PRICING_VERSION/);
});

test('parses the real price table', () => {
  const source = fs.readFileSync(path.join(REPO_ROOT, 'backend/app/pricing/prices.py'), 'utf8');
  const { version, prices } = parsePrices(source);
  assert.match(version, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(prices.length > 0);
  const calls = [...source.matchAll(/^\s+_price\(/gm)].length;
  assert.equal(prices.length, calls, 'every _price call in SEED_PRICES is parsed');
});

test('renders "—" for a missing cached rate', () => {
  const page = renderPricesPage(parsePrices(FIXTURE), { editUrl: `${GITHUB}/edit/main/backend/app/pricing/prices.py` });
  assert.match(page, /\| openai \| `model-b` \| 0\.15 \| — \| 0\.60 \|/);
  assert.match(page, /\| anthropic \| `model-a` \| 1\.00 \| 0\.10 \| 5\.00 \|/);
});

test('renders the frontmatter, the version, the matching rule and the links', () => {
  const page = renderPricesPage(parsePrices(FIXTURE), { editUrl: `${GITHUB}/edit/main/backend/app/pricing/prices.py` });
  assert.match(page, /^---\ntitle: "Prices"\n/);
  assert.match(page, /editUrl: "https:\/\/github\.com\/dheerajkarwasra\/spanlight\/edit\/main\/backend\/app\/pricing\/prices\.py"/);
  assert.match(page, /2030-01-02/);
  assert.match(page, /`-YYYYMMDD`/);
  assert.match(page, /`-YYYY-MM-DD`/);
  assert.match(page, /`-latest`/);
  assert.match(page, /\(\/docs\/concepts\/cost\/\)/);
  assert.match(page, /\(\/docs\/api\/prices\/\)/);
});

test('states that a span with a provider must match the row provider', () => {
  const page = renderPricesPage(parsePrices(FIXTURE), {});
  assert.match(page, /provider/i);
  assert.match(page, /`azure`/);
  assert.match(page, /`gpt-4o`/);
  assert.match(page, /`openai` row/);
});
