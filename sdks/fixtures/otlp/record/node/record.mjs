/**
 * Record the Vercel AI SDK OTLP fixtures (spec §8.1, §8.2).
 *
 *   node record.mjs --version <semver>|latest --mode genai|legacy [--out <dir>] [--check]
 *
 * Installs `ai` at the requested version with the matching `@ai-sdk/openai` (ruling P14), the
 * `@ai-sdk/otel` of the same release (7 only) and the pinned OTel SDK into a temporary directory
 * (`npm install --ignore-scripts`, never into this project), starts `fake_provider.py` and
 * `capture.py`, runs each scenario of the version's recipe as its own Node process, and hands the
 * captured exports to `record.py --from-capture`, so the fixture format has one writer. The only
 * network use is the npm install; no provider key is read.
 *
 * Without `--check` the fixture is written to `<out>/vercel-ai-<mode>/<version>.json` (`--out`
 * defaults to `sdks/fixtures/otlp`). With `--check` nothing is written unless `--out` is given
 * (the weekly job keeps the fresh file for its contract run); `expected` is diffed against the
 * committed fixture of the same version, else the highest committed one of the same major, one
 * `scenarios[...]` line per difference, exit 1 on any.
 */
import { execFileSync, spawn, spawnSync } from "node:child_process";
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { parseArgs } from "node:util";

const HERE = import.meta.dirname;
const RECORD = join(HERE, "..");
const FIXTURES = join(RECORD, "..");
const OTEL_SDK = "2.12.0";
const OTEL_PACKAGES = {
  "@opentelemetry/api": "1.9.1",
  "@opentelemetry/sdk-trace-node": OTEL_SDK,
  "@opentelemetry/resources": OTEL_SDK,
  "@opentelemetry/exporter-trace-otlp-http": "0.223.0",
};
// Ruling P14: `@ai-sdk/openai` matching each `ai` major; another major has no pin and stops.
const AI_SDK_OPENAI = { 5: "2.0.135", 6: "3.0.130", 7: "4.0.96" };
const ZOD = "4.1.13";
const SCENARIOS = [
  "generate_text_with_metadata",
  "stream_text",
  "tool_call",
  "generate_object",
  "error",
];
const SCENARIO_TIMEOUT_MS = 120_000;
const SEMVER = /^\d+\.\d+\.\d+$/;
/**
 * Native fields the exporter does not send, per scenario (the compatibility page prints them).
 * No path sends the 1-hour cache-write split; only a stream has a first chunk. `ai` 5 and 6 have
 * no cache-write usage, and their `generateText` / `generateObject` spans carry no cached tokens
 * (only `streamText` writes `ai.usage.cachedInputTokens`). The GenAI recipe does not turn on
 * `runtimeContext`, so metadata is not sent.
 */
function losesFor(major, mode) {
  const writes =
    major < 7 ? ["cache_write_tokens", "cache_write_1h_tokens"] : ["cache_write_1h_tokens"];
  const call = [...(major < 7 ? ["cached_tokens"] : []), ...writes, "time_to_first_token_ms"];
  return {
    generate_text_with_metadata: [...call, ...(mode === "genai" ? ["metadata"] : [])],
    stream_text: writes,
    tool_call: call,
    generate_object: call,
    error: ["output", "usage"],
  };
}

function usage(message) {
  console.error(
    `${message}\nusage: node record.mjs --version <semver>|latest --mode genai|legacy [--out <dir>] [--check]`,
  );
  process.exit(2);
}

function parse() {
  const { values } = parseArgs({
    options: {
      version: { type: "string" },
      mode: { type: "string" },
      out: { type: "string" },
      check: { type: "boolean", default: false },
    },
  });
  if (values.mode !== "genai" && values.mode !== "legacy") usage("--mode must be genai or legacy");
  if (values.version !== "latest" && !SEMVER.test(values.version ?? "")) {
    usage("--version must be x.y.z or latest");
  }
  return {
    version: String(values.version),
    mode: values.mode,
    out: values.out,
    check: values.check,
  };
}

function npmView(spec) {
  return execFileSync("npm", ["view", spec, "version"], { encoding: "utf8" }).trim();
}

/** The npm install list and the recipe file for one `ai` version and mode. */
function plan(version, mode) {
  const [major, minor, patch] = version.split(".").map(Number);
  const openai = AI_SDK_OPENAI[major];
  if (openai === undefined) usage(`no @ai-sdk/openai pin for ai ${major} (ruling P14)`);
  if (mode === "genai" && major < 7) usage("--mode genai needs ai 7 or later");
  const packages = { ai: version, "@ai-sdk/openai": openai, zod: ZOD, ...OTEL_PACKAGES };
  if (major >= 7) {
    // shortcut: `@ai-sdk/otel` 1.x ships in lockstep with `ai` 7.x (1.0.142 requires ai 7.0.142);
    // re-check this mapping when `ai` 8 ships.
    packages["@ai-sdk/otel"] = `1.${minor}.${patch}`;
  }
  const recipe = major === 5 ? "v5.ts" : major === 6 ? "v6.ts" : `v7-${mode}.ts`;
  return { packages, recipe };
}

