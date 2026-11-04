// Gateway overhead: what a call costs for passing through Spanlight. Two scenarios of 50 requests
// a second each send the same chat completion, one after the other:
//
//   gateway  POST /gw/v1/chat/completions on the web service (Caddy), with a gateway key. The path
//            of a self-hosted call: Caddy, the api (key lookup, limits, the route, the sealed
//            credential, the upstream call) and the fake provider, with the span written
//            afterwards in the background.
//   direct   POST /v1/chat/completions on the fake provider itself (fake_provider.py).
//
// The provider answers at once, so what the gateway adds is the difference of the two. The overhead
// is the p95 of `gateway` minus the p95 of `direct`, and it must stay under 20 ms. Thresholds:
// p95 of `gateway` below 25 ms, p95 of `direct` below 5 ms (a slow provider would hide the
// overhead). k6 cannot compare two thresholds, so the overhead is computed in handleSummary,
// shown in the table and stored in the JSON as `overhead` with `ok`; the workflow fails the job
// when `ok` is not true.
//
// A third scenario, `budget`, sends the same request at the same rate with the second key that
// `seed_gateway.py --budget-spans N` writes: a key under a block budget, so every call reads the
// month's spend history before it is forwarded (ADR 0017). `budget_overhead` in the JSON is the
// p95 of `budget` minus the p95 of `gateway`, and must stay under 5 ms. Choose the scenarios with
// GATEWAY_SCENARIOS; they run one after the other in the order given.
//
// `concurrency` holds 200 virtual users of streaming chat (GATEWAY_VUS overrides the count) for
// the run's duration, alternating over the two seeded gateway keys as two clients would, against
// a slow provider (FAKE_PROVIDER_DELAY_MS=1000 on the stack). It measures gateway admission: every
// call must be answered (any 503 fails the run), its latency counts successful calls only, and
// `concurrency` in the JSON holds the p95 of the successes, the refused count and `ok`.
//
// Environment (all optional in the container the workflow starts):
//   BASE_URL                  where the web service listens, default http://localhost:8080
//   FAKE_PROVIDER_URL         where the fake provider listens, default http://localhost:9000
//   GATEWAY_DURATION_SECONDS  how long each scenario holds its rate, default 60
//   GATEWAY_CREDENTIALS_FILE  JSON written by seed_gateway.py, default
//                             /creds/gateway-credentials.json
//   RESULTS_DIR               where the JSON and Markdown summaries go, default /results
//   GATEWAY_SCENARIOS         comma list of gateway, direct, budget and concurrency, default
//                             gateway,direct
//   GATEWAY_VUS               when set, each scenario runs this many virtual users in a loop
//                             instead of 50 requests a second: a concurrency run, for a slow
//                             provider (FAKE_PROVIDER_DELAY_MS)
import http from 'k6/http';
import { check } from 'k6';
import { TREND_STATS, formatValue, summarize } from './summary.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const FAKE_PROVIDER_URL = __ENV.FAKE_PROVIDER_URL || 'http://localhost:9000';
const DURATION_SECONDS = parseInt(__ENV.GATEWAY_DURATION_SECONDS || '60', 10);
const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const SCENARIOS = (__ENV.GATEWAY_SCENARIOS || 'gateway,direct')
  .split(',')
  .map((name) => name.trim())
  .filter(Boolean);
const VUS = __ENV.GATEWAY_VUS ? Number(__ENV.GATEWAY_VUS) : null;
if (VUS !== null && !(Number.isInteger(VUS) && VUS > 0)) {
  throw new Error(`GATEWAY_VUS must be a positive integer, got ${__ENV.GATEWAY_VUS}`);
}
const credentials = JSON.parse(
  open(__ENV.GATEWAY_CREDENTIALS_FILE || '/creds/gateway-credentials.json'),
);

const REQUESTS_PER_SECOND = 50;
// The pause between the scenarios: the gateway's last spans are still being written when the
// first scenario ends, and that work should not overlap the second.
const PAUSE_SECONDS = 10;
const OVERHEAD_LIMIT_MS = 20;
const BUDGET_OVERHEAD_LIMIT_MS = 5;
const CONCURRENCY_VUS = 200;

