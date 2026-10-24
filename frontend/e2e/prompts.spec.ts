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
  // Each version row carries its last 7 days of usage; nothing has called this prompt yet.
  await expect(versions.getByText("No calls in 7 days")).toHaveCount(2);

  // 4. "Create experiment" opens the new-experiment form with the shown version picked.
  await page.getByRole("link", { name: "Create experiment", exact: true }).click();
  await expect(page).toHaveURL(/\/experiments\/new\?prompt=support-answer&version=/);
  await expect(page.getByRole("combobox", { name: "Prompt" })).toHaveText("support-answer");
  await expect(page.getByRole("combobox", { name: "Version" })).toHaveText("v1");
  await expectNoAxeViolations(page);

  // 5. The playground cannot run without a provider credential, and says so before any call.
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
  // The run button counts the calls once there is more than one column.
  await page.getByRole("button", { name: "Add column" }).click();
  await expect(page.getByRole("button", { name: "Run 2 calls" })).toBeVisible();
  await expect(page.getByRole("link", { name: "View playground runs" })).toHaveAttribute(
    "href",
    /\/traces\?tag=spanlight(%3A|:)playground$/,
  );
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoAxeViolations(page);

  // 6. On a phone the prompt is read-only: "Move label" instead of "New version".
  await page.goto(`${base}/prompts/support-answer`);
  await expect(page.getByRole("button", { name: "Move label", exact: true })).toBeVisible();
  await expect(page.getByText("Edit and test versions on a larger screen.")).toBeVisible();
  await expect(page.getByRole("link", { name: "New version" })).toHaveCount(0);
  await expectNoAxeViolations(page);
  await page.goto(`${base}/prompts`);
  await expect(page.getByRole("link", { name: "support-answer" }).first()).toBeVisible();
  await expectNoAxeViolations(page);
});
