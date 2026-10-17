import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";

import type { Page } from "@playwright/test";

import {
  expect as baseExpect,
  test as baseTest,
  expectNoAxeViolations,
  uniqueEmail,
} from "./fixtures";

/*
 * Shared steps for the feature specs. The API allows 10 sign-ups per IP address per hour, so each
 * worker signs up once and every test then creates its own organization and project in that
 * account: each test still owns its data on a shared stack. `sendTrace` sends a real trace
 * through the ingestion API.
 *
 * `E2E_ACCOUNT_STATE` names a file (outside the repository) that keeps the signed-in session
 * between runs: the first run signs up and saves it there, later runs reuse it, so reruns and
 * worker restarts after a failure do not spend the sign-up allowance.
 */

/**
 * Assertions for the feature specs. Each page fetches its data after it renders, and these specs
 * run on a shared stack, so a first load may take longer than the 5 s default.
 */
export const expect = baseExpect.configure({ timeout: 15_000 });

const SAVED_ACCOUNT_STATE = process.env.E2E_ACCOUNT_STATE;

/** `test` signed in as one fresh account per worker (its saved session is the storage state). */
export const test = baseTest.extend<object, { accountState: string }>({
  accountState: [
    async ({ browser }, provide, workerInfo) => {
      if (SAVED_ACCOUNT_STATE && existsSync(SAVED_ACCOUNT_STATE)) {
        await provide(SAVED_ACCOUNT_STATE);
        return;
      }
      const context = await browser.newContext({ baseURL: workerInfo.project.use.baseURL });
      const page = await context.newPage();
      await page.goto("/signup");
      await page.getByLabel("Name").fill("E2E User");
      await page.getByLabel("Work email").fill(uniqueEmail());
      await page.getByLabel("Password").fill("correct-horse-battery");
      await page.getByRole("button", { name: "Create account" }).click();
      await expect(page).toHaveURL(/\/onboarding/);
      const path =
        SAVED_ACCOUNT_STATE ??
        workerInfo.project.outputDir + `/account-${String(workerInfo.workerIndex)}.json`;
      await context.storageState({ path });
      await context.close();
      await provide(path);
    },
    { scope: "worker", timeout: 120_000 },
  ],
  storageState: async ({ accountState }, provide) => {
    await provide(accountState);
  },
});

const SECRET_PATTERN = /spl_live_[A-Za-z0-9]+_[A-Za-z0-9]+/;

export interface Workspace {
  /** `/<org>/<project>`: prefix every project page with it. */
  base: string;
  /** An ingestion API key, when the workspace was created with one. */
  apiKey: string | null;
}

/** Creates a new organization and project for this test and lands on the project's overview. */
export async function createWorkspace(
  page: Page,
  options: { apiKey?: boolean } = {},
): Promise<Workspace> {
  await page.goto("/onboarding");
  await page.getByLabel("Organization name").fill(`E2E Org ${randomBytes(3).toString("hex")}`);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Project name").fill("E2E Project");
  await page.getByRole("button", { name: "Create project" }).click();

  let apiKey: string | null = null;
  if (options.apiKey) {
    await page.getByRole("button", { name: "Create API key" }).click();
    await expect(page.getByText("This key is shown once.", { exact: false })).toBeVisible();
    apiKey = SECRET_PATTERN.exec(await page.locator("main").innerText())?.[0] ?? null;
    expect(apiKey, "the new API key secret is displayed").toBeTruthy();
  }

  await page.getByRole("link", { name: "Skip to dashboard" }).click();
  await expect(page).toHaveURL(/\/overview$/);
  const base = new URL(page.url()).pathname.replace(/\/overview$/, "");
  return { base, apiKey };
}

/** The span's usage as `POST /v1/traces` takes it; `input_tokens` includes cached tokens. */
export interface UsageInput {
  input_tokens: number;
  output_tokens: number;
  service_tier?: "standard" | "batch" | "priority" | "flex";
  server_tool_use?: { web_search_requests: number };
}

