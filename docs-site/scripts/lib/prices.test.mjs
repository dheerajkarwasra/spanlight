import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { parsePrices, renderPricesPage, stripComments, tierLabel } from './prices.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '../../..');
const GITHUB = 'https://github.com/dheerajkarwasra/spanlight';

const FIXTURE = `
PRICING_VERSION = "2030-01-02"

SEED_PRICES: tuple[SeedPrice, ...] = (
    # A comment.
    _price("anthropic", "model-a", "1.00", "5.00", "0.10", write_5m="1.25", write_1h="2.00"),
    # Prompts over 100K tokens bill the whole prompt at the next tier.
    _price(
        "anthropic",
        "model-a",
        "2.00",
        "10.00",
        "0.20",
        write_5m="2.50",
        write_1h="4.00",
        min_prompt_tokens=100_001,
    ),
    _price("openai", "model-b", "0.15", "0.60", None),
)

CURRENT_SNAPSHOT = PriceSnapshot(PRICING_VERSION, EFFECTIVE_FROM, SEED_PRICES)

SNAPSHOT_2030_01_01 = PriceSnapshot(
    "2030-01-01",
    datetime(2030, 1, 1, tzinfo=UTC),
    (
        _price("anthropic", "model-a", "0.50", "2.50", "0.05"),
        _price("openai", "model-old", "0.10", "0.40", None),
    ),
)
`;

const base = {
  cacheWrite5m: null,
  cacheWrite1h: null,
  minPromptTokens: 0,
};

test('parses version and entries, with the write rates and the tier', () => {
  const { version, prices } = parsePrices(FIXTURE);
  assert.equal(version, '2030-01-02');
  assert.deepEqual(prices, [
    {
      provider: 'anthropic',
      modelPattern: 'model-a',
      input: '1.00',
      output: '5.00',
      cachedInput: '0.10',
      cacheWrite5m: '1.25',
      cacheWrite1h: '2.00',
      minPromptTokens: 0,
    },
    {
      provider: 'anthropic',
      modelPattern: 'model-a',
      input: '2.00',
      output: '10.00',
      cachedInput: '0.20',
      cacheWrite5m: '2.50',
      cacheWrite1h: '4.00',
      minPromptTokens: 100001,
    },
    { ...base, provider: 'openai', modelPattern: 'model-b', input: '0.15', output: '0.60', cachedInput: null },
  ]);
});

test('renders only the current snapshot, never a historical row', () => {
  const { prices } = parsePrices(FIXTURE);
  assert.ok(!prices.some((price) => price.modelPattern === 'model-old'));
  assert.equal(prices.filter((price) => price.modelPattern === 'model-a').length, 2);
});

test('parses a call that spans several lines', () => {
  const source =
    'PRICING_VERSION = "2030-01-02"\nSEED_PRICES = (\n    _price(\n        "openai",\n        "model-c",\n        "1",\n        "2",\n        None,\n    ),\n)\n';
  assert.equal(parsePrices(source).prices[0].modelPattern, 'model-c');
});

test('throws when nothing parses', () => {
  assert.throws(() => parsePrices('PRICING_VERSION = "2030-01-02"\n'), /SEED_PRICES/);
  assert.throws(() => parsePrices('PRICING_VERSION = "2030-01-02"\nSEED_PRICES = (\n)\n'), /no prices/i);
  assert.throws(() => parsePrices('SEED_PRICES = (\n    _price("openai", "m", "1", "2", None),\n)\n'), /PRICING_VERSION/);
});

test('fails loudly on a row it cannot read instead of dropping it', () => {
  const wrap = (call) => `PRICING_VERSION = "2030-01-02"\nSEED_PRICES = (\n    ${call},\n)\n`;
  assert.throws(() => parsePrices(wrap('_price("openai", "m", "1", "2", None, batch="0.5")')), /unknown keyword batch/);
  assert.throws(() => parsePrices(wrap('_price("openai", "m", "1", "2")')), /expected 5 positional/);
  assert.throws(() => parsePrices(wrap('_price("openai", "m", "1", "2", None, "3")')), /too many arguments/);
  assert.throws(() => parsePrices(wrap('_price("openai", "m", "1", "2", None, write_5m=1.25)')), /cannot read/);
  assert.throws(() => parsePrices(wrap('_price("openai", "m", "1", "2", None, min_prompt_tokens="1")')), /whole number/);
});

