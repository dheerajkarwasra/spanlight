// Product tour: the screens and write paths the other scripts leave out, all at once. Every
// scenario below runs in parallel at a modest constant rate for DURATION, reading with a browser
// session (signed in once in setup with POST /api/v1/auth/login, the Origin header the frontend's
// browser sends, and the session cookie it gets back) or writing with the project's keys:
//
//   trace_detail   GET /traces/{id} over 50 seeded traces
//   big_trace      GET /traces/{id}?span_content=bounded on the trace of 5 000 spans
//   span_detail    GET /traces/{id}/spans/{span} and its /cost-explanation
//   sessions       GET /sessions (24 hours) and GET /sessions/{id}/turns
//   end_users      GET /users (7 days)
//   attribution    GET /attribution and /attribution/series by trace name (7 days)
//   insights       GET /insights and /insights/summary
//   datasets       GET /datasets and /datasets/{id}/items
//   experiments    GET /experiments, /experiments/{id}/runs and the compare of its two runs
//   evaluators     GET /evaluators
//   prompts        GET /prompts and /prompts/{name}/versions
//   annotations    GET /annotation-queues/{id}/items
//   otlp_json      POST /v1/otlp/traces, OTLP/JSON, 10 spans, with the ingest key
//   otlp_protobuf  POST /v1/otlp/traces, OTLP/protobuf (encoded below, no library), 10 spans
//   scores         POST /v1/scores, 10 scores on seeded traces, with the scores key
//   login          POST /api/v1/auth/login, LOGIN_PER_MINUTE a minute (argon2 verify)
//
// Session reads have no rate limit; the keys do (50 a second each on ingest and scores), far
// above these rates. Sign-in hashes are limited to 20 per email and client address in 15
// minutes (`MAX_HASHES_PER_EMAIL_IP`, correct passwords included), so `login` rotates over the
// users seed_tour.py made and refuses a rate above one sign-in per user a minute: 15 per user in
// 15 minutes, which leaves room for setup's sign-in. Two runs within 15 minutes can still add up
// to more than 20; wait between them or seed more users (--login-users).
//
// Thresholds: p95 below 500 ms for every read endpoint, and per scenario under 1 % failed (a 429
// is a failure), checks above 99 % and no dropped iteration. The summary also lists the median,
// p95, p99 and max of every endpoint, writes included.
//
// Environment (all optional in the container the workflow starts):
//   BASE_URL               where the web service listens, default http://localhost:8080
//   ORIGIN                 the Origin header of the sign-in; must be one the api allows,
//                          default BASE_URL
//   DURATION               how long to hold the rates, default 30s
//   TOUR_RPS               multiplies every rate but the sign-in's, default 1
//   LOGIN_PER_MINUTE       sign-ins a minute, default 3; at most one per seeded user
//   CREDENTIALS_FILE       JSON written by seed.py, default /creds/credentials.json
//   TOUR_CREDENTIALS_FILE  JSON written by seed_tour.py, default /creds/tour-credentials.json
//   RESULTS_DIR            where the JSON and Markdown summaries go, default /results
//   NAME                   names the result files (<NAME>.json, <NAME>.md), default tour
import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { TREND_STATS, formatValue, summarize } from './summary.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const ORIGIN = __ENV.ORIGIN || BASE_URL;
const DURATION = __ENV.DURATION || '30s';
const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
const NAME = __ENV.NAME || 'tour';
const SCALE = Number(__ENV.TOUR_RPS || '1');
const LOGIN_PER_MINUTE = Number(__ENV.LOGIN_PER_MINUTE || '3');
const credentials = JSON.parse(open(__ENV.CREDENTIALS_FILE || '/creds/credentials.json'));
const tour = JSON.parse(open(__ENV.TOUR_CREDENTIALS_FILE || '/creds/tour-credentials.json'));

if (!(SCALE > 0)) throw new Error(`TOUR_RPS must be a positive number, got ${__ENV.TOUR_RPS}`);
if (!(Number.isInteger(LOGIN_PER_MINUTE) && LOGIN_PER_MINUTE >= 0)) {
  throw new Error(`LOGIN_PER_MINUTE must be a whole number, got ${__ENV.LOGIN_PER_MINUTE}`);
}
if (LOGIN_PER_MINUTE > tour.users.length) {
  throw new Error(
    `LOGIN_PER_MINUTE ${LOGIN_PER_MINUTE} is more than one sign-in per seeded user a minute ` +
      `(${tour.users.length} users); the sign-in throttle would answer 429`,
  );
}

