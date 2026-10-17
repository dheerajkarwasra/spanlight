import type { Page } from "@playwright/test";

import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { checkAtWidths, createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

const SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR;

/** One more width for a check `checkAtWidths` does not cover; leaves the page at 1280 px. */
async function checkAtWidth(page: Page, name: string, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await expectNoAxeViolations(page);
  if (SCREENSHOT_DIR) {
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/${name}-${String(width)}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test("attribution: empty, grouped by metadata, drill-down, promote, export", async ({ page }) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });

  // 1. Empty states on a fresh project: no traces, then no key to group by.
  await page.goto(`${base}/attribution?range=1h`);
  await expect(page.getByRole("heading", { name: "No traces in this window" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.goto(`${base}/attribution?range=1h&group_by=metadata`);
  await expect(page.getByRole("heading", { name: "Pick a metadata key" })).toBeVisible();
  await expectNoAxeViolations(page);

  // 2. Three traces carry `customer_id`: acme twice, globex once, all at the same price.
  for (const name of ["acme-chat-1", "acme-chat-2"]) {
    await sendTrace(page, apiKey ?? "", { name, metadata: { customer_id: "acme" } });
  }
  await sendTrace(page, apiKey ?? "", { name: "globex-chat", metadata: { customer_id: "globex" } });

  // 3. Grouped by the key over the last hour: one row per value, with its share of cost.
  await page.goto(`${base}/attribution?range=1h&group_by=metadata&key=customer_id`);
  const table = page.getByRole("table", { name: "Cost by customer_id" });
  await expect(table).toBeVisible();
  const acme = table.getByRole("row").filter({ has: page.getByRole("link", { name: "acme" }) });
  const globex = table.getByRole("row").filter({ has: page.getByRole("link", { name: "globex" }) });
  await expect(acme).toContainText("66.7%");
  await expect(globex).toContainText("33.3%");
  await expect(table.getByRole("columnheader")).toHaveCount(9);
  await checkAtWidths(page, "attribution", async () => {
    // The table on a laptop, one tile per value on a phone.
    await expect(page.getByLabel("Cost by customer_id").filter({ visible: true })).toHaveCount(1);
  });

  // The phone's sort sheet.
  await page.setViewportSize({ width: 375, height: 900 });
  await page.getByRole("button", { name: /^Sort: / }).click();
  const sheet = page.getByRole("dialog", { name: "Sort by" });
  await expect(sheet).toBeVisible();
  await checkAtWidth(page, "attribution-sort-sheet", 375);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();

  // 4. The export dialog: queued, or the warning when this server has no object storage.
  await page.getByRole("button", { name: "Export" }).click();
  const exportDialog = page.getByRole("dialog", { name: "Export attribution" });
  await expect(exportDialog).toBeVisible();
  await checkAtWidths(page, "attribution-export", async () => {
    await expect(exportDialog.getByRole("button", { name: "Start export" })).toBeVisible();
  });
  await exportDialog.getByRole("button", { name: "Start export" }).click();
  const queued = page.getByRole("dialog", { name: "Export queued" });
  const notConfigured = exportDialog.getByText("Not available on this server");
  await expect(queued.or(notConfigured)).toBeVisible();
  await expectNoAxeViolations(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();

  // 5. A value drills down to the trace list filtered by its pair.
  await acme.getByRole("link", { name: "acme" }).click();
  await expect(page).toHaveURL(/metadata_key=customer_id/);
  await expect(page).toHaveURL(/metadata_value=acme/);
  const drillUrl = page.url();
  await expect(page.getByRole("list", { name: "Active filters" })).toContainText("customer_id");
  await expect(page.getByRole("link", { name: "acme-chat-1" })).toBeVisible();
  await expect(page.getByRole("link", { name: "acme-chat-2" })).toBeVisible();
  await expect(page.getByRole("link", { name: "globex-chat" })).toHaveCount(0);
  await expectNoAxeViolations(page);

  // 6. Promote the key in Settings › Project.
  await page.goto(`${base}/settings/project`);
  await page.getByRole("combobox", { name: "Promoted keys" }).fill("customer_id");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("list", { name: "Promoted keys" })).toContainText("customer_id");
  await checkAtWidths(page, "settings-promoted-keys", async () => {
    await expect(page.getByRole("list", { name: "Promoted keys" })).toBeVisible();
  });

  // 7. A promoted key shows as a chip on the trace list, and in the trace's metadata block.
  await page.goto(drillUrl);
  await expect(page.getByTitle("Show traces where customer_id = acme").first()).toBeVisible();
  await checkAtWidths(page, "traces-metadata-chip", async () => {
    await expect(page.getByText("acme-chat-1").filter({ visible: true }).first()).toBeVisible();
  });
  await page.getByRole("link", { name: "acme-chat-1" }).click();
  const metadata = page.getByRole("region", { name: "Metadata" });
  await expect(metadata).toBeVisible();
  await expect(
    metadata.getByRole("link", { name: "Show traces where customer_id = acme" }),
  ).toBeVisible();
  await checkAtWidths(page, "trace-detail-metadata", async () => {
    await expect(metadata).toBeVisible();
  });

  // 8. With the key promoted, the menu marks it.
  await page.goto(`${base}/attribution?range=1h&group_by=metadata&key=customer_id`);
  await expect(page.getByRole("combobox", { name: "Metadata key" })).toContainText("promoted");
  await expectNoAxeViolations(page);
});

test("budgets: a metadata block budget with the soft-limit hint", async ({ page }) => {
  test.setTimeout(240_000);
  const { base } = await createWorkspace(page);

  await page.goto(`${base}/budgets`);
  await expect(page.getByRole("heading", { name: "No budgets" })).toBeVisible();
  await page.getByRole("button", { name: "Create budget" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Create budget" });
  await expect(dialog.getByRole("radiogroup", { name: "Scope" }).getByRole("radio")).toHaveCount(6);
  await dialog.getByLabel("Name").fill("Acme daily cap");
  await dialog.getByRole("radio", { name: "Metadata" }).click();
  await dialog.getByLabel("Key").fill("customer_id");
  await dialog.getByLabel("Value").fill("acme");
  await expect(dialog.getByRole("group", { name: "Metadata scope" })).toContainText(
    "customer_id = acme",
  );
  await dialog.getByRole("radio", { name: "Daily" }).click();
  await dialog.getByLabel("Amount (USD)").fill("5");
  await dialog.getByRole("radio", { name: "Block" }).click();
  // Spec §8.4: blocking is a soft limit, and the hint says how soft.
  await expect(dialog).toContainText(
    "Soft limit. Gateway calls in this scope get 402 BUDGET_EXCEEDED after spend is measured above the amount.",
  );
  await expect(dialog).toContainText("Spend is measured every 60 seconds");
  await expect(dialog).toContainText("Traces sent with the SDK are never blocked.");
  await checkAtWidths(page, "budget-dialog-metadata", async () => {
    await expect(dialog.getByRole("radio", { name: "Metadata" })).toBeVisible();
  });
  await checkAtWidth(page, "budget-dialog-metadata", 640);

  await dialog.getByRole("button", { name: "Create budget" }).click();
  await expect(dialog).toBeHidden();
  const budgets = page.getByRole("list", { name: "Budgets" });
  await expect(budgets).toContainText("Acme daily cap");
  await expect(budgets).toContainText("$5.00");
  await expectNoAxeViolations(page);
});
