// Builds the "Prices" reference page from the backend's price table, so the page never holds a
// hand-made copy of a rate: `backend/app/pricing/prices.py` is the only place a number is written.
//
// Only the current snapshot is rendered: the `_price(...)` calls inside the `SEED_PRICES` block,
// the service-tier factors of `TIER_MULTIPLIERS` (with the pairs of `COMMITMENT_BILLED`) and the
// `_fee(...)` calls of `SEED_FEES`. Historical snapshots are `_price(...)` calls outside that block
// and are left out on purpose, because a reader wants the table their spans are priced with now.

import { frontmatter } from './openapi.mjs';

// A `_price(` call and everything up to its closing parenthesis, matched on source whose comments
// are already stripped: no argument holds a parenthesis, so the first `)` ends the call.
const PRICE_CALL = /\b_price\(([^)]*)\)/g;

/**
 * The source without its `#` comments, so a comment holding a parenthesis or a comma cannot cut a
 * call short or glue itself to an argument. A `#` inside a double-quoted string is kept (no model
 * name holds one today, but a rate pattern must never be rewritten by the stripper). Python's
 * other string forms are not used in the price table.
 * @param {string} source
 */
export function stripComments(source) {
  return source
    .split('\n')
    .map((line) => {
      let quoted = false;
      for (let i = 0; i < line.length; i += 1) {
        const char = line[i];
        if (char === '"' && line[i - 1] !== '\\') quoted = !quoted;
        else if (char === '#' && !quoted) return line.slice(0, i);
      }
      return line;
    })
    .join('\n');
}
const SEED_BLOCK = /^SEED_PRICES\b[^=\n]*=\s*\(\n([\s\S]*?)^\)/m;
const POSITIONAL = ['provider', 'modelPattern', 'input', 'output', 'cachedInput'];
const KEYWORDS = { write_5m: 'cacheWrite5m', write_1h: 'cacheWrite1h', min_prompt_tokens: 'minPromptTokens' };

/** One Python argument: a string literal, `None`, or an integer such as `100_001`. */
function parseValue(raw, call) {
  const text = raw.trim();
  if (text === 'None') return null;
  const string = /^"([^"]*)"$/.exec(text);
  if (string) return string[1];
  if (/^\d[\d_]*$/.test(text)) return Number(text.replaceAll('_', ''));
  throw new Error(`prices.py: cannot read the argument ${text} in _price(${call})`);
}

/**
 * One `_price(...)` call. Positional arguments are provider, model, input, output and cached
 * rates; the keywords `write_5m`, `write_1h` and `min_prompt_tokens` are optional. Anything else
 * is an error, so a row the parser does not understand fails the sync instead of vanishing.
 * @param {string} call the text between the parentheses
 */
function parseCall(call) {
  const price = {
    provider: null,
    modelPattern: null,
    input: null,
    output: null,
    cachedInput: null,
    cacheWrite5m: null,
    cacheWrite1h: null,
    minPromptTokens: 0,
  };
  const args = call
    .split(',')
    .map((arg) => arg.trim())
    .filter((arg) => arg !== '');
  let position = 0;
  for (const arg of args) {
    const keyword = /^(\w+)\s*=\s*([\s\S]+)$/.exec(arg);
    if (keyword) {
      const field = KEYWORDS[keyword[1]];
      if (!field) throw new Error(`prices.py: unknown keyword ${keyword[1]} in _price(${call})`);
      price[field] = parseValue(keyword[2], call);
    } else {
      if (position >= POSITIONAL.length) throw new Error(`prices.py: too many arguments in _price(${call})`);
      price[POSITIONAL[position]] = parseValue(arg, call);
      position += 1;
    }
  }
  if (position !== POSITIONAL.length) {
    throw new Error(`prices.py: expected ${POSITIONAL.length} positional arguments in _price(${call})`);
  }
  for (const field of ['provider', 'modelPattern', 'input', 'output']) {
    if (typeof price[field] !== 'string') throw new Error(`prices.py: ${field} must be a string in _price(${call})`);
  }
  if (!Number.isInteger(price.minPromptTokens) || price.minPromptTokens < 0) {
    throw new Error(`prices.py: min_prompt_tokens must be a whole number in _price(${call})`);
  }
  return price;
}

/**
 * The version and every entry of the current price table: `PRICING_VERSION`, each `_price(...)`
 * call of the `SEED_PRICES` block, the service-tier factors and the server-tool fees.
 * @param {string} pricesPySource
 * @returns {{ version: string, prices: { provider: string, modelPattern: string, input: string, output: string, cachedInput: string | null, cacheWrite5m: string | null, cacheWrite1h: string | null, minPromptTokens: number }[], tierMultipliers: { provider: string, tier: string, factor: string, commitmentBilled: boolean }[], fees: { provider: string, tool: string, usdPer1k: string }[] }}
 */
