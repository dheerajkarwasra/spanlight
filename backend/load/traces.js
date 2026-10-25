// Trace list load: 20 requests a second on GET /api/v1/projects/{id}/traces, read with API keys
// that hold `traces:read`. Out of ten requests, three are the first page the dashboard opens with
// (the last 24 hours, no filter), two filter by one model and one to failed traces, and four sort
// the first page by a value (duration, cost, tokens and retry cost, one each, largest first).
// Each variant's time is a metric of its own, so the summary shows each sort beside the first
// page. The 24 hours end at `data_end` of the credentials file, the
// instant the seeded data ends at, and not at the wall clock: a seed that took hours would
// otherwise leave the end of the day empty. The target is the web service (Caddy). Threshold: p95
// of the request time below 300 ms.
//
// Environment (all optional in the container the workflow starts):
//   BASE_URL          where the web service listens, default http://localhost:8080
//   DURATION          how long to hold the rate, default 30s
//   CREDENTIALS_FILE  JSON written by seed.py, default /creds/credentials.json
//   RESULTS_DIR       where the JSON and Markdown summaries go, default /results
//   FILTER_MODEL      a model that seed.py stores, default claude-haiku-4-5 (its most common)
import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { TREND_STATS, formatValue, summarize } from './summary.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const DURATION = __ENV.DURATION || '30s';
const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const FILTER_MODEL = __ENV.FILTER_MODEL || 'claude-haiku-4-5';
const credentials = JSON.parse(open(__ENV.CREDENTIALS_FILE || '/creds/credentials.json'));

const REQUESTS_PER_SECOND = 20;

const WINDOW_END = new Date(credentials.data_end);
const WINDOW_START = new Date(WINDOW_END.getTime() - 24 * 3600 * 1000);
const WINDOW = `from=${WINDOW_START.toISOString()}&to=${WINDOW_END.toISOString()}`;

const ONE_MODEL = { name: 'one-model', filter: `&model=${encodeURIComponent(FILTER_MODEL)}` };
const ERRORS_ONLY = { name: 'errors-only', filter: '&status=error' };
const FIRST_PAGE = { name: 'first-page', filter: '' };
const VARIANTS = [
  FIRST_PAGE,
  ONE_MODEL,
  { name: 'sort-duration', filter: '&sort=duration' },
  FIRST_PAGE,
  ERRORS_ONLY,
  { name: 'sort-cost', filter: '&sort=cost' },
  FIRST_PAGE,
  ONE_MODEL,
  { name: 'sort-tokens', filter: '&sort=tokens' },
  { name: 'sort-retry-cost', filter: '&sort=retry_cost_usd' },
];
const VARIANT_NAMES = [...new Set(VARIANTS.map((variant) => variant.name))];

// Tags alone do not reach the summary unless a threshold names them, so each variant also gets a
// metric of its own (metric names take no `-`).
const metricName = (variantName) => `traces_duration_${variantName.replaceAll('-', '_')}`;
const durationByVariant = {};
for (const variantName of VARIANT_NAMES) {
  durationByVariant[variantName] = new Trend(metricName(variantName), true);
}

// A 429 or any other status outside 2xx counts as a failed request, not just a slow one.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }));

export const options = {
  scenarios: {
    traces: {
      executor: 'constant-arrival-rate',
      rate: REQUESTS_PER_SECOND,
      timeUnit: '1s',
      duration: DURATION,
      // A multiple of the number of keys, so every key gets the same share of the virtual users.
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
    // The rate was held: k6 drops an iteration when it has no free virtual user left.
    dropped_iterations: ['count==0'],
  },
  summaryTrendStats: TREND_STATS,
};

let reportedFailure = false;

export default function () {
  const variant = VARIANTS[exec.scenario.iterationInTest % VARIANTS.length];
  // Each key may read 20 times a second. A virtual user always uses the same key, and the ten
  // keys are shared out evenly, so each key sees about two requests a second.
  const key = credentials.read_keys[__VU % credentials.read_keys.length];
  const response = http.get(
    `${BASE_URL}/api/v1/projects/${credentials.project_id}/traces?${WINDOW}${variant.filter}`,
    {
      headers: { Authorization: `Bearer ${key}` },
      tags: { name: 'GET /traces', variant: variant.name },
    },
  );
  durationByVariant[variant.name].add(response.timings.duration);
  const served = check(response, {
    'status is 200': (r) => r.status === 200,
    // An empty page is fast for the wrong reason: the seeded data has matches for every variant.
    'the page has traces': (r) => {
      if (r.status !== 200) return false;
      const { items } = r.json();
      return Array.isArray(items) && items.length > 0;
    },
  });
  if (!served && !reportedFailure) {
    reportedFailure = true; // once per virtual user, so a broken run does not flood the log
    console.error(`traces: HTTP ${response.status} ${String(response.body).slice(0, 300)}`);
  }
}

// Not a threshold: each variant's time, to compare the sorts with the first page. mixed.js
// reuses it.
export function variantRows(data) {
  return VARIANT_NAMES.map((variantName) => {
    const metric = data.metrics[metricName(variantName)];
    const values = metric && metric.values ? metric.values : {};
    const cell = (stat) => formatValue(values[stat], metric && metric.contains);
    return `| ${variantName} | ${cell('med')} | ${cell('p(95)')} | ${cell('max')} |`;
  });
}

export function handleSummary(data) {
  return summarize({
    name: 'traces',
    title: `Trace list: ${REQUESTS_PER_SECOND} requests per second`,
    description:
      `${REQUESTS_PER_SECOND} requests a second for ${DURATION}: the first page, the first page ` +
      'filtered by model or to failed traces, and the first page sorted by duration, cost, ' +
      'tokens or retry cost.',
    data,
    resultsDir: RESULTS_DIR,
    extraLines: [
      '| Variant | Median | p95 | Max |',
      '|---|---|---|---|',
      ...variantRows(data),
      '',
    ],
  });
}