const GATEWAY_URL = `${BASE_URL}/gw/v1/chat/completions`;
const DIRECT_URL = `${FAKE_PROVIDER_URL}/v1/chat/completions`;
const GATEWAY_DURATION_METRIC = 'http_req_duration{scenario:gateway}';
const DIRECT_DURATION_METRIC = 'http_req_duration{scenario:direct}';
// Budget overhead compares successful calls only, and any failed budget call fails the figure.
const BUDGET_DURATION_METRIC = 'http_req_duration{scenario:budget,expected_response:true}';
const BUDGET_BASE_METRIC = 'http_req_duration{scenario:gateway,expected_response:true}';
const BUDGET_FAILED_METRIC = 'http_req_failed{scenario:budget}';
// Fast 503s would pull a p95 over every response down, so only successful calls count.
const CONCURRENCY_DURATION_METRIC =
  'http_req_duration{scenario:concurrency,expected_response:true}';
const CONCURRENCY_FAILED_METRIC = 'http_req_failed{scenario:concurrency}';

// A 429 or any other status outside 2xx counts as a failed request, not just a slow one.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }));

function arrivalRate(exec, startTime) {
  return {
    executor: 'constant-arrival-rate',
    exec,
    startTime,
    rate: REQUESTS_PER_SECOND,
    timeUnit: '1s',
    duration: `${DURATION_SECONDS}s`,
    preAllocatedVUs: 20,
    maxVUs: 100,
  };
}

function constantVus(exec, startTime, vus = VUS) {
  return {
    executor: 'constant-vus',
    exec,
    startTime,
    vus,
    duration: `${DURATION_SECONDS}s`,
  };
}

const EXEC = {
  gateway: 'throughGateway',
  direct: 'direct',
  budget: 'throughBudget',
  concurrency: 'streamThroughGateway',
};
const P95_LIMIT_MS = { gateway: 25, direct: 5 };

const scenarios = {};
const thresholds = {};
SCENARIOS.forEach((name, index) => {
  if (!EXEC[name]) throw new Error(`GATEWAY_SCENARIOS: unknown scenario ${name}`);
  const startTime = `${index * (DURATION_SECONDS + PAUSE_SECONDS)}s`;
  if (name === 'concurrency') {
    scenarios[name] = constantVus(EXEC[name], startTime, VUS || CONCURRENCY_VUS);
    thresholds[CONCURRENCY_DURATION_METRIC] = ['p(95)>=0'];
    thresholds[CONCURRENCY_FAILED_METRIC] = ['rate==0'];
    thresholds[`http_reqs{scenario:${name}}`] = ['count>0'];
    return;
  }
  scenarios[name] = (VUS ? constantVus : arrivalRate)(EXEC[name], startTime);
  // A slow provider makes the limits meaningless; k6 reports a sub-metric only when a threshold
  // names it, so the others get one that always passes.
  const limit = VUS ? null : P95_LIMIT_MS[name];
  thresholds[`http_req_duration{scenario:${name}}`] = [limit ? `p(95)<${limit}` : 'p(95)>=0'];
  thresholds[`http_req_failed{scenario:${name}}`] = ['rate<0.01'];
  if (name === 'budget') {
    thresholds[BUDGET_DURATION_METRIC] = ['p(95)>=0'];
    thresholds[BUDGET_BASE_METRIC] = ['p(95)>=0'];
  }
  // The throughput of a concurrency run.
  if (VUS) thresholds[`http_reqs{scenario:${name}}`] = ['count>0'];
});
thresholds.checks = ['rate>0.99'];
// The rate was held: k6 drops an iteration when it has no free virtual user left.
thresholds.dropped_iterations = ['count==0'];

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: TREND_STATS,
};

// The same request both ways: a short chat completion. The gateway key has no cache, so every
// call goes upstream.
const BODY = JSON.stringify({
  model: credentials.model,
  messages: [{ role: 'user', content: 'Summarize the ticket history in one sentence.' }],
  max_tokens: 64,
});

function headers(token) {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };
}

function send(url, token, name) {
  return http.post(url, BODY, { headers: headers(token), tags: { name } });
}

function answered(response) {
  if (response.status !== 200) return false;
  try {
    const choices = response.json('choices');
    return Array.isArray(choices) && choices.length === 1;
  } catch (error) {
    return false; // a 200 that is not JSON
  }
}