const P95_LIMIT_MS = 500;
const PROJECT = `/api/v1/projects/${credentials.project_id}`;
const SESSION_COOKIES = ['__Host-spl_session', 'spl_session'];
const SPANS_PER_OTLP = 10;
const SCORES_PER_REQUEST = 10;

const DAY_MS = 24 * 3600 * 1000;
const WINDOW_END = new Date(credentials.data_end);
const windowOf = (days) =>
  `from=${new Date(WINDOW_END.getTime() - days * DAY_MS).toISOString()}` +
  `&to=${WINDOW_END.toISOString()}`;
const DAY = windowOf(1);
const WEEK = windowOf(7);

// Requests a second at TOUR_RPS=1, and the endpoints each scenario calls once per iteration.
const SCENARIOS = {
  trace_detail: { rate: 5, endpoints: ['trace_detail'] },
  big_trace: { rate: 1, endpoints: ['big_trace_detail'] },
  span_detail: { rate: 3, endpoints: ['span_detail', 'cost_explanation'] },
  sessions: { rate: 3, endpoints: ['sessions_list', 'session_turns'] },
  end_users: { rate: 2, endpoints: ['end_users'] },
  attribution: { rate: 2, endpoints: ['attribution', 'attribution_series'] },
  insights: { rate: 2, endpoints: ['insights', 'insights_summary'] },
  datasets: { rate: 2, endpoints: ['datasets', 'dataset_items'] },
  experiments: {
    rate: 2,
    endpoints: ['experiments', 'experiment_runs', 'experiment_compare'],
  },
  evaluators: { rate: 1, endpoints: ['evaluators'] },
  prompts: { rate: 2, endpoints: ['prompts', 'prompt_versions'] },
  annotations: { rate: 2, endpoints: ['annotation_items'] },
  otlp_json: { rate: 2, endpoints: ['otlp_json'], write: true },
  otlp_protobuf: { rate: 2, endpoints: ['otlp_protobuf'], write: true },
  scores: { rate: 2, endpoints: ['scores'], write: true },
};

const scenarios = {};
const thresholds = { http_req_failed: ['rate<0.01'] };
function addScenario(name, rate, timeUnit) {
  scenarios[name] = {
    executor: 'constant-arrival-rate',
    exec: name,
    rate,
    timeUnit,
    duration: DURATION,
    preAllocatedVUs: 5,
    maxVUs: 50,
  };
  const tag = `{scenario:${name}}`;
  thresholds[`http_req_failed${tag}`] = ['rate<0.01'];
  thresholds[`checks${tag}`] = ['rate>0.99'];
  // The rate was held: k6 drops an iteration when it has no free virtual user left.
  thresholds[`dropped_iterations${tag}`] = ['count==0'];
}
for (const [name, scenario] of Object.entries(SCENARIOS)) {
  addScenario(name, Math.max(1, Math.round(scenario.rate * SCALE)), '1s');
  if (!scenario.write) {
    for (const endpoint of scenario.endpoints) {
      thresholds[`http_req_duration{endpoint:${endpoint}}`] = [`p(95)<${P95_LIMIT_MS}`];
    }
  }
}
// A constant-arrival-rate of 0 is not valid, so LOGIN_PER_MINUTE=0 leaves the scenario out.
if (LOGIN_PER_MINUTE > 0) addScenario('login', LOGIN_PER_MINUTE, '1m');

const ENDPOINTS = [
  ...Object.values(SCENARIOS).flatMap((scenario) => scenario.endpoints),
  ...(LOGIN_PER_MINUTE > 0 ? ['login'] : []),
];
// Tags alone do not reach the summary unless a threshold names them, so each endpoint also gets
// a metric of its own, writes and the sign-in included.
const trendByEndpoint = {};
for (const endpoint of ENDPOINTS) trendByEndpoint[endpoint] = new Trend(`tour_${endpoint}`, true);

// A 429 or any other status outside 2xx counts as a failed request, not just a slow one.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }));

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: TREND_STATS,
};

const reportedFailure = {};

