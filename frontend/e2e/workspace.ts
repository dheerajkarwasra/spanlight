import { randomBytes } from "node:crypto";

import type { Page } from "@playwright/test";

import { expect as baseExpect, test as baseTest, uniqueEmail } from "./fixtures";

/*
 * Shared steps for the feature specs. The API allows 10 sign-ups per IP address per hour, so each
 * worker signs up once and every test then creates its own organization and project in that
 * account: each test still owns its data on a shared stack. `sendTrace` sends a real trace
 * through the ingestion API.
 */

/**
 * Assertions for the feature specs. Each page fetches its data after it renders, and these specs
 * run on a shared stack, so a first load may take longer than the 5 s default.
 */
export const expect = baseExpect.configure({ timeout: 15_000 });

/** `test` signed in as one fresh account per worker (its saved session is the storage state). */
export const test = baseTest.extend<object, { accountState: string }>({
  accountState: [
    async ({ browser }, provide, workerInfo) => {
      const context = await browser.newContext({ baseURL: workerInfo.project.use.baseURL });
      const page = await context.newPage();
      await page.goto("/signup");
      await page.getByLabel("Name").fill("E2E User");
      await page.getByLabel("Work email").fill(uniqueEmail());
      await page.getByLabel("Password").fill("correct-horse-battery");
      await page.getByRole("button", { name: "Create account" }).click();
      await expect(page).toHaveURL(/\/onboarding/);
      const path = workerInfo.project.outputDir + `/account-${String(workerInfo.workerIndex)}.json`;
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

export interface SpanInput {
  name?: string;
  model?: string;
  status?: "ok" | "error";
  release?: string;
  userId?: string;
  environment?: string;
  input?: unknown;
  output?: unknown;
}

/** Sends one LLM span (its own trace) through `POST /v1/traces`; returns the trace id. */
export async function sendTrace(page: Page, apiKey: string, span: SpanInput = {}): Promise<string> {
  const traceId = randomBytes(16).toString("hex");
  const now = Date.now();
  const trace: Record<string, unknown> = {
    name: span.name ?? "e2e-trace",
    environment: span.environment ?? "e2e",
  };
  if (span.release !== undefined) trace.release = span.release;
  if (span.userId !== undefined) trace.user_id = span.userId;
  const response = await page.request.post("/v1/traces", {
    headers: { Authorization: `Bearer ${apiKey}` },
    data: {
      spans: [
        {
          trace_id: traceId,
          span_id: randomBytes(8).toString("hex"),
          parent_span_id: null,
          name: span.name ?? "e2e chat",
          kind: "llm",
          status: span.status ?? "ok",
          start_time: new Date(now - 1200).toISOString(),
          end_time: new Date(now).toISOString(),
          provider: "openai",
          model: span.model ?? "gpt-4o-mini",
          usage: { input_tokens: 12, output_tokens: 40 },
          input: span.input ?? [{ role: "user", content: "Hello from Playwright" }],
          output: span.output ?? { role: "assistant", content: "Hello back" },
          trace,
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return traceId;
}