export function parsePrices(pricesPySource) {
  const version = /^PRICING_VERSION\s*=\s*"([^"]+)"/m.exec(pricesPySource)?.[1];
  if (!version) throw new Error('prices.py: PRICING_VERSION not found');
  const source = stripComments(pricesPySource);
  const block = SEED_BLOCK.exec(source)?.[1];
  if (block === undefined) throw new Error('prices.py: SEED_PRICES block not found');
  const prices = [...block.matchAll(PRICE_CALL)].map((match) => parseCall(match[1]));
  if (prices.length === 0) throw new Error('prices.py: no prices found');
  const calls = (block.match(/\b_price\(/g) ?? []).length;
  if (calls !== prices.length) throw new Error(`prices.py: ${calls} _price calls but ${prices.length} parsed`);
  return { version, prices, tierMultipliers: parseTierMultipliers(source), fees: parseFees(source) };
}

const TIER_BLOCK = /^TIER_MULTIPLIERS\b[^=\n]*=\s*(?:MappingProxyType\(\s*)?\{\n([\s\S]*?)^\s*\}/m;
const TIER_ENTRY = /\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)\s*:\s*Decimal\(\s*"([^"]+)"\s*\)/g;
const COMMITMENT_BLOCK = /^COMMITMENT_BILLED\b[^=\n]*=\s*frozenset\(\{([^}]*)\}\)/m;
const PAIR = /\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)/g;
const FEE_BLOCK = /^SEED_FEES\b[^=\n]*=\s*\(\n([\s\S]*?)^\)/m;
const FEE_CALL = /\b_fee\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)/g;

/**
 * The service-tier factors of the current snapshot, in source order. A snapshot without a
 * `TIER_MULTIPLIERS` block has none. A pair listed in `COMMITMENT_BILLED` is billed by the provider
 * as a commitment, outside the per-token rate.
 * @param {string} source the price table with its comments stripped
 */
function parseTierMultipliers(source) {
  const block = TIER_BLOCK.exec(source)?.[1];
  if (block === undefined) return [];
  const committed = new Set(
    [...(COMMITMENT_BLOCK.exec(source)?.[1] ?? '').matchAll(PAIR)].map((match) => `${match[1]}/${match[2]}`),
  );
  const rows = [...block.matchAll(TIER_ENTRY)].map((match) => ({
    provider: match[1],
    tier: match[2],
    factor: match[3],
    commitmentBilled: committed.has(`${match[1]}/${match[2]}`),
  }));
  const entries = (block.match(/\)\s*:/g) ?? []).length;
  if (entries !== rows.length) throw new Error(`prices.py: ${entries} tier multipliers but ${rows.length} parsed`);
  return rows;
}

/**
 * The server-tool fees of the current snapshot: each `_fee(provider, tool, rate)` call of the
 * `SEED_FEES` block, the rate in USD per 1,000 requests. A snapshot without the block has none.
 * @param {string} source the price table with its comments stripped
 */