function record(endpoint, response, accepted = (r) => r.status === 200) {
  trendByEndpoint[endpoint].add(response.timings.duration);
  const served = check(response, { [`${endpoint} answered`]: accepted });
  if (!served && !reportedFailure[endpoint]) {
    reportedFailure[endpoint] = true; // once per virtual user, so a broken run does not flood the log
    console.error(`${endpoint}: HTTP ${response.status} ${String(response.body).slice(0, 300)}`);
  }
  return served;
}

function read(endpoint, path, data) {
  const response = http.get(`${BASE_URL}${PROJECT}${path}`, {
    headers: { Cookie: data.cookie },
    tags: { name: endpoint, endpoint },
  });
  record(endpoint, response);
  return response;
}

// A fresh cookie jar each time, so a virtual user never sends an earlier session along.
function signIn(user) {
  return http.post(`${BASE_URL}/api/v1/auth/login`, JSON.stringify(user), {
    headers: { 'Content-Type': 'application/json', Origin: ORIGIN },
    jar: new http.CookieJar(),
    tags: { name: 'login', endpoint: 'login' },
  });
}

export function setup() {
  const response = signIn(tour.users[0]);
  for (const name of SESSION_COOKIES) {
    const cookie = response.cookies[name] && response.cookies[name][0];
    if (response.status === 200 && cookie) return { cookie: `${name}=${cookie.value}` };
  }
  throw new Error(`sign-in failed: HTTP ${response.status} ${String(response.body).slice(0, 300)}`);
}

const pick = (list) => list[exec.scenario.iterationInTest % list.length];

export function trace_detail(data) {
  read('trace_detail', `/traces/${pick(tour.traces).trace_id}`, data);
}

export function big_trace(data) {
  read('big_trace_detail', `/traces/${tour.big_trace_id}?span_content=bounded`, data);
}

export function span_detail(data) {
  const { trace_id: traceId, span_id: spanId } = pick(tour.traces);
  read('span_detail', `/traces/${traceId}/spans/${spanId}`, data);
  read('cost_explanation', `/traces/${traceId}/spans/${spanId}/cost-explanation`, data);
}

export function sessions(data) {
  read('sessions_list', `/sessions?${DAY}`, data);
  read('session_turns', `/sessions/${encodeURIComponent(pick(tour.session_ids))}/turns`, data);
}

export function end_users(data) {
  read('end_users', `/users?${WEEK}`, data);
}

export function attribution(data) {
  read('attribution', `/attribution?group_by=trace_name&${WEEK}`, data);
  read('attribution_series', `/attribution/series?group_by=trace_name&${WEEK}`, data);
}

export function insights(data) {
  read('insights', '/insights', data);
  read('insights_summary', '/insights/summary', data);
}

export function datasets(data) {
  read('datasets', '/datasets', data);
  read('dataset_items', `/datasets/${pick(tour.dataset_ids)}/items`, data);
}

export function experiments(data) {
  const experiment = `/experiments/${tour.experiment_id}`;
  const [runA, runB] = tour.run_ids;
  read('experiments', '/experiments', data);
  read('experiment_runs', `${experiment}/runs`, data);
  read('experiment_compare', `${experiment}/runs/${runA}/compare/${runB}`, data);
}

export function evaluators(data) {
  read('evaluators', '/evaluators', data);
}

export function prompts(data) {
  read('prompts', '/prompts', data);
  read('prompt_versions', `/prompts/${encodeURIComponent(pick(tour.prompt_names))}/versions`, data);
}

export function annotations(data) {
  read('annotation_items', `/annotation-queues/${tour.queue_id}/items`, data);
}

export function login() {
  const response = signIn(pick(tour.users));
  record('login', response, (r) => r.status === 200 && r.json('status') === 'signed_in');
}

// ---------- OTLP ----------

// Trace and span ids must be new for every request, or the pipeline would update rows that
// already exist. The run id keeps a second run from repeating the first; the digit after it keeps
// the JSON and protobuf scenarios apart.
const RUN_ID = Date.now().toString(16).padStart(12, '0');
const hex = (value, width) => value.toString(16).padStart(width, '0');

