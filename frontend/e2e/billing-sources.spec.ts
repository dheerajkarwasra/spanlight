import { readFileSync } from "node:fs";

import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

const DAY_MS = 86_400_000;
const SOURCE_NAME = "Prod CSV e2e";
const SHORT_DAY = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** The UTC day `days` before today, as YYYY-MM-DD. */
function daysAgo(days: number): string {
  const today = Math.floor(Date.now() / DAY_MS) * DAY_MS;
  return new Date(today - days * DAY_MS).toISOString().slice(0, 10);
}

/**
 * The CSV template (a copy of the backend's reconciliation fixture) moved onto two recent days.
 * Both end at least 72 h before now, a full day past the 48 h a day takes to settle, so the upload
 * run settles them even when the test's clock runs ahead of the server's just after midnight UTC.
 * Both sit inside the report's default window (the 14 whole days before today).
 */
function templateCsv(first: string, second: string): string {
  return readFileSync(new URL("./fixtures/cost-template.csv", import.meta.url), "utf8")
    .replaceAll("2026-10-01", first)
    .replaceAll("2026-10-02", second);
}

// No provider is called: the bill is the uploaded file, and Spanlight's side is two spans sent
// with exactly the token usage the file bills. Claude Sonnet 4.5 is $3 / $15 per million tokens,
// so 411,500 input tokens are $1.2345 and 50,000 output tokens $0.75. The spans record no web
// search, so the file's $0.03 web search fee is the one explained gap: both days reconcile to a
// zero residual.
test("CSV source → upload → settled day with zero residual → CSV download", async ({ page }) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  const [first, second] = [daysAgo(5), daysAgo(4)];
  await sendTrace(page, apiKey ?? "", {
    name: "billed-input",
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    usage: {
      input_tokens: 411_500,
      output_tokens: 0,
      service_tier: "standard",
    },
    endTime: new Date(`${first}T12:00:00Z`),
  });
  await sendTrace(page, apiKey ?? "", {
    name: "billed-output",
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    usage: { input_tokens: 0, output_tokens: 50_000, service_tier: "standard" },
    endTime: new Date(`${second}T12:00:00Z`),
  });

  // 1. The empty list, then a CSV source mapped to this project.
  await page.goto(`${base}/settings/billing-sources`);
  await expect(page.getByRole("heading", { name: "No billing sources" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Add source" }).first().click();
  const createDialog = page.getByRole("dialog", { name: "Add billing source" });
  await createDialog.getByRole("radio", { name: "CSV upload" }).click();
  await createDialog.getByLabel("Name").fill(SOURCE_NAME);
  await createDialog.getByRole("combobox", { name: "Provider" }).click();
  await page.getByRole("option", { name: "Anthropic" }).click();
  await createDialog.getByRole("checkbox", { name: "E2E Project" }).click();
  await expect(createDialog.getByRole("checkbox", { name: "E2E Project" })).toBeChecked();
  await expectNoAxeViolations(page);
  await createDialog.getByRole("button", { name: "Add source" }).click();
  await expect(createDialog).toBeHidden();
  const sources = page.getByRole("list", { name: "Billing sources" });
  await expect(sources).toContainText(SOURCE_NAME);
  await expectNoAxeViolations(page);

  // 2. The template, uploaded as one run.
  await page.getByRole("button", { name: `Upload a CSV to ${SOURCE_NAME}` }).click();
  const uploadDialog = page.getByRole("dialog", { name: "Upload cost CSV" });
  await uploadDialog.getByLabel("Choose a CSV file").setInputFiles({
    name: "cost-template.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(templateCsv(first, second)),
  });
  await expect(uploadDialog.getByText("cost-template.csv")).toBeVisible();
  await expectNoAxeViolations(page);
  await uploadDialog.getByRole("button", { name: "Upload" }).click();
  await expect(uploadDialog).toBeHidden();
  await expect(
    page.getByText(`Imported 3 lines covering 2 days into ${SOURCE_NAME}.`),
  ).toBeVisible();

  // 3. The reconciliation report: both days settled, nothing left unexplained.
  await sources.getByRole("link", { name: SOURCE_NAME }).click();
  await expect(page.getByRole("heading", { level: 1, name: SOURCE_NAME })).toBeVisible();
  const summary = page.getByRole("region", { name: "Summary" });
  await expect(
    summary.getByText("Settled days", { exact: true }).locator("xpath=../following-sibling::p[1]"),
  ).toHaveText("2 of 2");
  const table = page.getByRole("table");
  await expect(table.getByRole("row")).toHaveCount(3);
  // Figures: provider, Spanlight, explained, residual (provider − Spanlight − explained).
  for (const [day, figures] of [
    [second, ["$0.75", "$0.75", "$0.00", "$0.00"]],
    [first, ["$1.26", "$1.23", "$0.03", "$0.00"]],
  ] as const) {
    const row = table.getByRole("row", {
      name: new RegExp(`^${SHORT_DAY.format(new Date(`${day}T00:00:00Z`))}\\b`),
    });
    const cells = row.getByRole("cell");
    for (const [index, figure] of figures.entries()) {
      await expect(cells.nth(index + 1)).toHaveText(figure);
    }
    await expect(row).not.toContainText("Provisional");
  }
  await page
    .getByRole("button", {
      name: `Show model groups for ${SHORT_DAY.format(new Date(`${second}T00:00:00Z`))}`,
    })
    .click();
  await expect(page.getByText(/^Settled · fetched /)).toBeVisible();
  await expectNoAxeViolations(page);

  // 4. The CSV export of the same window.
  const link = page.getByRole("link", { name: /^Download CSV/ });
  const href = await link.getAttribute("href");
  expect(href, "Download CSV links to the report's CSV route").toBeTruthy();
  const exported = await page.request.get(href ?? "");
  expect(exported.ok(), await exported.text()).toBe(true);
  expect(exported.headers()["content-type"]).toMatch(/^text\/csv/);
  const [header, ...lines] = (await exported.text()).trim().split("\r\n");
  expect(header).toBe(
    "day,provisional,incomplete,group,model_pattern,provider_usd,spanlight_usd,explained_usd,residual_usd,variance_ratio,explained_kinds",
  );
  for (const day of [first, second]) {
    expect(lines.some((line) => line.startsWith(`${day},`))).toBe(true);
  }
  const download = page.waitForEvent("download");
  await link.click();
  expect((await download).suggestedFilename()).toMatch(/\.csv$/);
});
