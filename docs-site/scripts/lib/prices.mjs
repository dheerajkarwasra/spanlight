// Builds the "Prices" reference page from the backend's price table, so the page never holds a
// hand-made copy of a rate: `backend/app/pricing/prices.py` is the only place a number is written.

import { frontmatter } from './openapi.mjs';

const STRING = '"([^"]*)"';
const PRICE_CALL = new RegExp(
  `\\b_price\\(\\s*${STRING}\\s*,\\s*${STRING}\\s*,\\s*${STRING}\\s*,\\s*${STRING}\\s*,\\s*(?:${STRING}|None)\\s*,?\\s*\\)`,
  'g',
);

/**
 * The version and every entry of the price table: `PRICING_VERSION` and each
 * `_price("provider", "model", "input", "output", "cached" | None)` call.
 * @param {string} pricesPySource
 * @returns {{ version: string, prices: { provider: string, modelPattern: string, input: string, output: string, cachedInput: string | null }[] }}
 */
export function parsePrices(pricesPySource) {
  const version = /^PRICING_VERSION\s*=\s*"([^"]+)"/m.exec(pricesPySource)?.[1];
  if (!version) throw new Error('prices.py: PRICING_VERSION not found');
  const prices = [...pricesPySource.matchAll(PRICE_CALL)].map((match) => ({
    provider: match[1],
    modelPattern: match[2],
    input: match[3],
    output: match[4],
    cachedInput: match[5] ?? null,
  }));
  if (prices.length === 0) throw new Error('prices.py: no prices found');
  return { version, prices };
}

const row = (price) =>
  `| ${price.provider} | \`${price.modelPattern}\` | ${price.input} | ${price.cachedInput ?? '—'} | ${price.output} |`;

/**
 * The Markdown of the Prices page.
 * @param {ReturnType<typeof parsePrices>} parsed
 * @param {{ editUrl?: string | false }} [options]
 * @returns {string}
 */
export function renderPricesPage({ version, prices }, { editUrl = false } = {}) {
  const head = frontmatter({
    title: 'Prices',
    description:
      'The built-in price table Spanlight uses to work out the cost of an LLM call: every model, its rates per million tokens, and how a span’s model is matched to a row.',
    editUrl,
  });
  return `${head}This page lists every model that has a built-in price. It is generated from the price table in the Spanlight backend, so it is always the table your version of Spanlight uses. The table version is \`${version}\`; a priced span stores it as its \`pricing_version\`.

Rates are in US dollars per million tokens, at the providers' standard rates. They leave out batch discounts, surcharges for very long prompts, cache-write premiums, regional modifiers and taxes. A model that is not listed has an unknown cost, shown as a dash, never as zero. To price such a model, add a [price override](/docs/api/prices/).

## How a model matches a row

A row is written for a model name. A span's model matches the row when it is:

- The same name exactly, or
- The name followed by one snapshot suffix: \`-YYYYMMDD\`, \`-YYYY-MM-DD\` or \`-latest\`.

A plain prefix does not match. Matching is case-sensitive. When the span names a provider, the provider must also equal the row's provider (compared in lower case). A span with provider \`azure\` and model \`gpt-4o\` gets no price from the \`openai\` row. A span with no provider can match a row of any provider. When more than one row matches, the longest name wins. When a row has no cached rate (shown as —), cached tokens are billed at the input rate. [How cost is calculated](/docs/concepts/cost/) explains the formula and the token convention.

## Price table

${prices.length} models. Rates are per million tokens.

| Provider | Model | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
${prices.map(row).join('\n')}

## Related pages

- [How cost is calculated](/docs/concepts/cost/): the formula, the token convention and when a cost is unknown.
- [Prices endpoints](/docs/api/prices/): read the table and manage price overrides for your organization.
- [OTLP attribute mapping](/docs/reference/otlp-mapping/): which attributes give a span its model and token counts.
`;
}