// A few calls each way before the clock starts, so the first scenario does not pay for opening
// the api's connection to the provider, the first key lookup or a cold Python code path. A broken
// setup stops the run here with the reason instead of failing 3 000 requests one by one.
export function setup() {
  const keys = [credentials.gateway_key];
  if (SCENARIOS.includes('concurrency')) {
    if (!credentials.gateway_keys || credentials.gateway_keys.length < 2) {
      throw new Error('concurrency: no second gateway key in the credentials (re-run the seed)');
    }
    keys.push(credentials.gateway_keys[1]);
  }
  if (SCENARIOS.includes('budget')) {
    if (!credentials.budget_key) {
      throw new Error('budget: no budget_key in the credentials (seed with --budget-spans N)');
    }
    keys.push(credentials.budget_key);
  }
  for (let call = 0; call < 20; call++) {
    for (const key of keys) {
      const response = send(GATEWAY_URL, key, 'warm-up gateway');
      if (!answered(response)) {
        throw new Error(
          `gateway: HTTP ${response.status} ${String(response.body).slice(0, 300)} (is the ` +
            'gateway seeded, and the fake provider up?)',
        );
      }
    }
    if (!SCENARIOS.includes('direct')) continue;
    if (!answered(send(DIRECT_URL, 'load-test', 'warm-up direct'))) {
      throw new Error(`the fake provider did not answer at ${DIRECT_URL}`);
    }
  }
}

export function throughGateway() {
  const response = send(GATEWAY_URL, credentials.gateway_key, 'POST /gw/v1/chat/completions');
  check(response, { 'gateway: answered with one choice': answered });
}

export function throughBudget() {
  const name = 'POST /gw/v1/chat/completions (budget)';
  const response = send(GATEWAY_URL, credentials.budget_key, name);
  check(response, { 'budget: answered with one choice': answered });
}

const STREAM_BODY = JSON.stringify({
  model: credentials.model,
  stream: true,
  messages: [{ role: 'user', content: 'Summarize the ticket history in one sentence.' }],
  max_tokens: 64,
});

function streamed(response) {
  return response.status === 200 && String(response.body).trimEnd().endsWith('data: [DONE]');
}

export function streamThroughGateway() {
  const key = credentials.gateway_keys[__VU % 2];
  const response = http.post(GATEWAY_URL, STREAM_BODY, {
    headers: headers(key),
    tags: { name: 'POST /gw/v1/chat/completions (stream)' },
  });
  check(response, { 'concurrency: streamed to [DONE]': streamed });
}

export function direct() {
  const response = send(DIRECT_URL, 'load-test', 'POST /v1/chat/completions (direct)');
  check(response, { 'direct: answered with one choice': answered });
}

function p95Of(data, metricName) {
  const metric = data.metrics[metricName];
  const value = metric && metric.values ? metric.values['p(95)'] : undefined;
  return value === undefined ? null : value;
}

// The figure the run is about. Without both p95 values it cannot be computed, which is a failure.
function overheadOf(data) {
  const gateway = p95Of(data, GATEWAY_DURATION_METRIC);
  const direct = p95Of(data, DIRECT_DURATION_METRIC);
  const overhead = gateway === null || direct === null ? null : gateway - direct;
  return {
    gateway_p95_ms: gateway,
    direct_p95_ms: direct,
    overhead_ms: overhead,
    limit_ms: OVERHEAD_LIMIT_MS,
    ok: overhead !== null && overhead < OVERHEAD_LIMIT_MS,
  };
}

function budgetOverheadOf(data) {
  const gateway = p95Of(data, BUDGET_BASE_METRIC);
  const budget = p95Of(data, BUDGET_DURATION_METRIC);
  const overhead = gateway === null || budget === null ? null : budget - gateway;
  const failed = data.metrics[BUDGET_FAILED_METRIC];
  const failedRate = failed && failed.values ? failed.values.rate : null;
  return {
    gateway_p95_ms: gateway,
    budget_p95_ms: budget,
    budget_overhead_ms: overhead,
    budget_failed_rate: failedRate,
    limit_ms: BUDGET_OVERHEAD_LIMIT_MS,
    ok: overhead !== null && overhead < BUDGET_OVERHEAD_LIMIT_MS && failedRate === 0,
  };
}

function concurrencyOf(data) {
  const failed = data.metrics[CONCURRENCY_FAILED_METRIC];
  const refused = failed && failed.values ? failed.values.passes : null;
  return {
    vus: VUS || CONCURRENCY_VUS,
    success_p95_ms: p95Of(data, CONCURRENCY_DURATION_METRIC),
    refused,
    ok: refused === 0,
  };
}