// One trace: a root span and SPANS_PER_OTLP - 1 chat calls under it, 200 ms apart.
function otlpSpans(protobuf) {
  const iteration = exec.scenario.iterationInTest;
  const traceId = `${RUN_ID}${protobuf ? 1 : 0}${hex(iteration, 11)}${hex(__VU, 8)}`;
  const nowMs = Date.now() - 3000;
  const spans = [];
  for (let index = 0; index < SPANS_PER_OTLP; index++) {
    const isRoot = index === 0;
    const startMs = nowMs + index * 200;
    spans.push({
      traceId,
      spanId: `${hex(iteration, 8)}${hex(index + 1, 8)}`,
      parentSpanId: isRoot ? null : `${hex(iteration, 8)}${hex(1, 8)}`,
      name: isRoot ? 'tour agent run' : 'chat gpt-4o-mini',
      kind: isRoot ? 1 : 3, // INTERNAL, CLIENT
      startMs,
      endMs: isRoot ? nowMs + SPANS_PER_OTLP * 200 : startMs + 150,
      strings: isRoot
        ? {}
        : {
            'gen_ai.system': 'openai',
            'gen_ai.operation.name': 'chat',
            'gen_ai.request.model': 'gpt-4o-mini',
          },
      ints: isRoot
        ? {}
        : {
            'gen_ai.usage.input_tokens': 300,
            'gen_ai.usage.output_tokens': 80,
          },
    });
  }
  return spans;
}

const RESOURCE = {
  'service.name': 'tour-load',
  'deployment.environment': 'load-test',
};

function otlpJson(spans) {
  const attributes = (strings, ints) => [
    ...Object.entries(strings).map(([key, value]) => ({
      key,
      value: { stringValue: value },
    })),
    ...Object.entries(ints).map(([key, value]) => ({
      key,
      value: { intValue: String(value) },
    })),
  ];
  return JSON.stringify({
    resourceSpans: [
      {
        resource: { attributes: attributes(RESOURCE, {}) },
        scopeSpans: [
          {
            scope: { name: 'tour' },
            spans: spans.map((span) => ({
              traceId: span.traceId,
              spanId: span.spanId,
              ...(span.parentSpanId ? { parentSpanId: span.parentSpanId } : {}),
              name: span.name,
              kind: span.kind,
              startTimeUnixNano: `${span.startMs}000000`,
              endTimeUnixNano: `${span.endMs}000000`,
              attributes: attributes(span.strings, span.ints),
              status: { code: 1 },
            })),
          },
        ],
      },
    ],
  });
}

// The few protobuf wire types an ExportTraceServiceRequest needs, written by hand so the script
// needs no library. Strings are ASCII only.
function varint(value) {
  const bytes = [];
  let rest = value;
  while (rest >= 128) {
    bytes.push((rest % 128) + 128);
    rest = Math.floor(rest / 128);
  }
  bytes.push(rest);
  return bytes;
}
const key = (field, wireType) => varint(field * 8 + wireType);
const ascii = (text) => Array.from(text, (character) => character.charCodeAt(0));
const idBytes = (id) => id.match(/../g).map((pair) => parseInt(pair, 16));
const lengthDelimited = (field, bytes) => [...key(field, 2), ...varint(bytes.length), ...bytes];
const enumField = (field, value) => [...key(field, 0), ...varint(value)];

// Milliseconds to the little-endian fixed64 of nanoseconds. ms * 1e6 passes 2^53, so it is
// multiplied in 16-bit limbs.
function fixed64Nanos(field, ms) {
  const limbs = [ms % 65536, Math.floor(ms / 65536) % 65536, Math.floor(ms / 4294967296), 0];
  const bytes = [...key(field, 1)];
  let carry = 0;
  for (const limb of limbs) {
    const product = limb * 1e6 + carry;
    const low = product % 65536;
    carry = Math.floor(product / 65536);
    bytes.push(low % 256, Math.floor(low / 256));
  }
  return bytes;
}
// 1 ms is 1 000 000 ns, 0x0F4240: a broken encoder stops the run here, not on the server.
if (fixed64Nanos(7, 1).slice(1).join() !== [0x40, 0x42, 0x0f, 0, 0, 0, 0, 0].join()) {
  throw new Error('fixed64Nanos is broken');
}