export interface SpanInput {
  name?: string;
  /** The trace's name when it differs from the span's (read from a trace's first span). */
  traceName?: string;
  provider?: string;
  model?: string;
  usage?: UsageInput;
  /** When the call ended; now by default. It started 1.2 s earlier. */
  endTime?: Date;
  status?: "ok" | "error";
  release?: string;
  userId?: string;
  environment?: string;
  /** Trace metadata (`trace.metadata`), read from a trace's first span. */
  metadata?: Record<string, string>;
  input?: unknown;
  output?: unknown;
  /** The error message of a failed span (`status_message`). */
  statusMessage?: string;
  /** Span attributes, e.g. `http.status_code` and the `provider.*` keys of a failed call. */
  attributes?: Record<string, unknown>;
}

/** Sends one LLM span (its own trace) through `POST /v1/traces`; returns the trace id. */
export async function sendTrace(page: Page, apiKey: string, span: SpanInput = {}): Promise<string> {
  return sendTraceSpans(page, apiKey, [span]);
}

/**
 * Sends several LLM spans as one trace through `POST /v1/traces`, in the order given, each a
 * sibling under no parent; returns the trace id. The trace fields come from the first span.
 */
export async function sendTraceSpans(
  page: Page,
  apiKey: string,
  spans: readonly SpanInput[],
): Promise<string> {
  const traceId = randomBytes(16).toString("hex");
  const response = await page.request.post("/v1/traces", {
    headers: { Authorization: `Bearer ${apiKey}` },
    data: { spans: spans.map((span) => spanBody(traceId, span, spans[0] ?? span)) },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return traceId;
}

function spanBody(traceId: string, span: SpanInput, first: SpanInput): Record<string, unknown> {
  const end = span.endTime?.getTime() ?? Date.now();
  const trace: Record<string, unknown> = {
    name: first.traceName ?? first.name ?? "e2e-trace",
    environment: first.environment ?? "e2e",
  };
  if (first.release !== undefined) trace.release = first.release;
  if (first.userId !== undefined) trace.user_id = first.userId;
  if (first.metadata !== undefined) trace.metadata = first.metadata;
  const body: Record<string, unknown> = {
    trace_id: traceId,
    span_id: randomBytes(8).toString("hex"),
    parent_span_id: null,
    name: span.name ?? "e2e chat",
    kind: "llm",
    status: span.status ?? "ok",
    start_time: new Date(end - 1200).toISOString(),
    end_time: new Date(end).toISOString(),
    provider: span.provider ?? "openai",
    model: span.model ?? "gpt-4o-mini",
    usage: span.usage ?? { input_tokens: 12, output_tokens: 40 },
    input: span.input ?? [{ role: "user", content: "Hello from Playwright" }],
    output: span.output ?? { role: "assistant", content: "Hello back" },
    trace,
  };
  if (span.statusMessage !== undefined) body.status_message = span.statusMessage;
  if (span.attributes !== undefined) body.attributes = span.attributes;
  return body;
}

const SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR;

/** The widths every page is checked at: a laptop and the 375 px minimum. */
export const WIDTHS = [1280, 375] as const;

/**
 * At each of `WIDTHS`: waits for `ready` (what must be on screen at that width), runs axe, and,
 * when `E2E_SCREENSHOT_DIR` is set, saves a full-page screenshot as `<name>-<width>.png` for a
 * visual check. Leaves the page at 1280 px.
 */
export async function checkAtWidths(
  page: Page,
  name: string,
  ready: () => Promise<void> = async () => {},
): Promise<void> {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await ready();
    await expectNoAxeViolations(page);
    if (SCREENSHOT_DIR) {
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/${name}-${String(width)}.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  await page.setViewportSize({ width: WIDTHS[0], height: 900 });
}
