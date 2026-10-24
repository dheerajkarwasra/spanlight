import {
  DB_SKIP_REASON,
  dbEnabled,
  insertProviderIncidentInsight,
  insertResolvedFiring,
  markAsGatewayTraffic,
  projectIdOf,
} from "./db-seed";
import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { checkAtWidths, createWorkspace, expect, sendTrace, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

const UNPRICED_MODEL = "e2e-unpriced-model";

test("cost hub: four tabs, Spend → prefilled budget with its burn, Pricing overrides", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  for (const name of ["acme-chat-1", "acme-chat-2"]) {
    await sendTrace(page, apiKey ?? "", { name, metadata: { customer_id: "acme" } });
  }
  await sendTrace(page, apiKey ?? "", { name: "unpriced-chat", model: UNPRICED_MODEL });

  // 1. The Cost hub's four tabs, each at its own URL.
  await page.goto(`${base}/attribution?range=1h`);
  const tabs = page.getByRole("navigation", { name: "Cost sections" });
  for (const [label, path] of [
    ["Budgets", "budgets"],
    ["Reconciliation", "reconciliation"],
    ["Pricing", "pricing"],
    ["Spend", "attribution"],
  ] as const) {
    await tabs.getByRole("link", { name: label }).click();
    await expect(page).toHaveURL(new RegExp(`${base}/${path}(\\?|$)`));
    await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
    await expect(tabs.getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  }
  await page.goto(`${base}/reconciliation`);
  await checkAtWidths(page, "cost-reconciliation", async () => {
    await expect(
      page.getByRole("heading", { name: "No billing source covers this project." }),
    ).toBeVisible();
  });

  // 2. Spend grouped by a metadata key: the chart, then "Set a budget for this" on a row.
  await page.goto(`${base}/attribution?range=1h&group_by=metadata&key=customer_id`);
  const chart = page.getByRole("region", { name: "Spend over time" });
  const table = page.getByRole("table", { name: "Cost by customer_id" });
  await checkAtWidths(page, "cost-spend", async () => {
    await expect(chart).toBeVisible();
    await expect(page.getByLabel("Cost by customer_id").filter({ visible: true })).toHaveCount(1);
  });
  await table.getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Show over time" })).toBeVisible();
  // The row menu is non-modal, so the page behind it is not aria-hidden while it is open.
  await expectNoAxeViolations(page);
  await page.getByRole("menuitem", { name: "Set a budget for this" }).click();
  await expect(page).toHaveURL(/\/budgets\?/);
  const dialog = page.getByRole("dialog", { name: "Create budget" });
  await expect(dialog.getByRole("radio", { name: "Metadata" })).toBeChecked();
  await expect(dialog.getByLabel("Key")).toHaveValue("customer_id");
  await expect(dialog.getByLabel("Value")).toHaveValue("acme");
  await checkAtWidths(page, "cost-budget-prefilled", async () => {
    await expect(dialog.getByLabel("Value")).toBeVisible();
  });
  await dialog.getByLabel("Name").fill("Acme monthly cap");
  await dialog.getByLabel("Amount (USD)").fill("5");
  await dialog.getByRole("button", { name: "Create budget" }).click();
  await expect(dialog).toBeHidden();
  // Closing the dialog drops the prefill from the URL.
  await expect(page).not.toHaveURL(/new=1/);

  // 3. The budget's burn disclosure.
  const budgets = page.getByRole("list", { name: "Budgets" });
  await expect(budgets).toContainText("Acme monthly cap");
  const showBurn = budgets.getByRole("button", { name: "Show burn" });
  await showBurn.click();
  const hideBurn = budgets.getByRole("button", { name: "Hide burn" });
  await expect(hideBurn).toHaveAttribute("aria-expanded", "true");
  await expect(budgets.getByText(/^Cumulative spend/)).toBeVisible();
  await checkAtWidths(page, "cost-budget-burn", async () => {
    await expect(hideBurn).toBeVisible();
  });

  // 4. Pricing: the unpriced model's "Add price" opens the dialog prefilled; add, then delete.
  await page.goto(`${base}/pricing`);
  const unpriced = page.getByRole("table", { name: "Unpriced models" });
  await unpriced.getByRole("button", { name: `Add price for ${UNPRICED_MODEL}` }).click();
  const override = page.getByRole("dialog", { name: "Add price override" });
  await expect(override.getByLabel("Provider")).toHaveValue("openai");
  await expect(override.getByLabel("Model pattern")).toHaveValue(UNPRICED_MODEL);
  await override.getByLabel("Input per 1M", { exact: true }).fill("1");
  await override.getByLabel("Output per 1M", { exact: true }).fill("2");
  await checkAtWidths(page, "cost-pricing-override", async () => {
    await expect(override.getByLabel("Model pattern")).toBeVisible();
  });
  await override.getByRole("button", { name: "Add override" }).click();
  await expect(override).toBeHidden();
  const overrides = page.getByRole("list", { name: "Organization overrides" });
  await expect(overrides).toContainText(UNPRICED_MODEL);
  await checkAtWidths(page, "cost-pricing", async () => {
    await expect(overrides).toBeVisible();
  });
  await overrides.getByRole("button", { name: `Delete override for ${UNPRICED_MODEL}` }).click();
  const confirm = page.getByRole("alertdialog");
  await expect(confirm).toContainText("Stored costs are not recomputed");
  await confirm.getByRole("button", { name: "Delete override" }).click();
  await expect(page.getByRole("heading", { name: "No overrides yet." })).toBeVisible();

  // 5. A link with `?provider&model` opens the dialog prefilled; closing it clears the params.
  await page.goto(`${base}/pricing?provider=anthropic&model=claude-e2e`);
  await expect(override.getByLabel("Model pattern")).toHaveValue("claude-e2e");
  await page.keyboard.press("Escape");
  await expect(override).toBeHidden();
  await expect(page).not.toHaveURL(/model=/);
});

test("settings › project: the Data card and the self-saving section", async ({ page }) => {
  test.setTimeout(180_000);
  const { base } = await createWorkspace(page);
  await page.goto(`${base}/settings/project`);
  const data = page.getByRole("region", { name: "Data" });
  await expect(data).toContainText("days are deleted every hour.");
  await expect(data.getByText("Capture prompts & completions")).toBeVisible();
  await checkAtWidths(page, "settings-project", async () => {
    await expect(
      page.getByRole("heading", { level: 2, name: "Saved as you change them" }),
    ).toBeVisible();
  });
});

test("monitor and gateway: Doctor example trace, firing history, gateway ribbon", async ({
  page,
}) => {
  test.skip(!dbEnabled, DB_SKIP_REASON);
  test.setTimeout(240_000);
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  const projectId = projectIdOf(base);
  const traceId = await sendTrace(page, apiKey ?? "", {
    name: "overloaded call",
    provider: "anthropic",
    status: "error",
    statusMessage: "Overloaded",
    attributes: { "http.status_code": 529, "provider.error.type": "overloaded_error" },
  });

  // 1. A Doctor finding: its track record, and an example trace that opens on its span.
  const insightId = insertProviderIncidentInsight(projectId, [traceId], "E2E elevated errors");
  await page.goto(`${base}/doctor/${insightId}`);
  await expect(page.getByRole("region", { name: "Track record" })).toBeVisible();
  const example = page
    .getByRole("list", { name: "Example traces" })
    .getByRole("link", { name: new RegExp(`^Open trace ${traceId} on span overloaded call`) });
  await checkAtWidths(page, "doctor-finding", async () => {
    await expect(example).toBeVisible();
  });
  await example.click();
  await expect(page).toHaveURL(new RegExp(`/traces/${traceId}\\?.*span=[0-9a-f]{16}`));

  // 2. Alerts: a resolved firing, grouped by its rule, leads to the traces it measured.
  const ruleName = insertResolvedFiring(projectId);
  await page.goto(`${base}/alerts`);
  const firings = page.getByRole("list", { name: "Firings by rule" });
  const group = firings.getByRole("button", { name: new RegExp(ruleName) });
  await expect(group).toContainText("Fired 1 time");
  await group.click();
  await expect(group).toHaveAttribute("aria-expanded", "true");
  const viewTraces = firings.getByRole("link", { name: /^View traces/ });
  await checkAtWidths(page, "alerts-firing-history", async () => {
    await expect(viewTraces).toBeVisible();
  });
  await viewTraces.click();
  await expect(page).toHaveURL(/\/traces\?/);
  await expect(page).toHaveURL(/model=e2e-alert-model/);
  await expect(page).toHaveURL(/status=error/);
  await expect(page).toHaveURL(/range=custom/);

  // 3. Gateway overview: the four-card ribbon and requests over time.
  markAsGatewayTraffic(projectId, traceId);
  await page.goto(`${base}/gateway`);
  const ribbon = page.getByRole("list", { name: "Gateway metrics" });
  await expect(ribbon.getByRole("listitem")).toHaveCount(4);
  await checkAtWidths(page, "gateway-overview", async () => {
    await expect(ribbon).toBeVisible();
    await expect(page.getByRole("region", { name: "Requests over time" })).toBeVisible();
  });
});
