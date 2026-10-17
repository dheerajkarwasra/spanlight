import type { Page } from "@playwright/test";

import {
  DB_SKIP_REASON,
  dbEnabled,
  deleteIncident,
  insertIncident,
  type SeededIncident,
} from "./db-seed";
import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import {
  checkAtWidths,
  createWorkspace,
  expect,
  sendTrace,
  sendTraceSpans,
  test,
  type SpanInput,
} from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

/**
 * A status page incident title that would run script if it were ever rendered as HTML. The page
 * must show it as text: no element is created and the flag stays unset.
 */
const HOSTILE_TITLE =
  '<script>window.__e2eXss = 1</script><img src=x onerror="window.__e2eXss = 2"> E2E elevated errors';

/** An Anthropic 429 whose message names the monthly usage limit: a spend cap. */
const ANTHROPIC_SPEND_CAP: SpanInput = {
  name: "spend-capped call",
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  status: "error",
  usage: { input_tokens: 0, output_tokens: 0 },
  statusMessage:
    "You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC.",
  attributes: {
    "http.status_code": 429,
    "provider.error.type": "rate_limit_error",
    "provider.request_id": "req_e2e_spend_cap",
    "provider.ratelimit": { "anthropic-ratelimit-requests-remaining": "41" },
  },
};

const RETRIED_INPUT = [{ role: "user", content: "Summarise the incident report" }];

/** An OpenAI 429 on tokens per minute, then the same request again, which succeeds: one retry. */
function openAiTokensRetry(): SpanInput[] {
  const now = Date.now();
  return [
    {
      name: "chat attempt 1",
      traceName: "tokens-retry",
      provider: "openai",
      model: "gpt-4o-mini",
      status: "error",
      endTime: new Date(now - 3_000),
      input: RETRIED_INPUT,
      usage: { input_tokens: 0, output_tokens: 0 },
      statusMessage:
        "Rate limit reached for gpt-4o-mini in organization org-e2e on tokens per min (TPM): Limit 200000, Used 199990, Requested 120.",
      attributes: {
        "http.status_code": 429,
        "provider.error.type": "tokens",
        "provider.error.code": "rate_limit_exceeded",
        "provider.request_id": "req_e2e_tokens",
        "provider.retry_after_s": 2,
        "provider.ratelimit": { "x-ratelimit-remaining-tokens": "0" },
      },
    },
    {
      name: "chat attempt 2",
      provider: "openai",
      model: "gpt-4o-mini",
      endTime: new Date(now),
      input: RETRIED_INPUT,
    },
  ];
}

async function seedFailures(page: Page, apiKey: string): Promise<{ retryTrace: string }> {
  await sendTrace(page, apiKey, ANTHROPIC_SPEND_CAP);
  await sendTrace(page, apiKey, {
    name: "healthy call",
    provider: "anthropic",
    model: "claude-sonnet-4-5",
  });
  const retryTrace = await sendTraceSpans(page, apiKey, openAiTokensRetry());
  return { retryTrace };
}

async function hostileFlag(page: Page): Promise<unknown> {
  return page.evaluate(() => (window as unknown as { __e2eXss?: unknown }).__e2eXss);
}