function parseFees(source) {
  const block = FEE_BLOCK.exec(source)?.[1];
  if (block === undefined) return [];
  const fees = [...block.matchAll(FEE_CALL)].map((match) => ({ provider: match[1], tool: match[2], usdPer1k: match[3] }));
  const calls = (block.match(/\b_fee\(/g) ?? []).length;
  if (calls !== fees.length) throw new Error(`prices.py: ${calls} _fee calls but ${fees.length} parsed`);
  return fees;
}

const formatTokens = (count) => count.toLocaleString('en-US');

/** The "Prompt" column: the prompt sizes a row prices. */
export const tierLabel = (minPromptTokens) =>
  minPromptTokens === 0 ? 'Any size' : `Over ${formatTokens(minPromptTokens - 1)} tokens`;

const cell = (rate) => rate ?? '—';

const COMMITMENT_NOTE = 'Billed as a commitment, outside the per-token rate; priced at the standard rate';

const tierRow = (entry) =>
  `| ${entry.provider} | \`${entry.tier}\` | ${entry.factor} |${entry.commitmentBilled ? ` ${COMMITMENT_NOTE} |` : ' |'}`;

const feeRow = (fee) => `| ${fee.provider} | \`${fee.tool}\` | ${fee.usdPer1k} |`;

function tierSection(tierMultipliers) {
  if (tierMultipliers.length === 0) return '';
  return `
## Service tiers

A span that reports the service tier the provider billed has its token cost multiplied by the factor of that tier. Standard calls, calls that report no tier and any tier not listed here are priced at the standard rate (factor 1); a tier with no factor is marked as unmodelled in the span's cost explanation. The factor never applies to a fee. [Service tiers](/docs/concepts/cost/#service-tiers) explains where the tier comes from.

| Provider | Tier | Factor | Note |
| --- | --- | --- | --- |
${tierMultipliers.map(tierRow).join('\n')}
`;
}

function feeSection(fees) {
  if (fees.length === 0) return '';
  return `
## Web search and other tool fees

A provider bills some server-side tools per request, apart from the tokens. The fee is added to the span's cost and reported again in its \`fee_usd\`. [Fees (web search)](/docs/concepts/cost/#fees-web-search) explains how the request count is read.

| Provider | Tool | USD per 1,000 requests |
| --- | --- | --- |
${fees.map(feeRow).join('\n')}
`;
}

const row = (price) =>
  `| ${price.provider} | \`${price.modelPattern}\` | ${tierLabel(price.minPromptTokens)} | ${price.input} | ${cell(price.cachedInput)} | ${cell(price.cacheWrite5m)} | ${cell(price.cacheWrite1h)} | ${price.output} |`;

/**
 * The Markdown of the Prices page.
 * @param {ReturnType<typeof parsePrices>} parsed
 * @param {{ editUrl?: string | false }} [options]
 * @returns {string}
 */
export function renderPricesPage({ version, prices, tierMultipliers = [], fees = [] }, { editUrl = false } = {}) {
  const head = frontmatter({
    title: 'Prices',
    description:
      'The built-in price table Spanlight uses to work out the cost of an LLM call: every model, its rates per million tokens, including prompt-cache write rates and prompt-size tiers, the service-tier factors and web search fees, and how a span’s model is matched to a row.',
    editUrl,
  });
  const models = new Set(prices.map((price) => `${price.provider}/${price.modelPattern}`)).size;
  const tiered = prices.filter((price) => price.minPromptTokens > 0).length;
  const tierNote =
    tiered > 0
      ? ` ${tiered === 1 ? 'One row prices' : `${tiered} rows price`} a second tier: the whole prompt is billed at that row's rates once the prompt is larger than the size in the **Prompt** column.`
      : '';
  return `${head}This page lists every model that has a built-in price. It is generated from the price table in the Spanlight backend, so it is always the table your version of Spanlight uses. The table version is \`${version}\`; a priced span stores it as its \`pricing_version\`.

Rates are in US dollars per million tokens, at the providers' standard rates. They include the prompt-cache read rate, the prompt-cache write rates by cache lifetime (5 minutes and 1 hour) and the higher rates a provider charges for long prompts, where the provider publishes them. Service-tier factors, such as the batch discount, and per-request tool fees, such as web search, apply on top of these rates; when the table has them they are listed below the price table. Regional modifiers, fast mode and taxes are left out. A model that is not listed has an unknown cost, shown as a dash, never as zero. To price such a model, add a [price override](/docs/api/prices/).

## How a model matches a row

A row is written for a model name. A span's model matches the row when it is:

- The same name exactly, or
- The name followed by one snapshot suffix: \`-YYYYMMDD\`, \`-YYYY-MM-DD\` or \`-latest\`.

A plain prefix does not match. Matching is case-sensitive. When the span names a provider, the provider must also equal the row's provider. The span's provider is trimmed and lower-cased first, and known aliases are folded onto the provider the table uses: \`gemini\`, \`vertex_ai\` and the other Google spellings find the \`google\` rows, and \`claude\` finds the \`anthropic\` rows. The row's provider is folded the same way, so an override saved as \`gemini\` matches a span sent as \`google\`. \`azure\` and \`azure_openai\` are not aliases of \`openai\`: Azure bills its own rates, and no Azure row is built in. The span keeps the provider it was sent with. A span with no provider can match a row of any provider. When more than one row matches, the longest name wins. [Which provider names match which price](/docs/concepts/cost/#which-provider-names-match-which-price) lists every alias.

A model with a prompt-size tier has one row per tier. The **Prompt** column says which prompts the row prices: **Any size** is the base row, and **Over N tokens** is the row used when the span's \`input_tokens\` is larger than N. The row with the largest size not above the span's input tokens prices the whole prompt.

When a row has no cached rate (shown as —), cached tokens are billed at the input rate; when it has no cache-write rate, cache writes are billed at the input rate too. [How cost is calculated](/docs/concepts/cost/) explains the formula and the token convention.

## Price table

${models} models, ${prices.length} rows. Rates are per million tokens.${tierNote}

| Provider | Model | Prompt | Input | Cached input | Cache write, 5 min | Cache write, 1 h | Output |
| --- | --- | --- | --- | --- | --- | --- | --- |
${prices.map(row).join('\n')}
${tierSection(tierMultipliers)}${feeSection(fees)}
## Related pages

- [How cost is calculated](/docs/concepts/cost/): the formula, the token convention and when a cost is unknown.
- [Prices endpoints](/docs/api/prices/): read the table and manage price overrides for your organization.
- [OTLP attribute mapping](/docs/reference/otlp-mapping/): which attributes give a span its model and token counts.
`;
}
