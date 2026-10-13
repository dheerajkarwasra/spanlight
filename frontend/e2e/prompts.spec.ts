import { e2eEnabled, expectNoAxeViolations, SKIP_REASON } from "./fixtures";
import { createWorkspace, expect, test } from "./workspace";

test.skip(!e2eEnabled, SKIP_REASON);
// The shared stack runs beside other suites: allow for a slow browser start and first loads.
test.describe.configure({ timeout: 240_000 });

test("prompt v1 → v2 → label → playground explains the missing credential", async ({ page }) => {
  test.setTimeout(240_000);
  const { base } = await createWorkspace(page);

  // 1. Version 1 of a text prompt.
  await page.goto(`${base}/prompts`);
  await expect(page.getByRole("heading", { name: "No prompts yet" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "Create prompt" }).click();
  await page.getByLabel("Name").fill("support-answer");
  await page.getByLabel("Template").fill("Answer {{question}} politely.");
  await expect(page.getByRole("list", { name: "Variables" })).toContainText("question");
  await page.getByLabel("Commit message").fill("first draft");
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Save version" }).click();
  await expect(page).toHaveURL(/\/prompts\/support-answer/);
  await expect(page.getByRole("list", { name: "Versions" })).toContainText("v1");

  // 2. Version 2 adds a variable; version 1 stays as it was.
  await page.getByRole("link", { name: "New version" }).click();
  await page.getByLabel("Template").fill("Answer {{question}} for {{customer}}.");
  await page.getByLabel("Commit message").fill("address the customer");
  await page.getByRole("button", { name: "Save version" }).click();
  const versions = page.getByRole("list", { name: "Versions" });
  await expect(versions.getByRole("listitem")).toHaveCount(2);
  await expect(versions).toContainText("address the customer");
  await expect(versions).toContainText("first draft");
  await page.getByRole("button", { name: "Show v1" }).click();
  await expect(page.getByText("Answer {{question}} politely.")).toBeVisible();
  await expectNoAxeViolations(page);

  // 3. A label on version 2.
  await page.getByRole("button", { name: "Set label on v2" }).click();
  const labelDialog = page.getByRole("dialog", { name: "Set label" });
  await labelDialog.getByLabel("Label").fill("production");
  await expectNoAxeViolations(page);
  await labelDialog.getByRole("button", { name: "Save label" }).click();
  await expect(labelDialog).toBeHidden();
  await expect(page.getByText("production", { exact: true }).first()).toBeVisible();

  // 4. The playground cannot run without a provider credential, and says so before any call.
  await page.goto(`${base}/playground`);
  await expect(
    page.getByText("This organization has no provider credentials yet.", { exact: false }),
  ).toBeVisible();
  await expectNoAxeViolations(page);
  const column = page.getByRole("group", { name: "Run A" });
  await column.getByRole("radio", { name: "Messages" }).click();
  await column.getByLabel("Text of message 1").fill("Hello");
  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Run A: Choose a credential.");
  await expectNoAxeViolations(page);
});