function keyValues(field, strings, ints) {
  return [
    ...Object.entries(strings).flatMap(([name, value]) =>
      lengthDelimited(field, [
        ...lengthDelimited(1, ascii(name)),
        ...lengthDelimited(2, lengthDelimited(1, ascii(value))), // AnyValue.string_value
      ]),
    ),
    ...Object.entries(ints).flatMap(([name, value]) =>
      lengthDelimited(field, [
        ...lengthDelimited(1, ascii(name)),
        ...lengthDelimited(2, [...key(3, 0), ...varint(value)]), // AnyValue.int_value
      ]),
    ),
  ];
}

function otlpProtobuf(spans) {
  const encoded = spans.flatMap((span) =>
    lengthDelimited(2, [
      ...lengthDelimited(1, idBytes(span.traceId)),
      ...lengthDelimited(2, idBytes(span.spanId)),
      ...(span.parentSpanId ? lengthDelimited(4, idBytes(span.parentSpanId)) : []),
      ...lengthDelimited(5, ascii(span.name)),
      ...enumField(6, span.kind),
      ...fixed64Nanos(7, span.startMs),
      ...fixed64Nanos(8, span.endMs),
      ...keyValues(9, span.strings, span.ints),
      ...lengthDelimited(15, enumField(3, 1)), // Status.code OK
    ]),
  );
  const scopeSpans = [...lengthDelimited(1, lengthDelimited(1, ascii('tour'))), ...encoded];
  const resourceSpans = [
    ...lengthDelimited(1, keyValues(1, RESOURCE, {})),
    ...lengthDelimited(2, scopeSpans),
  ];
  return new Uint8Array(lengthDelimited(1, resourceSpans)).buffer;
}

function sendOtlp(endpoint, body, contentType, accepted) {
  const response = http.post(`${BASE_URL}/v1/otlp/traces`, body, {
    headers: {
      'Content-Type': contentType,
      Authorization: `Bearer ${credentials.ingest_key}`,
    },
    tags: { name: endpoint, endpoint },
  });
  record(endpoint, response, (r) => r.status === 200 && accepted(r));
}

export function otlp_json() {
  // An empty object means every span was accepted; a partial success names the rejected ones.
  sendOtlp(
    'otlp_json',
    otlpJson(otlpSpans(false)),
    'application/json',
    (r) => !r.json('partialSuccess'),
  );
}

export function otlp_protobuf() {
  // An empty ExportTraceServiceResponse (no partial success) means every span was accepted.
  sendOtlp(
    'otlp_protobuf',
    otlpProtobuf(otlpSpans(true)),
    'application/x-protobuf',
    (r) => !r.body,
  );
}

export function scores() {
  const start = exec.scenario.iterationInTest * SCORES_PER_REQUEST;
  const batch = [];
  for (let index = 0; index < SCORES_PER_REQUEST; index++) {
    const { trace_id: traceId } = tour.traces[(start + index) % tour.traces.length];
    batch.push({
      trace_id: traceId,
      name: 'tour_quality',
      value: ((start + index) % 11) / 10,
    });
  }
  const response = http.post(`${BASE_URL}/v1/scores`, JSON.stringify({ scores: batch }), {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${tour.scores_key}`,
    },
    tags: { name: 'scores', endpoint: 'scores' },
  });
  record('scores', response, (r) => {
    if (r.status !== 200) return false;
    const body = r.json();
    return body.accepted === SCORES_PER_REQUEST && body.rejected.length === 0;
  });
}

// ---------- summary ----------

function endpointRows(data) {
  return ENDPOINTS.map((endpoint) => {
    const metric = data.metrics[`tour_${endpoint}`];
    const values = metric && metric.values ? metric.values : {};
    const cell = (stat) => formatValue(values[stat], metric && metric.contains);
    return `| ${endpoint} | ${cell('med')} | ${cell('p(95)')} | ${cell('p(99)')} | ${cell('max')} |`;
  });
}

export function handleSummary(data) {
  const rates = Object.entries(scenarios)
    .map(([name, scenario]) => `${name} ${scenario.rate}/${scenario.timeUnit}`)
    .join(', ');
  return summarize({
    name: NAME,
    title: `Product tour (${NAME}): TOUR_RPS ${SCALE}`,
    description: `Every scenario in parallel for ${DURATION}: ${rates}.`,
    data,
    resultsDir: RESULTS_DIR,
    extraLines: [
      '| Endpoint | Median | p95 | p99 | Max |',
      '|---|---|---|---|---|',
      ...endpointRows(data),
      '',
    ],
  });
}