test('a comment inside a call cannot cut it short or swallow its keywords', () => {
  const wrap = (call) => `PRICING_VERSION = "2030-01-02"\nSEED_PRICES = (\n${call}\n)\n`;
  // A parenthesis in a trailing comment before the keywords.
  const [withParen] = parsePrices(
    wrap('    _price(\n        "anthropic", "m", "1.00", "5.00", "0.10",  # 1.25x (5 m) and 2x (1 h)\n        write_5m="1.25", write_1h="2.00",\n    ),'),
  ).prices;
  assert.equal(withParen.cacheWrite5m, '1.25');
  assert.equal(withParen.cacheWrite1h, '2.00');
  // A plain trailing comment after an argument.
  const [plain] = parsePrices(wrap('    _price(\n        "anthropic",  # provider\n        "m", "1", "2", None,\n    ),')).prices;
  assert.equal(plain.provider, 'anthropic');
  assert.equal(plain.modelPattern, 'm');
  // A comment that looks like a keyword or an argument.
  const [tricky] = parsePrices(wrap('    _price("openai", "m", "1", "2", None),  # write_5m="9", min_prompt_tokens=5')).prices;
  assert.equal(tricky.cacheWrite5m, null);
  assert.equal(tricky.minPromptTokens, 0);
});

test('stripComments keeps a # inside a quoted string', () => {
  assert.equal(stripComments('_price("openai", "m#1", "1", "2", None),  # note'), '_price("openai", "m#1", "1", "2", None),  ');
  assert.equal(stripComments('# whole line\nx = 1  # tail\ny = "a # b"'), '\nx = 1  \ny = "a # b"');
  const [price] = parsePrices('PRICING_VERSION = "2030-01-02"\nSEED_PRICES = (\n    _price("openai", "m#1", "1", "2", None),\n)\n').prices;
  assert.equal(price.modelPattern, 'm#1');
});

test('parses the real price table', () => {
  const source = fs.readFileSync(path.join(REPO_ROOT, 'backend/app/pricing/prices.py'), 'utf8');
  const { version, prices } = parsePrices(source);
  assert.match(version, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(prices.length > 0);
  const seedBlock = /^SEED_PRICES\b[^=\n]*=\s*\(\n([\s\S]*?)^\)/m.exec(source)[1];
  const calls = [...seedBlock.matchAll(/^\s+_price\(/gm)].length;
  assert.equal(prices.length, calls, 'every _price call in SEED_PRICES is parsed');
  const allCalls = [...source.matchAll(/^\s+_price\(/gm)].length;
  assert.ok(allCalls > calls, 'the historical snapshot rows are _price calls outside SEED_PRICES');

  const anthropic = prices.filter((price) => price.provider === 'anthropic');
  assert.ok(anthropic.length >= 12, `expected the Anthropic rows, found ${anthropic.length}`);
  for (const price of anthropic) {
    assert.ok(price.cacheWrite5m && price.cacheWrite1h, `${price.modelPattern} has no cache-write rates`);
  }
  const tiers = prices.filter((price) => price.minPromptTokens > 0);
  assert.ok(tiers.length >= 1, 'the table has at least one prompt-size tier');
  assert.ok(
    tiers.some((price) => price.modelPattern === 'claude-haiku-5-5' && price.minPromptTokens === 100001),
    'claude-haiku-5-5 has its over-100,000 tier',
  );
  for (const price of prices.filter((price) => price.provider === 'openai')) {
    assert.equal(price.cacheWrite5m, null, `${price.modelPattern}: OpenAI does not bill cache writes`);
    assert.equal(price.minPromptTokens, 0);
  }
});

test('labels the prompt tiers', () => {
  assert.equal(tierLabel(0), 'Any size');
  assert.equal(tierLabel(100001), 'Over 100,000 tokens');
});

test('renders "—" for a missing cached or write rate and the tier rows', () => {
  const page = renderPricesPage(parsePrices(FIXTURE), { editUrl: `${GITHUB}/edit/main/backend/app/pricing/prices.py` });
  assert.match(page, /\| openai \| `model-b` \| Any size \| 0\.15 \| — \| — \| — \| 0\.60 \|/);
  assert.match(page, /\| anthropic \| `model-a` \| Any size \| 1\.00 \| 0\.10 \| 1\.25 \| 2\.00 \| 5\.00 \|/);
  assert.match(page, /\| anthropic \| `model-a` \| Over 100,000 tokens \| 2\.00 \| 0\.20 \| 2\.50 \| 4\.00 \| 10\.00 \|/);
  assert.match(page, /2 models, 3 rows\./);
  assert.match(page, /One row prices a second tier/);
  assert.doesNotMatch(page, /model-old/);
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
