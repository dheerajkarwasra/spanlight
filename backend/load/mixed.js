// Dashboard reads while ingestion runs. Three scenarios run in parallel for DURATION: `ingest`
// sends INGEST_RPS requests a second of 100 spans each (the request of ingest.js), and `overview`
// and `traces` each read READ_RPS times a second (the requests of overview.js and traces.js: the
// overview windows from 1 hour to 30 days, and the trace list's first page, filters and sorts).
// Each scenario has its own thresholds, the same as its script run alone: ingest p95 below
// 200 ms, reads p95 below 300 ms, under 1 % failed, checks above 99 % and no dropped iteration.
// INGEST_RPS=0 leaves the ingest scenario out, which is the baseline. The workflow runs three
// stages, one k6 run each with settle.sh between: baseline (0), half (5) and near-knee (10, its
// result reported but not gated). See docs/performance.md, "Reads during ingestion".
//
// Environment (all optional in the container the workflow starts):
//   BASE_URL          where the web service listens, default http://localhost:8080
//   DURATION          how long to hold the rates, default 30s
//   CREDENTIALS_FILE  JSON written by seed.py, default /creds/credentials.json
//   RESULTS_DIR       where the JSON and Markdown summaries go, default /results
//   INGEST_RPS        ingest requests a second (100 spans each), default 5; 0 for the baseline
//   READ_RPS          requests a second of each read scenario, default 10
//   NAME              names the result files (<NAME>.json, <NAME>.md), default mixed
import ingestOnce from './ingest.js';
import readOverview, { windowRows } from './overview.js';
import readTraces, { variantRows } from './traces.js';
import { TREND_STATS, summarize } from './summary.js';

const DURATION = __ENV.DURATION || '30s';
const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const NAME = __ENV.NAME || 'mixed';
const INGEST_RPS = parseInt(__ENV.INGEST_RPS || '5', 10);
const READ_RPS = parseInt(__ENV.READ_RPS || '10', 10);
const SPANS_PER_REQUEST = 100; // what ingest.js sends in one request

function arrival(exec, rate) {
  return {
    executor: 'constant-arrival-rate',
    exec,
    rate,
    timeUnit: '1s',
    duration: DURATION,
    // A multiple of the number of read keys, so every key gets the same share of the virtual users.
    preAllocatedVUs: 20,
    maxVUs: 100,
  };
}

const scenarios = {
  overview: arrival('overview', READ_RPS),
  traces: arrival('traces', READ_RPS),
};
// A constant-arrival-rate of 0 is not valid, so the baseline has no ingest scenario at all.
if (INGEST_RPS > 0) scenarios.ingest = arrival('ingest', INGEST_RPS);

const P95_LIMIT_MS = { ingest: 200, overview: 300, traces: 300 };
const thresholds = {};
for (const name of Object.keys(scenarios)) {
  const tag = `{scenario:${name}}`;
  thresholds[`http_req_duration${tag}`] = [`p(95)<${P95_LIMIT_MS[name]}`];
  thresholds[`http_req_failed${tag}`] = ['rate<0.01'];
  thresholds[`checks${tag}`] = ['rate>0.99'];
  // The rate was held: k6 drops an iteration when it has no free virtual user left.
  thresholds[`dropped_iterations${tag}`] = ['count==0'];
}

export const options = { scenarios, thresholds, summaryTrendStats: TREND_STATS };

export function ingest() {
  ingestOnce();
}

export function overview() {
  readOverview();
}

export function traces() {
  readTraces();
}

export function handleSummary(data) {
  const ingestText =
    INGEST_RPS > 0
      ? `${INGEST_RPS} ingest requests a second (${INGEST_RPS * SPANS_PER_REQUEST} spans)`
      : 'no ingestion';
  return summarize({
    name: NAME,
    title: `Reads during ingestion (${NAME}): ${ingestText}`,
    description:
      `${ingestText} while the overview and the trace list are each read ${READ_RPS} times a ` +
      `second, for ${DURATION}.`,
    data,
    resultsDir: RESULTS_DIR,
    latencies: Object.keys(scenarios).map((name) => ({
      label: `${name} request time`,
      metric: `http_req_duration{scenario:${name}}`,
    })),
    extraLines: [
      '| Window | Read from | Median | p95 | Max |',
      '|---|---|---|---|---|',
      ...windowRows(data),
      '',
      '| Variant | Median | p95 | Max |',
      '|---|---|---|---|',
      ...variantRows(data),
      '',
    ],
  });
}
