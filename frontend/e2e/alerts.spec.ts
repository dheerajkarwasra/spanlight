import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

// Nothing is delivered: the webhook channel is created but never tested, and the new rule and
// budget have no data to fire on.
test("webhook channel → rule with preview → mute → budget", async ({ page }) => {
  test.setTimeout(240_000);
  const { base } = await createWorkspace(page);

  // 1. A webhook channel; its signing secret is shown once. The old Alerts URL redirects to
  // Settings › Notification channels.
  await page.goto(`${base}/alerts/channels`);
  await expect(page).toHaveURL(`${base}/settings/channels`);
  await expect(page.getByRole("heading", { name: "Notification channels" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No channels" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Add channel" }).first().click();
  const channelDialog = page.getByRole("dialog", { name: "Add channel" });
  await channelDialog.getByRole("radio", { name: "Webhook" }).click();
  await channelDialog.getByLabel("Name").fill("ops-webhook");
  await channelDialog.getByLabel("Endpoint URL").fill("https://example.com/spanlight-e2e");
  await expectNoAxeViolations(page);
  await channelDialog.getByRole("button", { name: "Add channel" }).click();
  const reveal = page.getByRole("dialog", { name: "Channel created" });
  await expect(reveal.getByText("Signing secret", { exact: true }).first()).toBeVisible();
  await expectNoAxeViolations(page);
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("list", { name: /^Webhook/ })).toContainText("ops-webhook");

  // 2. A threshold rule; the preview answers before saving.
  await page.goto(`${base}/alerts`);
  await expect(page.getByRole("heading", { name: "No alert rules" })).toBeVisible();
  await page.getByRole("button", { name: "Create rule" }).first().click();
  const ruleDialog = page.getByRole("dialog", { name: "New alert rule" });
  await ruleDialog.getByLabel("Name").fill("Error spike");
  await expect(ruleDialog.getByText("Enter a threshold to see a preview.")).toBeVisible();
  await ruleDialog.getByLabel(/^Threshold/).fill("5");
  await expect(ruleDialog.getByText("No data in the last 7 days for these filters.")).toBeVisible();
  await ruleDialog.getByRole("combobox", { name: "Channels" }).click();
  await page.getByRole("option", { name: /ops-webhook/ }).click();
  await page.keyboard.press("Escape");
  await expect(ruleDialog).toBeVisible();
  await expectNoAxeViolations(page);
  await ruleDialog.getByRole("button", { name: "Create rule" }).click();
  await expect(ruleDialog).toBeHidden();
  const rules = page.getByRole("list", { name: "Alert rules" });
  await expect(rules).toContainText("Error spike");
  await expectNoAxeViolations(page);

  // 3. Mute it for an hour from its detail page, then unmute.
  await rules.getByRole("link", { name: /Error spike/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Error spike" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Mute", exact: true }).click();
  const muteDialog = page.getByRole("dialog", { name: "Mute Error spike" });
  await muteDialog.getByRole("radio", { name: /1 hour/ }).click();
  await muteDialog.getByRole("button", { name: "Mute rule" }).click();
  await expect(muteDialog).toBeHidden();
  const unmute = page.getByRole("button", { name: "Unmute" });
  await expect(unmute).toBeVisible();
  await unmute.click();
  await expect(page.getByText('Unmuted "Error spike".')).toBeVisible();
  await expect(page.getByRole("button", { name: "Mute", exact: true })).toBeVisible();

  // 4. A monthly project budget: its spend is unknown ("—") until the first evaluation.
  await page.goto(`${base}/budgets`);
  await expect(page.getByRole("heading", { name: "No budgets" })).toBeVisible();
  await page.getByRole("button", { name: "Create budget" }).first().click();
  const budgetDialog = page.getByRole("dialog", { name: "Create budget" });
  await budgetDialog.getByLabel("Name").fill("Monthly cap");
  await budgetDialog.getByLabel("Amount (USD)").fill("50");
  await expectNoAxeViolations(page);
  await budgetDialog.getByRole("button", { name: "Create budget" }).click();
  await expect(budgetDialog).toBeHidden();
  const budgets = page.getByRole("list", { name: "Budgets" });
  await expect(budgets).toContainText("Monthly cap");
  await expect(budgets).toContainText("$50.00");
  await expect(budgets).not.toContainText("$0.00");
  await expectNoAxeViolations(page);
});