test.describe("provider path", () => {
  // The incident row is global (every project on the stack sees it), so it lives only while this
  // file runs.
  let incident: SeededIncident | null = null;
  test.beforeAll(() => {
    if (e2eEnabled && dbEnabled) {
      incident = insertIncident(HOSTILE_TITLE);
    }
  });
  test.afterAll(() => {
    if (incident) {
      deleteIncident(incident.id);
    }
  });

  test("provider health: seeded failures, table toggle, escaped incident title", async ({
    page,
  }) => {
    test.skip(!dbEnabled, DB_SKIP_REASON);
    const { base, apiKey } = await createWorkspace(page, { apiKey: true });
    await seedFailures(page, apiKey ?? "");

    await page.goto(`${base}/providers`);
    await expect(page.getByRole("heading", { level: 1, name: "Provider health" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Anthropic" })).toBeVisible();
    await expect(page.getByRole("region", { name: "OpenAI" })).toBeVisible();
    const kinds = page.getByRole("region", { name: "Rate-limit kinds" });
    await expect(kinds.getByRole("table")).toContainText("Spend cap");
    await expect(kinds.getByRole("table")).toContainText("Token limit");

    // The stacked chart, then its table; the choice survives a reload.
    const errors = page.getByRole("region", { name: "Errors by class" });
    await expect(errors.getByRole("list", { name: "Error classes" })).toContainText("Rate limited");
    await errors.getByRole("button", { name: "Show table" }).click();
    await expect(errors.getByRole("table")).toContainText("Rate limited");
    await page.reload();
    await expect(errors.getByRole("button", { name: "Show chart" })).toBeVisible();
    await expect(errors.getByRole("table")).toBeVisible();
    await checkAtWidths(page, "providers-table", async () => {
      await expect(errors.getByRole("table")).toBeVisible();
    });
    await errors.getByRole("button", { name: "Show chart" }).click();
    await expect(errors.getByRole("button", { name: "Show table" })).toBeVisible();

    // The status page title is text, never markup.
    const incidents = page.getByRole("region", { name: "Provider incidents" });
    await expect(incidents.getByText(HOSTILE_TITLE, { exact: true })).toBeVisible();
    await expect(incidents.locator("script, img")).toHaveCount(0);
    expect(await hostileFlag(page)).toBeUndefined();

    await checkAtWidths(page, "providers", async () => {
      await expect(errors.getByRole("list", { name: "Error classes" })).toBeVisible();
    });

    // The dark theme, at the laptop width.
    await page.evaluate(() => {
      window.localStorage.setItem("spanlight-theme", "dark");
    });
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(errors.getByRole("list", { name: "Error classes" })).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("overview: the incident overlay switch persists across a reload", async ({ page }) => {
    const { base, apiKey } = await createWorkspace(page, { apiKey: true });
    await seedFailures(page, apiKey ?? "");

    await page.goto(`${base}/overview`);
    const overlay = page.getByRole("switch", { name: "Show incidents" });
    await expect(overlay).toBeChecked();
    await checkAtWidths(page, "overview", async () => {
      await expect(overlay).toBeVisible();
    });
    await overlay.click();
    await expect(overlay).not.toBeChecked();
    await page.reload();
    await expect(overlay).not.toBeChecked();
    await overlay.click();
    await expect(overlay).toBeChecked();
    await page.reload();
    await expect(overlay).toBeChecked();
    expect(await hostileFlag(page)).toBeUndefined();
  });

  test("provider health: the polling-off copy", async ({ page }) => {
    const { base } = await createWorkspace(page);
    // The page's own incidents request says whether this server polls the feeds.
    const incidentsResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/provider-incidents") && response.request().method() === "GET",
    );
    await page.goto(`${base}/providers`);
    const response = await incidentsResponse;
    expect(response.ok(), await response.text()).toBe(true);
    const { polling } = (await response.json()) as { polling: "enabled" | "disabled" };
    test.skip(
      polling === "enabled",
      "This stack polls the status feeds; run it with PROVIDER_STATUS_POLLING_ENABLED=false to check the polling-off copy.",
    );

    const incidents = page.getByRole("region", { name: "Provider incidents" });
    await expect(incidents).toContainText("Provider status feeds are off on this server");
    await expect(incidents).toContainText("PROVIDER_STATUS_POLLING_ENABLED=false");
    await checkAtWidths(page, "providers-polling-off");
  });

  test("traces: Retries column and the span's provider error block", async ({ page }) => {
    const { base, apiKey } = await createWorkspace(page, { apiKey: true });
    const { retryTrace } = await seedFailures(page, apiKey ?? "");

    await page.goto(`${base}/traces`);
    await expect(page.getByText("Retries", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /^1 retry · / })).toBeVisible();
    await checkAtWidths(page, "traces", async () => {
      await expect(page.getByRole("link", { name: "tokens-retry" }).first()).toBeVisible();
    });

    await page.goto(`${base}/traces/${retryTrace}`);
    await page.getByRole("treeitem", { name: /chat attempt 1/ }).click();
    const block = page.getByRole("region", { name: "Provider error" });
    await expect(block).toBeVisible();
    await expect(block).toContainText("Rate limited");
    await expect(block).toContainText("Token limit");
    await expect(block).toContainText("req_e2e_tokens");
    await expect(block).toContainText("rate_limit_exceeded");
    await checkAtWidths(page, "trace-provider-error", async () => {
      await expect(block).toBeVisible();
    });
  });

  test("alerts: Error class and Rate-limit kind on an error-rate rule", async ({ page }) => {
    const { base } = await createWorkspace(page);

    await page.goto(`${base}/alerts`);
    await page.getByRole("button", { name: "Create rule" }).first().click();
    const dialog = page.getByRole("dialog", { name: "New alert rule" });
    await dialog.getByLabel("Name").fill("Spend cap refusals");
    const errorClass = dialog.getByRole("combobox", { name: "Error class" });
    const kind = dialog.getByRole("combobox", { name: "Rate-limit kind" });
    await expect(errorClass).toBeVisible();
    await expect(kind).toBeVisible();
    await errorClass.click();
    await page.getByRole("option", { name: "Rate limited" }).click();
    await kind.click();
    await page.getByRole("option", { name: /^Spend cap/ }).click();
    await expect(errorClass).toContainText("Rate limited");
    await expect(kind).toContainText("Spend cap");
    await dialog.getByLabel(/^Threshold/).fill("5");
    await checkAtWidths(page, "alert-editor", async () => {
      // On a phone the dialog scrolls: bring the error filters into the screenshot.
      await kind.scrollIntoViewIfNeeded();
      await expect(kind).toBeVisible();
    });

    // A metric without error filters drops both selects.
    await dialog.getByRole("combobox", { name: "Metric" }).click();
    await page.getByRole("option", { name: "p95 latency" }).click();
    await expect(errorClass).toBeHidden();
    await expect(kind).toBeHidden();
    await dialog.getByRole("combobox", { name: "Metric" }).click();
    await page.getByRole("option", { name: "Error rate" }).click();
    await expect(errorClass).toBeVisible();

    // Saved with both filters; the rule's settings show them.
    await errorClass.click();
    await page.getByRole("option", { name: "Rate limited" }).click();
    await kind.click();
    await page.getByRole("option", { name: /^Spend cap/ }).click();
    await dialog.getByLabel(/^Threshold/).fill("5");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog).toBeHidden();
    await page
      .getByRole("list", { name: "Alert rules" })
      .getByRole("link", { name: /Spend cap refusals/ })
      .click();
    await expect(page.getByRole("heading", { level: 1, name: "Spend cap refusals" })).toBeVisible();
    await expect(page.getByText("Error class", { exact: true })).toBeVisible();
    await expect(page.getByText("Rate limited", { exact: true })).toBeVisible();
    await expect(page.getByText("Rate-limit kind", { exact: true })).toBeVisible();
    await expect(page.getByText("Spend cap", { exact: true })).toBeVisible();
  });
});