function install(work, packages) {
  writeFileSync(join(work, "package.json"), JSON.stringify({ private: true, type: "module" }));
  const specs = Object.entries(packages).map(([name, pin]) => `${name}@${pin}`);
  execFileSync(
    "npm",
    ["install", "--ignore-scripts", "--no-audit", "--no-fund", "--save-exact", ...specs],
    {
      cwd: work,
      stdio: ["ignore", "ignore", "inherit"],
    },
  );
}

/** Start one harness server by file path; resolves with it and the port it printed. */
function startServer(script, args) {
  const child = spawn(
    "uv",
    ["run", "--project", RECORD, "--frozen", "python", join(RECORD, script), ...args],
    {
      stdio: ["ignore", "pipe", "inherit"],
    },
  );
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) =>
      reject(new Error(`${script} exited with ${code} before printing its port`)),
    );
    createInterface({ input: child.stdout }).once("line", (line) => {
      resolve({ child, url: `http://127.0.0.1:${line.trim()}` });
    });
  });
}

/** Run every scenario against the servers; one export each lands in `<capture>/<scenario>/`. */
async function captureScenarios(work, recipe) {
  const raw = join(work, "raw");
  const capture = join(work, "capture");
  const servers = [];
  try {
    const provider = await startServer("fake_provider.py", []);
    servers.push(provider.child);
    const sink = await startServer("capture.py", ["--out", raw]);
    servers.push(sink.child);
    const env = {
      ...process.env,
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: `${sink.url}/v1/otlp/traces`,
      OPENAI_BASE_URL: `${provider.url}/v1`,
      // Read by recipe.ts (the GenAI recipe); the capture server ignores the key.
      SPANLIGHT_HOST: sink.url,
      SPANLIGHT_API_KEY: "spl_fixture",
      RELEASE: "1.0.0",
      NODE_ENV: "production",
    };
    for (const scenario of SCENARIOS) {
      const result = spawnSync(process.execPath, [join(work, "recipes", recipe)], {
        env: { ...env, X_FIXTURE_SCENARIO: scenario },
        stdio: "inherit",
        timeout: SCENARIO_TIMEOUT_MS,
      });
      if (result.status !== 0)
        throw new Error(`${scenario}: the recipe exited with ${result.status ?? result.signal}`);
      const files = readdirSync(raw);
      if (files.length !== 1)
        throw new Error(`${scenario}: expected one OTLP export, the capture got ${files.length}`);
      mkdirSync(join(capture, scenario), { recursive: true });
      renameSync(join(raw, files[0]), join(capture, scenario, `0001${files[0].slice(4)}`));
    }
  } finally {
    for (const child of servers) child.kill("SIGTERM");
  }
  return capture;
}

/** The committed version `--check` diffs against: the same version, else the newest of its major. */
function committedVersion(library, version) {
  const files = readdirSync(join(FIXTURES, library)).filter((name) =>
    SEMVER.test(name.replace(/\.json$/, "")),
  );
  const versions = files.map((name) => name.replace(/\.json$/, ""));
  if (versions.includes(version)) return version;
  const major = version.split(".")[0];
  const sameMajor = versions.filter((v) => v.split(".")[0] === major);
  if (sameMajor.length === 0) usage(`no committed ${library} fixture for ai ${major}`);
  return sameMajor.sort((a, b) => a.localeCompare(b, "en", { numeric: true })).at(-1);
}

function recordPy(args) {
  const result = spawnSync(
    "uv",
    ["run", "--project", RECORD, "--frozen", "python", join(RECORD, "record.py"), ...args],
    {
      cwd: RECORD,
      stdio: "inherit",
    },
  );
  return result.status ?? 1;
}

async function main() {
  const args = parse();
  // Error spans carry stack traces with install paths: keep the per-user macOS temp dir
  // (`/var/folders/<hash>/T/`) out of the fixtures. Inherited by npm, the recipes and record.py.
  process.env.TMPDIR = "/tmp";
  const version = args.version === "latest" ? npmView("ai@latest") : args.version;
  const library = `vercel-ai-${args.mode}`;
  const { packages, recipe } = plan(version, args.mode);
  const work = mkdtempSync(join(tmpdir(), "spanlight-record-node-"));
  try {
    install(work, packages);
    cpSync(join(HERE, "recipes"), join(work, "recipes"), { recursive: true });
    const capture = await captureScenarios(work, recipe);
    const recorder = {
      runtime: `node ${process.versions.node}`,
      otel_sdk: OTEL_SDK,
      loses: losesFor(Number(version.split(".")[0]), args.mode),
    };
    writeFileSync(join(capture, "recorder.json"), JSON.stringify(recorder));
    const common = ["--from-capture", capture, "--library", library];
    if (!args.check || args.out !== undefined) {
      const status = recordPy([...common, "--version", version, "--out", args.out ?? FIXTURES]);
      if (status !== 0 || !args.check) return status;
    }
    return recordPy([...common, "--version", committedVersion(library, version), "--check"]);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

process.exitCode = await main();
