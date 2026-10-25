import type { Page } from "@playwright/test";

import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { checkAtWidths, createWorkspace, expect, sendTrace, test } from "./workspace";

/*
 * UX Phase 6: the Doctor's Sensitivity tab and its change dialog, the evaluator agreement card,
 * and the "Get set up" checklist in the rail, each checked with axe.
 */

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

test("doctor sensitivity: every check is listed, and the dialog previews a change", async ({
  page,
}) => {
  const { base } = await createWorkspace(page);

  // 1. The tab: fifteen checks, the fixed ones say so; tiles on a phone, a table on a laptop.
  await page.goto(`${base}/doctor`);
  await page.getByRole("radio", { name: "Sensitivity" }).click();
  await expect(page).toHaveURL(/[?&]tab=sensitivity/);
  await expect(page.getByRole("heading", { name: "How sensitive each check is" })).toBeVisible();
  await checkAtWidths(page, "doctor-sensitivity", async () => {
    await expect(
      page.getByText("Provider incident", { exact: true }).filter({ visible: true }),
    ).toHaveCount(1);
  });
  const checks = page.getByRole("table", { name: "Sensitivity per check" });
  await expect(checks.getByRole("row", { name: /Provider incident/ })).toContainText("Fixed");
  const errorSpike = checks.getByRole("row", { name: /Error spike/ });
  await expect(errorSpike).toContainText("High recall");

  // 2. The dialog starts at the current setting and previews 7 and 30 days for each setting.
  const change = errorSpike.getByRole("button", { name: "Change sensitivity for Error spike" });
  await change.click();
  const dialog = page.getByRole("dialog", { name: "Sensitivity · Error spike" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("radio", { name: /^High recall/ })).toBeChecked();
  const preview = dialog.getByRole("table", { name: "Findings it would have opened" });
  await expect(preview).toHaveAttribute("aria-busy", "false");
  await expect(preview.getByRole("columnheader", { name: "Last 7 days" })).toBeVisible();
  await expect(preview.getByRole("columnheader", { name: "Last 30 days" })).toBeVisible();
  // A new project has no findings: every count (3 settings x 2 windows) is a real 0.
  await expect(preview.getByRole("cell")).toHaveText(["0", "0", "0", "0", "0", "0"]);

  // 3. A stricter choice explains how open findings resolve; Cancel saves nothing.
  await dialog.getByRole("radio", { name: /^Balanced/ }).click();
  await expect(dialog.getByText(/resolve after 24 hours without a new detection/)).toBeVisible();
  await expectNoAxeViolations(page);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(change).toBeFocused();
  await expect(errorSpike).toContainText("High recall");
});

test("evaluator agreement card on an LLM judge", () => {
  // An LLM judge needs a provider credential and CREDENTIALS_KEYS on the server; the e2e stack
  // seeds neither, and the card shows only on a judge's page. Vitest covers its states.
  test.skip(
    true,
    "The e2e stack has no LLM judge (it needs a provider credential), so the agreement card cannot render.",
  );
});

test("setup checklist: open, expand a step, hide, then restore from the account menu", async ({
  page,
}) => {
  // The full rail, where the tile sits above the account menu.
  await page.setViewportSize({ width: 1440, height: 900 });
  const { base, apiKey } = await createWorkspace(page, { apiKey: true });
  await showChecklist(page);
  // A trace from "your app" (not the setup test trace) does the first of the four steps.
  await sendTrace(page, apiKey ?? "", { name: "checklist call" });

  // 1. The tile opens the popover; a done step says so, an undone one discloses its guide.
  await page.goto(`${base}/traces`);
  const tile = page.getByRole("button", { name: /^Get set up/ });
  await expect(tile).toContainText("1 of 4");
  await tile.click();
  const popover = page.getByRole("dialog", { name: "Get set up" });
  await expect(popover).toContainText("1 of 4 done");
  await expect(
    popover.getByRole("listitem").filter({ hasText: "Send a trace from your app" }),
  ).toContainText("Done");
  const step = popover.getByRole("button", { name: "Group traces into a session" });
  await expect(step).toHaveAttribute("aria-expanded", "false");
  await step.click();
  await expect(step).toHaveAttribute("aria-expanded", "true");
  await expect(popover.getByText(/^Pass a session ID with each call/)).toBeVisible();
  await expect(popover.getByRole("link", { name: "Open Sessions" })).toBeVisible();
  await expectNoAxeViolations(page);

  // 2. Hide: the tile goes and focus moves to the account menu, which can bring it back.
  await popover.getByRole("button", { name: "Hide checklist" }).click();
  await expect(tile).toBeHidden();
  const accountMenu = page.getByRole("button", { name: /Account menu$/ });
  await expect(accountMenu).toBeFocused();
  await accountMenu.click();
  await page.getByRole("menuitem", { name: "Show setup checklist" }).click();
  await expect(tile).toBeVisible();
  await expect(tile).toContainText("1 of 4");
  await expectNoAxeViolations(page);
});

/**
 * Clears the dismissal through the API. It is per user across projects, and every test in this
 * worker shares one account, so a run that stopped after "Hide checklist" would hide it here.
 */
async function showChecklist(page: Page): Promise<void> {
  const status = await page.evaluate(async () => {
    const csrf = /(?:^|; )spl_csrf=([^;]*)/.exec(document.cookie)?.[1] ?? "";
    const response = await fetch("/api/v1/auth/me/activation-checklist", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrf) },
      body: JSON.stringify({ dismissed: false }),
    });
    return response.status;
  });
  expect(status, "the checklist dismissal is cleared").toBe(204);
}