function concurrencyLine(result) {
  return (
    `| Refused or failed calls (${result.vus} streaming users) | 0 ` +
    `| ${result.refused ?? '—'} | ${result.ok ? 'pass' : '**FAIL**'} |`
  );
}

function budgetOverheadLine(overhead) {
  const observed = formatValue(overhead.budget_overhead_ms ?? undefined, 'time');
  return (
    '| Budget admission overhead (p95 budget − p95 gateway) ' +
    `| below ${BUDGET_OVERHEAD_LIMIT_MS.toFixed(1)} ms | ${observed} ` +
    `| ${overhead.ok ? 'pass' : '**FAIL**'} |`
  );
}

function overheadLines(overhead) {
  const observed =
    overhead.overhead_ms === null
      ? formatValue(undefined, 'time')
      : formatValue(overhead.overhead_ms, 'time');
  return [
    '| Figure | Limit | Observed | Result |',
    '|---|---|---|---|',
    '| Gateway overhead (p95 gateway − p95 direct) ' +
      `| below ${OVERHEAD_LIMIT_MS.toFixed(1)} ms | ${observed} ` +
      `| ${overhead.ok ? 'pass' : '**FAIL**'} |`,
  ];
}

// The default run's summary stays as it was; other selections report what they measured.
function selectedSummary(data, requests) {
  const budget = SCENARIOS.includes('budget') ? budgetOverheadOf(data) : null;
  const overhead = SCENARIOS.includes('direct') ? overheadOf(data) : null;
  const concurrency = SCENARIOS.includes('concurrency') ? concurrencyOf(data) : null;
  const lines = ['| Figure | Limit | Observed | Result |', '|---|---|---|---|'];
  if (overhead) lines.push(overheadLines(overhead)[2]);
  if (budget) lines.push(budgetOverheadLine(budget));
  if (concurrency) lines.push(concurrencyLine(concurrency));
  const extra = {};
  if (overhead) extra.overhead = overhead;
  if (budget) extra.budget_overhead = budget;
  if (concurrency) extra.concurrency = concurrency;
  const load = (name) =>
    VUS || name === 'concurrency'
      ? `${name} ${VUS || CONCURRENCY_VUS} virtual users in a loop for ${DURATION_SECONDS} s`
      : `${name} ${REQUESTS_PER_SECOND} requests a second for ${DURATION_SECONDS} s`;
  return summarize({
    name: 'gateway',
    title: 'Gateway: overhead of a call through Spanlight',
    description: `Scenarios one after the other: ${SCENARIOS.map(load).join('; ')}.`,
    data: Object.assign({}, data, extra),
    resultsDir: RESULTS_DIR,
    latencies: SCENARIOS.map((name) =>
      name === 'concurrency'
        ? {
            label: 'Request time (concurrency, successes)',
            metric: CONCURRENCY_DURATION_METRIC,
          }
        : {
            label: `Request time (${name})`,
            metric: `http_req_duration{scenario:${name}}`,
          },
    ),
    sent: requests ? `Sent ${requests.count} requests in all.` : 'No request was sent.',
    extraLines: lines.length > 2 ? lines : [],
  });
}

export function handleSummary(data) {
  const requests = data.metrics.http_reqs && data.metrics.http_reqs.values;
  if (SCENARIOS.join(',') !== 'gateway,direct' || VUS) return selectedSummary(data, requests);
  const overhead = overheadOf(data);
  return summarize({
    name: 'gateway',
    title: 'Gateway: overhead of a call through Spanlight',
    description:
      `${REQUESTS_PER_SECOND} requests a second for ${DURATION_SECONDS} s through the gateway, ` +
      'then the same directly to the fake provider, which answers at once.',
    // The overhead is stored in the JSON beside the metrics, for the workflow to check.
    data: Object.assign({}, data, { overhead }),
    resultsDir: RESULTS_DIR,
    latencies: [
      {
        label: 'Request time through the gateway',
        metric: GATEWAY_DURATION_METRIC,
      },
      { label: 'Request time direct', metric: DIRECT_DURATION_METRIC },
    ],
    sent: requests ? `Sent ${requests.count} requests in all.` : 'No request was sent.',
    extraLines: overheadLines(overhead),
  });
}
