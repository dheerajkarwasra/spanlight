// Dashboard overview load: 20 requests a second on GET /api/v1/projects/{id}/metrics/overview,
// read with API keys that hold `traces:read`. The time window rotates through 1 hour, 24 hours,
// 7 days and 30 days: the first two read the raw spans, the other two the hourly rollups. Every
// window ends at `data_end` of the credentials file, the instant the seeded data ends at, and not
// at the wall clock: a seed that took hours would otherwise leave the 1 hour window empty. The
// target is the web service (Caddy). Threshold: p95 of the request time below 300 ms; the p95 of
// each window is shown in the summary but is not a threshold.
//
// Environment (all optional in the container the workflow starts):
//   BASE_URL          where the web service listens, default http://localhost:8080
//   DURATION          how long to hold the rate, default 30s
//   CREDENTIALS_FILE  JSON written by seed.py, default /creds/credentials.json
//   RESULTS_DIR       where the JSON and Markdown summaries go, default /results
import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { TREND_STATS, formatValue, summarize } from './summary.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const DURATION = __ENV.DURATION || '30s';
const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const credentials = JSON.parse(open(__ENV.CREDENTIALS_FILE || '/creds/credentials.json'));

const REQUESTS_PER_SECOND = 20;
const HOUR_MS = 3600 * 1000;

// `rollup` is what the API reports in `approximate`: windows over 24 hours read the rollups.
const WINDOWS = [
  { name: '1h', lengthMs: HOUR_MS, rollup: false },
  { name: '24h', lengthMs: 24 * HOUR_MS, rollup: false },
  { name: '7d', lengthMs: 7 * 24 * HOUR_MS, rollup: true },
  { name: '30d', lengthMs: 30 * 24 * HOUR_MS, rollup: true },
];

// Tags alone do not reach the summary unless a threshold names them, so each window also gets a
// metric of its own.
const durationByWindow = {};
for (const timeWindow of WINDOWS) {
  durationByWindow[timeWindow.name] = new Trend(`overview_duration_${timeWindow.name}`, true);
}

// A 429 or any other status outside 2xx counts as a failed request, not just a slow one.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }));

export const options = {
  scenarios: {
    overview: {
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
  const timeWindow = WINDOWS[exec.scenario.iterationInTest % WINDOWS.length];
  const to = new Date(credentials.data_end);
  const from = new Date(to.getTime() - timeWindow.lengthMs);
  // Each key may read 20 times a second. A virtual user always uses the same key, and the ten
  // keys are shared out evenly, so each key sees about two requests a second.
  const key = credentials.read_keys[__VU % credentials.read_keys.length];
  const response = http.get(
    `${BASE_URL}/api/v1/projects/${credentials.project_id}/metrics/overview` +
      `?from=${from.toISOString()}&to=${to.toISOString()}`,
    {
      headers: { Authorization: `Bearer ${key}` },
      tags: { name: 'GET /metrics/overview', window: timeWindow.name },
    },
  );
  durationByWindow[timeWindow.name].add(response.timings.duration);
  const served = check(response, {
    'status is 200': (r) => r.status === 200,
    // The seeded data covers every window. An empty one is fast for the wrong reason, and for 7
    // and 30 days it is what missing rollups look like (`approximate` follows the window length
    // alone, not whether rollup rows exist).
    'the window has traces': (r) => r.status === 200 && r.json('current.traces') > 0,
    'reported the expected source': (r) =>
      r.status === 200 && r.json('approximate') === timeWindow.rollup,
  });
  if (!served && !reportedFailure) {
    reportedFailure = true; // once per virtual user, so a broken run does not flood the log
    console.error(`overview: HTTP ${response.status} ${String(response.body).slice(0, 300)}`);
  }
}
// Not a threshold: how each window did, to show where the time goes. mixed.js reuses it.
export function windowRows(data) {
  return WINDOWS.map((timeWindow) => {
    const metric = data.metrics[`overview_duration_${timeWindow.name}`];
    const values = metric && metric.values ? metric.values : {};
    const cell = (stat) => formatValue(values[stat], metric && metric.contains);
    const source = timeWindow.rollup ? 'hourly rollups' : 'raw spans';
    return `| ${timeWindow.name} | ${source} | ${cell('med')} | ${cell('p(95)')} | ${cell('max')} |`;
  });
}

export function handleSummary(data) {
  return summarize({
    name: 'overview',
    title: `Overview: ${REQUESTS_PER_SECOND} requests per second`,
    description:
      `${REQUESTS_PER_SECOND} requests a second for ${DURATION}, rotating through the windows below.`,
    data,
    resultsDir: RESULTS_DIR,
    extraLines: [
      '| Window | Read from | Median | p95 | Max |',
      '|---|---|---|---|---|',
      ...windowRows(data),
      '',
    ],
  });
}
